from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import aiohttp

from ..models import Event, ProviderResult, Source
from .base import DEFAULT_CACHE_TTL, CachedHTTPProvider, filter_events
from .jsonld import parse_datetime

# Ticketmaster Discovery API v2 — the one free, self-serve, location-queryable
# music feed we found. Covers Ticketmaster-ticketed shows (big-room touring
# acts in Hobart). Free tier: 5000 calls/day. Optional in this cog: it stays
# inert until an API key is configured (Bot owner: `[p]giglistset ticketmaster_key <key>`).
BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

# Used by _safe_url to keep the apikey out of logs and ProviderResult.error.
_APIKEY_RE = re.compile(r"(apikey=)[^&]*")

# Spec keys that map directly to Discovery API query params for "where".
# `keyword` is supported as a free-text filter when given.
_LOCATION_PARAMS = ("dmaId", "city", "geoPoint", "latlong", "radius", "unit", "keyword")


class TicketmasterProvider(CachedHTTPProvider):
    kind = "ticketmaster"
    name = "ticketmaster"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str = "",
        country: str = "AU",
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL,
        page_size: int = 100,
    ):
        super().__init__(session, cache_ttl_seconds=cache_ttl_seconds)
        self._api_key = (api_key or "").strip()
        self._country = (country or "AU").strip() or "AU"
        self._page_size = max(1, min(int(page_size or 100), 200))

    # ---- URL + cache -------------------------------------------------------

    def _cache_key(self, source: Source) -> str:
        # Cache by the location-defining spec only; days/limit are applied
        # at read time via filter_events. apikey is intentionally excluded so
        # rotating the key doesn't invalidate the cache.
        relevant = {k: source.spec[k] for k in _LOCATION_PARAMS if k in source.spec}
        return json.dumps(relevant, sort_keys=True)

    def _build_url(self, source: Source) -> str:
        params = [
            ("apikey", self._api_key),
            ("classificationName", "music"),
            ("countryCode", self._country),
            ("size", str(self._page_size)),
            ("sort", "date,asc"),
        ]
        for k in _LOCATION_PARAMS:
            v = source.spec.get(k)
            if v not in (None, ""):
                params.append((k, str(v)))
        return f"{BASE_URL}?{urlencode(params)}"

    def _safe_url(self, url: str) -> str:
        # Strip the apikey so it never appears in logs or in
        # ProviderResult.error (which the cog forwards to the channel).
        return _APIKEY_RE.sub(r"\1[REDACTED]", url)

    # ---- fetch override: API key gate --------------------------------------

    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        if not self._api_key:
            return ProviderResult(
                events=[],
                warnings=[],
                error=(
                    "Ticketmaster API key not configured. Set it with "
                    "`[p]giglistset ticketmaster_key <key>` (free at "
                    "developer.ticketmaster.com)."
                ),
            )
        return await super().fetch(source, days=days, limit=limit)

    # ---- response parsing --------------------------------------------------

    def _parse(self, text: str) -> tuple[list[Event], list[str]]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return [], [f"ticketmaster JSON decode error: {exc}"]

        events: list[Event] = []
        for raw in (data.get("_embedded") or {}).get("events") or []:
            event = _parse_event(raw)
            if event is not None:
                events.append(event)

        warnings: list[str] = []
        if not events and not (data.get("_embedded") or {}).get("events"):
            warnings.append("ticketmaster returned no events for this query.")
        return events, warnings


def _parse_event(raw: dict) -> Optional[Event]:
    title = raw.get("name")
    if not title:
        return None
    dates = raw.get("dates") or {}
    start_block = dates.get("start") or {}
    start = _resolve_start(start_block)
    venue = _resolve_venue(raw)
    url = raw.get("url") if isinstance(raw.get("url"), str) else None
    return Event(
        title=str(title).strip(),
        start=start,
        end=None,
        venue=venue,
        url=url,
        source="ticketmaster",
    )


def _resolve_start(start_block: dict) -> Optional[datetime]:
    # Prefer the precise ISO `dateTime` (carries tz / UTC offset).
    dt = parse_datetime(start_block.get("dateTime"))
    if dt is not None:
        return dt
    local_date = start_block.get("localDate")
    if not local_date:
        return None
    local_time = start_block.get("localTime")
    combined = f"{local_date}T{local_time}" if local_time else local_date
    return parse_datetime(combined)


def _resolve_venue(raw: dict) -> Optional[str]:
    venues = (raw.get("_embedded") or {}).get("venues") or []
    if not venues or not isinstance(venues[0], dict):
        return None
    name = venues[0].get("name")
    return str(name).strip() if name else None
