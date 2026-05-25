from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

import aiohttp
from icalendar import Calendar

from ..models import Event, ProviderResult, Source
from .base import EventProvider
from .eventbrite import DEFAULT_CACHE_TTL, filter_events

log = logging.getLogger("red.whatsonin")


def _to_datetime(value) -> Optional[datetime]:
    """Coerce an icalendar dt value to an aware UTC datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        # All-day event, represented as midnight UTC for sorting
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return None


def _text(component, key: str) -> Optional[str]:
    val = component.get(key)
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def parse_ics(text: str) -> list[Event]:
    """Parse an ICS document into Event records. Returns [] on any failure."""
    if not text or not text.strip():
        return []
    try:
        cal = Calendar.from_ical(text)
    except Exception:
        return []

    events: list[Event] = []
    for component in cal.walk("VEVENT"):
        title = _text(component, "summary")
        if not title:
            continue
        start = _to_datetime(component.get("dtstart").dt) if component.get("dtstart") else None
        end = _to_datetime(component.get("dtend").dt) if component.get("dtend") else None
        events.append(
            Event(
                title=title,
                start=start,
                end=end,
                venue=_text(component, "location"),
                url=_text(component, "url"),
                source="ics",
                description=_text(component, "description"),
            )
        )
    return events


class IcsProvider(EventProvider):
    kind = "ics"
    name = "ics"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL,
    ):
        self._session = session
        self._cache_ttl = max(0, cache_ttl_seconds)
        self._cache: dict[str, tuple[float, list[Event]]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def _cache_get(self, url: str) -> Optional[list[Event]]:
        if self._cache_ttl <= 0:
            return None
        entry = self._cache.get(url)
        if entry is None:
            return None
        cached_at, events = entry
        if time.monotonic() - cached_at > self._cache_ttl:
            del self._cache[url]
            return None
        return events

    def _cache_set(self, url: str, events: list[Event]) -> None:
        if self._cache_ttl <= 0:
            return
        self._cache[url] = (time.monotonic(), events)

    def cache_age_seconds(self, url: str) -> Optional[int]:
        entry = self._cache.get(url)
        if entry is None:
            return None
        cached_at, _ = entry
        return int(time.monotonic() - cached_at)

    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        url = source.spec["url"]
        cached = self._cache_get(url)
        if cached is not None:
            log.info("ics cache hit url=%s cached_events=%d", url, len(cached))
            return ProviderResult(
                events=filter_events(cached, days=days, limit=limit),
                warnings=[],
            )

        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status >= 400:
                    log.warning("ics http %d url=%s", resp.status, url)
                    return ProviderResult(
                        events=[],
                        warnings=[],
                        error=f"ICS request failed ({resp.status}) for {url}.",
                    )
                text = await resp.text()
        except aiohttp.ClientError as exc:
            log.warning("ics request error url=%s err=%s", url, exc)
            return ProviderResult(
                events=[], warnings=[], error=f"ICS request error: {exc}"
            )

        parsed = parse_ics(text)
        self._cache_set(url, parsed)
        if not parsed:
            log.warning("ics empty parse url=%s", url)
        else:
            log.info("ics fetched url=%s parsed_events=%d", url, len(parsed))

        warnings: list[str] = []
        if not parsed:
            warnings.append(
                f"ICS source returned no events (URL may be unreachable or invalid)."
            )

        return ProviderResult(
            events=filter_events(parsed, days=days, limit=limit),
            warnings=warnings,
        )
