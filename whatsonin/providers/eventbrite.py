from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp

from ..models import Event, ProviderResult, Source
from .base import EventProvider

EVENTBRITE_BASE = "https://www.eventbrite.com/d"
DEFAULT_CACHE_TTL = 600

log = logging.getLogger("red.whatsonin")


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_eventbrite_html(html: str) -> tuple[list[Event], list[str]]:
    """Parse Eventbrite JSON-LD into events plus any parser-level warnings."""
    events: list[Event] = []
    blocks_found = 0
    for match in re.finditer(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    ):
        blocks_found += 1
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict) and data.get("@type") == "ItemList":
            for element in data.get("itemListElement", []):
                if not isinstance(element, dict):
                    continue
                item = element.get("item")
                if isinstance(item, dict) and item.get("@type") == "Event":
                    parsed = _parse_schema_event(item)
                    if parsed:
                        events.append(parsed)
        elif isinstance(data, dict) and data.get("@type") == "Event":
            parsed = _parse_schema_event(data)
            if parsed:
                events.append(parsed)

    warnings: list[str] = []
    if not events:
        if blocks_found == 0:
            warnings.append(
                "Eventbrite returned no event data (page may be blocked, "
                "rate-limited, or restructured)."
            )
        else:
            warnings.append(
                "Eventbrite returned no event data in this directory listing."
            )

    return events, warnings


def _parse_schema_event(item: dict) -> Optional[Event]:
    title = item.get("name")
    if not title:
        return None

    location = item.get("location")
    venue = None
    if isinstance(location, dict):
        venue = location.get("name")
        if not venue:
            address = location.get("address")
            if isinstance(address, dict):
                parts = [
                    str(address.get(k) or "")
                    for k in ("streetAddress", "addressLocality", "addressRegion")
                ]
                venue = ", ".join(p for p in parts if p) or None

    return Event(
        title=str(title).strip(),
        start=_parse_datetime(item.get("startDate")),
        end=_parse_datetime(item.get("endDate")),
        venue=venue,
        url=item.get("url") if isinstance(item.get("url"), str) else None,
        source="eventbrite",
        description=item.get("description") if isinstance(item.get("description"), str) else None,
    )


PAST_EVENT_GRACE = timedelta(hours=2)

# Events that started in the past are kept while still "in progress" (end in
# the future). But MEC/WordPress feeds emit "always on" placeholders —
# memberships, booking-confirmation pages — with multi-year end dates. A real
# event, even a long exhibition, doesn't run for more than ~6 months, so a span
# beyond this marks a non-event we drop rather than show as currently on.
MAX_IN_PROGRESS_SPAN = timedelta(days=180)


def filter_events(
    events: list[Event],
    *,
    days: int,
    limit: int,
    now: Optional[datetime] = None,
) -> list[Event]:
    """Drop events that have ended or started too long ago, and events beyond
    the lookahead window. `now` is injectable for deterministic tests."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    cutoff = now + timedelta(days=days)
    past_threshold = now - PAST_EVENT_GRACE

    filtered: list[Event] = []
    dropped_long_span = 0
    for event in events:
        if event.start is None:
            filtered.append(event)
            continue

        start = event.start
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        end = event.end
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # Keep events already under way (started, not yet ended) even though
        # their start is in the past — unless the span is implausibly long,
        # which marks an "always on" placeholder (see MAX_IN_PROGRESS_SPAN).
        # The `start < now` gate keeps this off genuinely-upcoming long events,
        # which fall through to the window check below and are kept on merit.
        if end is not None and start < now <= end:
            if end - start > MAX_IN_PROGRESS_SPAN:
                dropped_long_span += 1
                continue
            filtered.append(event)
            continue

        if start < past_threshold:
            continue
        if start > cutoff:
            continue
        filtered.append(event)

    if dropped_long_span:
        # Not surfaced to users (these are non-events on every fetch), but
        # logged so an admin can explain a missing long-running event.
        log.info(
            "filter_events dropped %d in-progress event(s) over the %d-day "
            "span cap (likely always-on placeholders)",
            dropped_long_span,
            MAX_IN_PROGRESS_SPAN.days,
        )

    # Sorting is the aggregator's job (aggregator.merge_events). Keeping
    # it there avoids two sorts on the cog's single-provider path and is
    # the right place when multiple providers are merged.
    return filtered[:limit]


class EventbriteProvider(EventProvider):
    kind = "eventbrite"
    name = "eventbrite"  # legacy alias used in some log lines

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        locale: str = "en-AU,en;q=0.9",
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL,
    ):
        self._session = session
        self._locale = locale
        self._cache_ttl = max(0, cache_ttl_seconds)
        self._cache: dict[str, tuple[float, list[Event]]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def _get_cached_events(self, slug: str) -> Optional[list[Event]]:
        if self._cache_ttl <= 0:
            return None
        entry = self._cache.get(slug)
        if entry is None:
            return None
        cached_at, events = entry
        if time.monotonic() - cached_at > self._cache_ttl:
            del self._cache[slug]
            return None
        return events

    def _store_cache(self, slug: str, events: list[Event]) -> None:
        if self._cache_ttl <= 0:
            return
        self._cache[slug] = (time.monotonic(), events)

    def cache_age_seconds(self, slug: str) -> Optional[int]:
        entry = self._cache.get(slug)
        if entry is None:
            return None
        cached_at, _ = entry
        return int(time.monotonic() - cached_at)

    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        slug = source.spec["slug"]
        cached = self._get_cached_events(slug)
        if cached is not None:
            log.info("eventbrite cache hit slug=%s cached_events=%d", slug, len(cached))
            return ProviderResult(
                events=filter_events(cached, days=days, limit=limit),
                warnings=[],
            )

        # /events--in-person/ filters out online events that Eventbrite would
        # otherwise dump into city directories regardless of geography.
        url = f"{EVENTBRITE_BASE}/{slug}/events--in-person/"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; WhatsoninRedCog/0.2; +https://github.com/)",
            "Accept-Language": self._locale,
        }

        try:
            async with self._session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status >= 400:
                    log.warning(
                        "eventbrite http %d slug=%s", resp.status, slug
                    )
                    return ProviderResult(
                        events=[],
                        warnings=[],
                        error=(
                            f"Eventbrite request failed ({resp.status}) for slug "
                            f"`{slug}`."
                        ),
                    )
                html = await resp.text()
        except aiohttp.ClientError as exc:
            log.warning("eventbrite request error slug=%s err=%s", slug, exc)
            return ProviderResult(
                events=[],
                warnings=[],
                error=f"Eventbrite request error: {exc}",
            )

        parsed, parser_warnings = parse_eventbrite_html(html)
        self._store_cache(slug, parsed)
        if parser_warnings:
            for warning in parser_warnings:
                log.warning("eventbrite parser slug=%s: %s", slug, warning)
        else:
            log.info(
                "eventbrite fetched slug=%s parsed_events=%d", slug, len(parsed)
            )
        return ProviderResult(
            events=filter_events(parsed, days=days, limit=limit),
            warnings=list(parser_warnings),
        )
