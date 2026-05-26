from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Tuple

import aiohttp
import discord
from redbot.core import commands

from .aggregator import merge_events
from .config import get_config, register_config
from .models import Event, Place, Source
from .places import PlaceResolver, RegionNotFoundError
from .providers import PROVIDERS, TicketmasterProvider
from .regions import DEFAULT_REGION
from .render import render_places_listing

log = logging.getLogger("red.giglist")


def parse_command_args(text: str) -> Tuple[str, int, int]:
    """Return (placename, limit, days) parsed from command input.

    Supports `--days N` / `--days=N` / `--limit N` / `--limit=N` mixed into
    a free-form place name."""
    limit = 0
    days = 0

    m = re.search(r"(?:^|\s)--limit(?:=|\s+)(\d+)", text, re.IGNORECASE)
    if m:
        limit = int(m.group(1))
    m = re.search(r"(?:^|\s)--days(?:=|\s+)(\d+)", text, re.IGNORECASE)
    if m:
        days = int(m.group(1))

    placename = re.sub(r"(?:^|\s)--limit(?:=|\s+)\d+", "", text, flags=re.IGNORECASE)
    placename = re.sub(r"(?:^|\s)--days(?:=|\s+)\d+", "", placename, flags=re.IGNORECASE)
    placename = re.sub(r"\s+", " ", placename).strip()
    return placename, limit, days


async def gather_events_for_place(
    place: Place,
    *,
    get_provider: Callable[[str], Awaitable[object]],
    source_enabled: Callable[[str], Awaitable[bool]],
    days: int,
    limit: int,
    now: datetime,
) -> tuple[list[Event], list[str], dict]:
    """Fetch each source on `place` and merge the results.

    Per-source failures (disabled flag, missing provider, error result, or
    an unexpected exception in `fetch`) are captured into `diag` and the
    loop continues to the next source — so one bad source can never kill
    the rest of the aggregation. Extracted from the Giglist cog so the
    orchestration is testable without instantiating the cog class."""
    all_events: list[Event] = []
    warnings: list[str] = []
    diag: dict = {}

    for idx, source in enumerate(place.sources):
        entry = {
            "kind": source.kind,
            "label": source.label,
            "events": 0,
            "error": None,
            "warnings": [],
            "cache_age_s": None,
        }
        if not await source_enabled(source.kind):
            entry["error"] = f"{source.kind} provider is disabled."
            warnings.append(entry["error"])
            diag[idx] = entry
            continue

        prov = await get_provider(source.kind)
        if prov is None:
            entry["error"] = f"No provider for source kind '{source.kind}'."
            warnings.append(entry["error"])
            diag[idx] = entry
            continue

        try:
            result = await prov.fetch(source, days=days, limit=limit)
        except Exception as exc:
            # A provider raising (timeout, library bug, malformed spec) must
            # never abort the loop — the other sources must still be tried.
            log.exception(
                "%s.fetch raised for source=%r", source.kind, source.spec
            )
            entry["error"] = f"{source.kind} crashed: {exc}"
            warnings.append(entry["error"])
            diag[idx] = entry
            continue

        entry["events"] = len(result.events)
        entry["warnings"] = list(result.warnings)
        entry["error"] = result.error
        entry["cache_age_s"] = (
            prov.cache_age_seconds(source)
            if hasattr(prov, "cache_age_seconds")
            else None
        )
        if result.error:
            warnings.append(f"{prov.name}: {result.error}")
        warnings.extend(result.warnings)
        all_events.extend(result.events)
        diag[idx] = entry

    return merge_events(all_events, limit, now=now), warnings, diag


def _spec_from_args(kind: str, args: str) -> Optional[dict]:
    """Parse a free-form spec argument for `gigliststest` per source kind."""
    args = args.strip()
    if kind == "tasguide":
        return {"path": args} if args else {"path": "category/music"}
    if kind == "humanitix":
        # 'au--tas--hobart/music' or 'au--tas--hobart'
        if not args:
            return None
        if "/" in args:
            slug, _, category = args.partition("/")
            return {"slug": slug, "category": category}
        return {"slug": args}
    if kind == "ticketmaster":
        # 'dma:707' or 'city:Hobart' or 'keyword:...'
        if not args:
            return None
        if ":" in args:
            k, _, v = args.partition(":")
            mapping = {
                "dma": "dmaId",
                "dmaid": "dmaId",
                "city": "city",
                "keyword": "keyword",
                "geopoint": "geoPoint",
            }
            real = mapping.get(k.strip().lower())
            if not real:
                return None
            return {real: v.strip()}
        return None
    return None


class Giglist(commands.Cog):
    """List upcoming live music gigs for a place.

    Aggregates Tasguide (Tasmanian gig guide, primary), Humanitix (community
    ticketing), and Ticketmaster Discovery API (touring shows). The
    Ticketmaster source is inert until an API key is configured.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = get_config(self)
        self._resolver: Optional[PlaceResolver] = None
        self._resolver_region: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._providers: dict = {}
        self._tm_provider: Optional[TicketmasterProvider] = None
        self._tm_signature: tuple = ("", "", 0)
        self._last_diag: dict = {}

    async def cog_load(self) -> None:
        register_config(self.config)
        self._session = aiohttp.ClientSession()
        await self._ensure_resolver()

    async def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- resolver + providers ---------------------------------------------

    async def _ensure_resolver(self) -> PlaceResolver:
        region = await self.config.active_region() or DEFAULT_REGION
        if self._resolver is None or self._resolver_region != region:
            self._resolver = PlaceResolver(region)
            self._resolver_region = region
        return self._resolver

    async def _get_provider(self, kind: str):
        """Lazily build a provider for `kind`. The Ticketmaster provider is
        rebuilt when the API key, country, or cache TTL config changes."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        ttl = await self.config.cache_ttl_seconds() or 600

        if kind == "ticketmaster":
            api_key = await self.config.ticketmaster_api_key() or ""
            country = await self.config.ticketmaster_country() or "AU"
            signature = (api_key, country, ttl)
            if self._tm_provider is None or self._tm_signature != signature:
                self._tm_provider = TicketmasterProvider(
                    self._session,
                    api_key=api_key,
                    country=country,
                    cache_ttl_seconds=ttl,
                )
                self._tm_signature = signature
            return self._tm_provider

        cls = PROVIDERS.get(kind)
        if cls is None:
            return None
        if kind not in self._providers:
            self._providers[kind] = cls(self._session, cache_ttl_seconds=ttl)
        return self._providers[kind]

    async def _source_enabled(self, kind: str) -> bool:
        if kind == "tasguide":
            return bool(await self.config.enable_tasguide())
        if kind == "humanitix":
            return bool(await self.config.enable_humanitix())
        if kind == "ticketmaster":
            return bool(await self.config.enable_ticketmaster())
        return True

    # ---- fetch flow --------------------------------------------------------

    async def _fetch_for_place(
        self,
        place: Place,
        *,
        days: int,
        limit: int,
        now: Optional[datetime] = None,
    ) -> tuple[list[Event], list[str], dict]:
        if now is None:
            now = datetime.now(timezone.utc)
        return await gather_events_for_place(
            place,
            get_provider=self._get_provider,
            source_enabled=self._source_enabled,
            days=days,
            limit=limit,
            now=now,
        )

    # ---- commands ----------------------------------------------------------

    @commands.command(name="giglist")
    async def giglist(self, ctx: commands.Context, *, query: str) -> None:
        """List upcoming gigs for a place.

        Examples:
        `[p]giglist hobart`
        `[p]giglist tasmania --days 14 --limit 20`

        Flags: `--days N` (1-90, default 30), `--limit N` (1-30, default 10).

        See available places with `[p]giglistplaces`. Per-source diagnostics
        live under `[p]giglistdiag <place>`.
        """
        placename, limit_override, days_override = parse_command_args(query)
        if not placename:
            await ctx.send("Please provide a place name, e.g. `[p]giglist hobart`.")
            return

        try:
            resolver = await self._ensure_resolver()
        except RegionNotFoundError as exc:
            await ctx.send(str(exc))
            return

        place = resolver.resolve(placename)
        if place is None:
            known = ", ".join(resolver.known_places()) or "(none configured)"
            await ctx.send(
                f"I don't recognise **{placename}**.\nTry one of: {known}"
            )
            return

        limit = limit_override or await self.config.default_limit() or 10
        days = days_override or await self.config.default_days() or 30
        limit = max(1, min(limit, 30))
        days = max(1, min(days, 90))

        now = datetime.now(timezone.utc)
        async with ctx.typing():
            events, warnings, diag = await self._fetch_for_place(
                place, days=days, limit=limit, now=now
            )
        self._last_diag[(ctx.guild.id if ctx.guild else 0, place.key)] = diag

        log.info(
            "giglist region=%s place=%s sources=%d events=%d warnings=%d",
            self._resolver_region,
            place.key,
            len(place.sources),
            len(events),
            len(warnings),
        )
        source_counts = dict(Counter(e.source for e in events))
        ages = [d["cache_age_s"] for d in diag.values() if d["cache_age_s"] is not None]
        cache_age_s = min(ages) if ages else None
        payload = render_places_listing(
            place,
            events,
            warnings,
            days=days,
            source_counts=source_counts,
            cache_age_s=cache_age_s,
            now=now,
        )
        await ctx.send(embed=discord.Embed(**payload))

    @commands.command(name="giglistplaces")
    async def giglistplaces(self, ctx: commands.Context) -> None:
        """List all places resolvable in the active region."""
        try:
            resolver = await self._ensure_resolver()
        except RegionNotFoundError as exc:
            await ctx.send(str(exc))
            return
        known = resolver.known_places()
        if not known:
            await ctx.send("No places configured.")
            return
        lines = ["**Gig places:**"] + [f"• {name}" for name in known]
        await ctx.send("\n".join(lines)[:1900])

    @commands.command(name="giglistregions")
    async def giglistregions(self, ctx: commands.Context) -> None:
        """List available region packs and the active region."""
        active = await self.config.active_region() or DEFAULT_REGION
        regions = PlaceResolver.available_regions()
        lines = [f"**Active region:** `{active}`", "", "**Available regions:**"]
        for region in regions:
            marker = " (active)" if region == active else ""
            lines.append(f"• `{region}`{marker}")
        await ctx.send("\n".join(lines))

    @commands.command(name="giglistdiag")
    async def giglistdiag(self, ctx: commands.Context, *, placename: str) -> None:
        """Per-source breakdown of the last `[p]giglist` fetch for a place."""
        try:
            resolver = await self._ensure_resolver()
        except RegionNotFoundError as exc:
            await ctx.send(str(exc))
            return
        place = resolver.resolve(placename)
        if place is None:
            await ctx.send(f"I don't recognise **{placename}**.")
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        diag = self._last_diag.get((guild_id, place.key))
        if diag is None:
            await ctx.send(
                f"No recent fetch for **{place.display_name}**. "
                f"Run `[p]giglist {place.key}` first."
            )
            return

        lines = [f"**Diag for {place.display_name}**", ""]
        for idx, entry in diag.items():
            src = place.sources[idx] if idx < len(place.sources) else None
            spec_str = ""
            if src:
                spec_str = ": " + ", ".join(f"{k}={v}" for k, v in src.spec.items())
            age = entry["cache_age_s"]
            age_str = "fresh" if age == 0 else (f"cached {age}s" if age else "no cache")
            label = f" ({entry['label']})" if entry["label"] else ""
            lines.append(f"`{idx}` {entry['kind']}{label}{spec_str}")
            lines.append(
                f"   events={entry['events']}, {age_str}"
                + (f", error={entry['error']}" if entry["error"] else "")
            )
            for w in entry["warnings"]:
                lines.append(f"   ⚠ {w}")
        await ctx.send("\n".join(lines)[:1900])

    @commands.command(name="gigliststest")
    @commands.is_owner()
    async def gigliststest(
        self, ctx: commands.Context, kind: str, *, spec_args: str = ""
    ) -> None:
        """Test-fetch any source without persisting (bot owner only).

        Examples:
        `[p]gigliststest tasguide category/music`
        `[p]gigliststest humanitix au--tas--hobart/music`
        `[p]gigliststest ticketmaster dma:707`
        """
        kind = kind.strip().lower()
        if kind not in PROVIDERS:
            await ctx.send(
                f"Unknown source kind `{kind}`. Known: {', '.join(sorted(PROVIDERS))}."
            )
            return
        spec = _spec_from_args(kind, spec_args.strip())
        if spec is None:
            await ctx.send(f"Couldn't parse spec for `{kind}`.")
            return

        days = await self.config.default_days() or 30
        place = Place(
            key=f"_test_{kind}",
            display_name=f"{kind} test",
            sources=(Source(kind=kind, spec=spec),),
        )
        async with ctx.typing():
            events, warnings, _ = await self._fetch_for_place(
                place, days=days, limit=10
            )
        embed = discord.Embed(
            title=f"{kind} test",
            description=(
                f"spec={json.dumps(spec)}\n"
                f"Found {len(events)} event(s) in the next {days} days."
            ),
            color=discord.Color.blue(),
        )
        if events:
            preview = "\n".join(f"• {event.title}" for event in events[:5])
            embed.add_field(name="Sample events", value=preview[:1024], inline=False)
        if warnings:
            embed.add_field(
                name="Warnings", value="\n".join(warnings)[:1024], inline=False
            )
        await ctx.send(embed=embed)

    @commands.group(name="giglistset")
    @commands.is_owner()
    async def giglistset(self, ctx: commands.Context) -> None:
        """Configure giglist (bot owner only)."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @giglistset.command(name="ticketmaster_key")
    async def giglistset_tm_key(
        self, ctx: commands.Context, *, key: str = ""
    ) -> None:
        """Set (or clear with empty arg) the Ticketmaster Discovery API key.

        Get a free key at https://developer.ticketmaster.com — 5000 calls/day."""
        await self.config.ticketmaster_api_key.set(key.strip())
        # Force provider rebuild on the next fetch
        self._tm_provider = None
        if key.strip():
            await ctx.send("✅ Ticketmaster API key set.")
        else:
            await ctx.send("Ticketmaster API key cleared. Source now inert.")
