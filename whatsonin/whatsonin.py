from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Optional, Tuple

import aiohttp
import discord
from redbot.core import commands

from .aggregator import merge_events
from .config import get_config, register_config
from .models import Event, Place, Source
from .places import PlaceResolver, RegionNotFoundError
from .providers import PROVIDERS, EventbriteProvider, IcsProvider, ManualProvider
from .regions import DEFAULT_REGION
from .render import render_places_listing
from .stores import GuildStore, PackStore, PlaceStore

log = logging.getLogger("red.whatsonin")


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_description(text: Optional[str]) -> str:
    """Strip HTML tags and collapse whitespace. JSON-LD descriptions can
    contain raw HTML that Discord would render as literal text."""
    if not text:
        return ""
    text = _HTML_TAG_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _cache_age_for(provider, source: Source) -> Optional[int]:
    """Ask the provider for the age of its cache entry for this source, if any."""
    if not hasattr(provider, "cache_age_seconds"):
        return None
    if source.kind == "eventbrite":
        return provider.cache_age_seconds(source.spec.get("slug", ""))
    if source.kind in ("ics", "rss"):
        return provider.cache_age_seconds(source.spec.get("url", ""))
    return None


def _spec_from_args(kind: str, args: str) -> Optional[dict]:
    """Parse the user-supplied spec string for a given source kind into a
    spec dict. Returns None if the args don't fit the kind."""
    args = args.strip()
    if kind == "eventbrite":
        if not args or " " in args:
            return None
        return {"slug": args}
    if kind in ("ics", "rss"):
        if not args.startswith("http"):
            return None
        return {"url": args}
    if kind == "manual":
        # whatsonintest doesn't pre-populate manual events
        return {"events": []}
    return None


def parse_command_args(text: str) -> Tuple[str, int, int]:
    """Return placename, limit, days parsed from command input."""
    limit = 0
    days = 0

    limit_match = re.search(r"(?:^|\s)--limit(?:=|\s+)(\d+)", text, re.IGNORECASE)
    if limit_match:
        limit = int(limit_match.group(1))

    days_match = re.search(r"(?:^|\s)--days(?:=|\s+)(\d+)", text, re.IGNORECASE)
    if days_match:
        days = int(days_match.group(1))

    placename = re.sub(r"(?:^|\s)--limit(?:=|\s+)\d+", "", text, flags=re.IGNORECASE)
    placename = re.sub(r"(?:^|\s)--days(?:=|\s+)\d+", "", placename, flags=re.IGNORECASE)
    placename = re.sub(r"\s+", " ", placename).strip()
    return placename, limit, days


class Whatsonin(commands.Cog):
    """List upcoming Eventbrite events for configured places in a region pack."""

    def __init__(self, bot):
        self.bot = bot
        self.config = get_config(self)
        self._resolver: Optional[PlaceResolver] = None
        self._resolver_region: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._provider: Optional[EventbriteProvider] = None
        self._providers: dict = {}
        self._last_diag: dict = {}

    async def cog_load(self) -> None:
        register_config(self.config)
        self._session = aiohttp.ClientSession()
        await self._ensure_resolver()

    async def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _ensure_resolver(self) -> PlaceResolver:
        region = await self.config.active_region()
        if region is None:
            region = DEFAULT_REGION
        if self._resolver is None or self._resolver_region != region:
            self._resolver = PlaceResolver(region)
            self._resolver_region = region
        return self._resolver

    async def _ensure_store(self, guild) -> PlaceStore:
        """Build a PlaceStore composing this guild's GuildStore over the
        active region's PackStore. Recomputed per command (cheap)."""
        await self._ensure_resolver()
        region = self._resolver_region or DEFAULT_REGION
        try:
            pack = PackStore(region)
        except RegionNotFoundError:
            pack = None
        guild_store = GuildStore(self.config, guild) if guild is not None else None
        return PlaceStore(guild=guild_store, pack=pack)

    async def _get_provider(self, kind: str):
        """Return a provider instance for the given source kind, creating one
        on demand. Eventbrite is recreated when locale/TTL config changes."""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        if kind == "eventbrite":
            locale = await self.config.eventbrite_locale()
            cache_ttl = await self.config.cache_ttl_seconds()
            if (
                self._provider is None
                or self._provider._locale != locale
                or self._provider._cache_ttl != cache_ttl
            ):
                self._provider = EventbriteProvider(
                    self._session,
                    locale=locale or "en-AU,en;q=0.9",
                    cache_ttl_seconds=cache_ttl or 600,
                )
            return self._provider

        cls = PROVIDERS.get(kind)
        if cls is None:
            return None
        if kind not in self._providers:
            cache_ttl = await self.config.cache_ttl_seconds() or 600
            if kind == "manual":
                self._providers[kind] = cls()
            else:
                self._providers[kind] = cls(
                    self._session, cache_ttl_seconds=cache_ttl
                )
        return self._providers[kind]

    async def _fetch_for_place(
        self,
        place: Place,
        *,
        days: int,
        limit: int,
        store: Optional[PlaceStore] = None,
    ) -> tuple[list[Event], list[str], dict]:
        """Returns (events, warnings, diag).

        diag is keyed by source-index and contains per-source: kind, label,
        event_count, error, warnings, cache_age_s. Used by the diag command
        and to compute the minimum cache age shown in the embed footer."""
        enable_eventbrite = await self.config.enable_eventbrite()

        all_events: list[Event] = []
        warnings: list[str] = []
        diag: dict = {}

        if store is not None:
            parent = await store.aggregated_parent(place)
            if parent:
                warnings.append(
                    f"Eventbrite has no {place.display_name} directory; "
                    f"showing {parent}-wide events."
                )

        for idx, source in enumerate(place.sources):
            entry = {
                "kind": source.kind,
                "label": source.label,
                "events": 0,
                "error": None,
                "warnings": [],
                "cache_age_s": None,
            }
            if source.kind == "eventbrite" and not enable_eventbrite:
                entry["error"] = "Eventbrite provider is disabled."
                warnings.append(entry["error"])
                diag[idx] = entry
                continue
            prov = await self._get_provider(source.kind)
            if prov is None:
                entry["error"] = f"No provider for source kind '{source.kind}'."
                warnings.append(entry["error"])
                diag[idx] = entry
                continue
            result = await prov.fetch(source, days=days, limit=limit)
            entry["events"] = len(result.events)
            entry["warnings"] = list(result.warnings)
            entry["error"] = result.error
            entry["cache_age_s"] = _cache_age_for(prov, source)
            if result.error:
                warnings.append(f"{prov.name}: {result.error}")
            warnings.extend(result.warnings)
            all_events.extend(result.events)
            diag[idx] = entry

        return merge_events(all_events, limit), warnings, diag

    @commands.command(name="whatsonin")
    async def whatsonin(self, ctx: commands.Context, *, query: str) -> None:
        """List upcoming events for a place in the active region.

        Examples:
        `[p]whatsonin hobart`
        `[p]whatsonin launceston --days 14 --limit 5`

        Set the region with `[p]set whatsonin active_region tasmania` or `sydney`.
        """
        placename, limit_override, days_override = parse_command_args(query)
        if not placename:
            await ctx.send("Please provide a place name, e.g. `[p]whatsonin hobart`.")
            return

        try:
            store = await self._ensure_store(ctx.guild)
        except RegionNotFoundError as exc:
            await ctx.send(str(exc))
            return

        place = await store.resolve(placename)
        if place is None:
            known = ", ".join(name for name, _ in await store.known_places())
            await ctx.send(
                f"I don't recognise **{placename}**.\nTry one of: {known or '(no places configured)'}\n"
                f"Or add one with `[p]whatsonin add <key>`."
            )
            return

        limit = limit_override or await self.config.default_limit()
        days = days_override or await self.config.default_days()
        limit = max(1, min(limit, 10))
        days = max(1, min(days, 90))

        async with ctx.typing():
            events, warnings, diag = await self._fetch_for_place(
                place, days=days, limit=limit, store=store
            )
        # Stash the most recent diag for the diag command
        self._last_diag[(ctx.guild.id if ctx.guild else 0, place.key)] = diag

        log.info(
            "whatsonin region=%s place=%s sources=%d events=%d warnings=%d",
            self._resolver_region,
            place.key,
            len(place.sources),
            len(events),
            len(warnings),
        )
        source_counts = dict(Counter(e.source for e in events))
        ages = [d["cache_age_s"] for d in diag.values() if d["cache_age_s"] is not None]
        cache_age_s = min(ages) if ages else None
        parent = await store.aggregated_parent(place)
        # The aggregated_parent warning we add to `warnings` in _fetch_for_place
        # is for the diag/log surface. Render the inline note instead and
        # filter the duplicate-y warning out so it doesn't appear twice.
        display_warnings = [
            w for w in warnings if not w.startswith("Eventbrite has no ")
        ]
        payload = render_places_listing(
            place,
            events,
            display_warnings,
            days=days,
            source_counts=source_counts,
            cache_age_s=cache_age_s,
            aggregated_parent=parent,
        )
        await ctx.send(embed=discord.Embed(**payload))

    @commands.command(name="whatsonintest")
    @commands.is_owner()
    async def whatsonintest(
        self,
        ctx: commands.Context,
        kind: str,
        *,
        spec_args: str,
    ) -> None:
        """Test-fetch any source without persisting (bot owner only).

        Examples:
        `[p]whatsonintest eventbrite australia--hobart`
        `[p]whatsonintest ics https://example.com/calendar.ics`
        """
        from .models import Source

        kind = kind.strip().lower()
        if kind not in PROVIDERS:
            await ctx.send(
                f"Unknown source kind `{kind}`. Known: {', '.join(sorted(PROVIDERS))}."
            )
            return

        spec = _spec_from_args(kind, spec_args.strip())
        if spec is None:
            await ctx.send(
                f"Couldn't parse spec for `{kind}`. "
                f"Try `[p]whatsonintest eventbrite australia--hobart` "
                f"or `[p]whatsonintest ics https://example.com/cal.ics`."
            )
            return

        days = await self.config.default_days()
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
            description=f"Found {len(events)} event(s) in the next {days} days.",
            color=discord.Color.blue(),
        )
        if events:
            preview = "\n".join(f"• {event.title}" for event in events[:5])
            embed.add_field(name="Sample events", value=preview[:1024], inline=False)
        if warnings:
            embed.add_field(
                name="Warnings",
                value="\n".join(warnings)[:1024],
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="whatsonindiag")
    async def whatsonindiag(self, ctx: commands.Context, *, placename: str) -> None:
        """Per-source breakdown of the last fetch for a place: events,
        warnings, cache age. Open to everyone (read-only)."""
        try:
            store = await self._ensure_store(ctx.guild)
        except RegionNotFoundError as exc:
            await ctx.send(str(exc))
            return
        place = await store.resolve(placename)
        if place is None:
            await ctx.send(f"I don't recognise **{placename}**.")
            return

        guild_id = ctx.guild.id if ctx.guild else 0
        diag = self._last_diag.get((guild_id, place.key))
        if diag is None:
            await ctx.send(
                f"No recent fetch for **{place.display_name}**. "
                f"Run `[p]whatsonin {place.key}` first."
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
            lines.append(
                f"`{idx}` {entry['kind']}{label}{spec_str}"
            )
            lines.append(
                f"   events={entry['events']}, {age_str}"
                + (f", error={entry['error']}" if entry["error"] else "")
            )
            for w in entry["warnings"]:
                lines.append(f"   ⚠ {w}")
        await ctx.send("\n".join(lines)[:1900])

    @commands.command(name="whatsoninplaces")
    async def whatsoninplaces(self, ctx: commands.Context) -> None:
        """List all places resolvable in this guild (guild + pack)."""
        try:
            store = await self._ensure_store(ctx.guild)
        except RegionNotFoundError as exc:
            await ctx.send(str(exc))
            return
        known = await store.known_places()
        if not known:
            await ctx.send("No places configured.")
            return
        lines = ["**Places resolvable here:**"]
        for name, origin in known:
            marker = "🏠" if origin == "guild" else "📦"
            lines.append(f"{marker} {name} ({origin})")
        await ctx.send("\n".join(lines)[:1900])

    @commands.command(name="whatsoninregions")
    async def whatsoninregions(self, ctx: commands.Context) -> None:
        """List available region packs and the active region."""
        active = await self.config.active_region()
        regions = PlaceResolver.available_regions()
        lines = [f"**Active region:** `{active}`", "", "**Available regions:**"]
        for region in regions:
            marker = " (active)" if region == active else ""
            lines.append(f"• `{region}`{marker}")
        lines.append("")
        lines.append(
            "Switch with `[p]set whatsonin active_region <name>` (applies on the next command)."
        )
        await ctx.send("\n".join(lines))

    # ------------------------------------------------------------------
    # Per-guild write commands (admins only)
    # ------------------------------------------------------------------

    @commands.group(name="whatsonin_admin", aliases=["wsa"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def whatsonin_admin(self, ctx: commands.Context) -> None:
        """Per-guild place configuration (admins only).

        Subcommands: add, remove, source, alias, manual, places
        """
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @whatsonin_admin.command(name="add")
    async def admin_add(
        self, ctx: commands.Context, key: str, *, display_name: Optional[str] = None
    ) -> None:
        """Create a new place in this guild.

        `[p]wsa add brisbane Brisbane`
        """
        store = GuildStore(self.config, ctx.guild)
        place = await store.add_place(key, display_name=display_name or key.title())
        log.info(
            "admin add guild=%s key=%s by=%s", ctx.guild.id, place.key, ctx.author
        )
        await ctx.send(
            f"Added place **{place.display_name}** (`{place.key}`). "
            f"Add a source with `[p]wsa source add {place.key} <kind> <spec>`."
        )

    @whatsonin_admin.command(name="remove")
    async def admin_remove(self, ctx: commands.Context, key: str) -> None:
        """Remove a place from this guild."""
        store = GuildStore(self.config, ctx.guild)
        ok = await store.remove_place(key)
        if ok:
            log.info("admin remove guild=%s key=%s by=%s", ctx.guild.id, key, ctx.author)
            await ctx.send(f"Removed place `{key}`.")
        else:
            await ctx.send(f"No place `{key}` in this guild.")

    @whatsonin_admin.command(name="places")
    async def admin_places(self, ctx: commands.Context) -> None:
        """List places configured in THIS guild (excludes bundled pack)."""
        store = GuildStore(self.config, ctx.guild)
        keys = await store.list_keys()
        if not keys:
            await ctx.send(
                "No guild-defined places. Bundled pack still applies. "
                "Try `[p]whatsoninregions`."
            )
            return
        lines = ["**Guild places:**"]
        for key in keys:
            place = await store.get(key)
            assert place is not None
            kinds = ", ".join(s.kind for s in place.sources) or "(no sources yet)"
            lines.append(f"• `{key}` ({place.display_name}): {kinds}")
        await ctx.send("\n".join(lines))

    # ---- source subgroup ----

    @whatsonin_admin.group(name="source")
    async def admin_source(self, ctx: commands.Context) -> None:
        """Manage sources on a place: add / remove / list."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @admin_source.command(name="add")
    async def admin_source_add(
        self,
        ctx: commands.Context,
        key: str,
        kind: str,
        *,
        spec_args: str,
    ) -> None:
        """Add a source to a place; validates with one fetch.

        `[p]wsa source add hobart eventbrite australia--hobart`
        `[p]wsa source add hobart ics https://example.com/cal.ics`
        `[p]wsa source add hobart rss https://example.com/events/feed/`
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

        # Validate via one fetch
        prov = await self._get_provider(kind)
        if prov is None:
            await ctx.send(f"No provider for `{kind}`.")
            return
        source = Source(kind=kind, spec=spec)
        async with ctx.typing():
            result = await prov.fetch(source, days=90, limit=10)
        if result.error:
            await ctx.send(f"❌ Validation failed: {result.error}")
            return

        store = GuildStore(self.config, ctx.guild)
        try:
            place = await store.add_source(key, source)
        except KeyError:
            await ctx.send(
                f"No place `{key}` in this guild. Create it first with "
                f"`[p]wsa add {key}`."
            )
            return
        log.info(
            "admin source add guild=%s key=%s kind=%s by=%s",
            ctx.guild.id, key, kind, ctx.author,
        )
        await ctx.send(
            f"✅ Added {kind} source to **{place.display_name}** "
            f"(validation found {len(result.events)} upcoming events)."
        )

    @admin_source.command(name="remove")
    async def admin_source_remove(
        self, ctx: commands.Context, key: str, index: int
    ) -> None:
        """Remove the Nth source from a place. Use `source list` to find indexes."""
        store = GuildStore(self.config, ctx.guild)
        try:
            place = await store.remove_source(key, index)
        except KeyError:
            await ctx.send(f"No place `{key}` in this guild.")
            return
        log.info(
            "admin source remove guild=%s key=%s index=%d by=%s",
            ctx.guild.id, key, index, ctx.author,
        )
        await ctx.send(
            f"Removed source at index {index} from **{place.display_name}**. "
            f"{len(place.sources)} source(s) remain."
        )

    @admin_source.command(name="list")
    async def admin_source_list(self, ctx: commands.Context, key: str) -> None:
        """List sources on a place with their indexes."""
        store = GuildStore(self.config, ctx.guild)
        place = await store.get(key)
        if place is None:
            await ctx.send(f"No place `{key}` in this guild.")
            return
        if not place.sources:
            await ctx.send(f"**{place.display_name}** has no sources yet.")
            return
        lines = [f"**Sources for {place.display_name}:**"]
        for i, src in enumerate(place.sources):
            label = f" ({src.label})" if src.label else ""
            spec_str = ", ".join(f"{k}={v}" for k, v in src.spec.items())
            lines.append(f"`{i}` {src.kind}{label}: {spec_str}")
        await ctx.send("\n".join(lines))

    # ---- alias subgroup ----

    @whatsonin_admin.group(name="alias")
    async def admin_alias(self, ctx: commands.Context) -> None:
        """Manage aliases on a place: add / remove."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @admin_alias.command(name="add")
    async def admin_alias_add(
        self, ctx: commands.Context, key: str, alias: str
    ) -> None:
        store = GuildStore(self.config, ctx.guild)
        try:
            place = await store.add_alias(key, alias)
        except KeyError:
            await ctx.send(f"No place `{key}` in this guild.")
            return
        await ctx.send(f"Aliases for **{place.display_name}**: {', '.join(place.aliases) or '(none)'}")

    @admin_alias.command(name="remove")
    async def admin_alias_remove(
        self, ctx: commands.Context, key: str, alias: str
    ) -> None:
        store = GuildStore(self.config, ctx.guild)
        try:
            place = await store.remove_alias(key, alias)
        except KeyError:
            await ctx.send(f"No place `{key}` in this guild.")
            return
        await ctx.send(f"Aliases for **{place.display_name}**: {', '.join(place.aliases) or '(none)'}")

    # ---- manual subgroup ----

    @whatsonin_admin.group(name="manual")
    async def admin_manual(self, ctx: commands.Context) -> None:
        """Manage manual events on a place: add / remove."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @admin_manual.command(name="add")
    async def admin_manual_add(
        self,
        ctx: commands.Context,
        key: str,
        title: str,
        start_iso: str,
        *,
        extras: str = "",
    ) -> None:
        """Add a manual event to a place.

        `[p]wsa manual add hobart "Trivia Night" 2026-06-04T19:00 --venue Republic`
        `extras` supports --venue X --url X --end ISO.
        """
        venue, url, end = None, None, None
        if "--venue" in extras:
            venue = extras.split("--venue", 1)[1].split("--", 1)[0].strip() or None
        if "--url" in extras:
            url = extras.split("--url", 1)[1].split("--", 1)[0].strip() or None
        if "--end" in extras:
            end = extras.split("--end", 1)[1].split("--", 1)[0].strip() or None

        store = GuildStore(self.config, ctx.guild)
        place = await store.get(key)
        if place is None:
            await ctx.send(f"No place `{key}` in this guild.")
            return

        # Find or create a manual source on the place
        manual_index = next(
            (i for i, s in enumerate(place.sources) if s.kind == "manual"), None
        )
        event_payload = {
            "title": title,
            "start": start_iso,
            "venue": venue,
            "url": url,
            "end": end,
        }
        if manual_index is None:
            await store.add_source(
                key, Source(kind="manual", spec={"events": [event_payload]})
            )
        else:
            def add_event(raw_source):
                events = list(raw_source.get("spec", {}).get("events") or [])
                events.append(event_payload)
                raw_source.setdefault("spec", {})["events"] = events
            await store.update_source(key, manual_index, mutator=add_event)

        await ctx.send(f"✅ Added manual event **{title}** to `{key}`.")

    @admin_manual.command(name="remove")
    async def admin_manual_remove(
        self, ctx: commands.Context, key: str, event_index: int
    ) -> None:
        """Remove the Nth manual event from a place."""
        store = GuildStore(self.config, ctx.guild)
        place = await store.get(key)
        if place is None:
            await ctx.send(f"No place `{key}` in this guild.")
            return
        manual_index = next(
            (i for i, s in enumerate(place.sources) if s.kind == "manual"), None
        )
        if manual_index is None:
            await ctx.send(f"`{key}` has no manual source.")
            return

        def drop(raw_source):
            events = list(raw_source.get("spec", {}).get("events") or [])
            if 0 <= event_index < len(events):
                events.pop(event_index)
            raw_source.setdefault("spec", {})["events"] = events

        await store.update_source(key, manual_index, mutator=drop)
        await ctx.send(f"Removed manual event {event_index} from `{key}`.")
