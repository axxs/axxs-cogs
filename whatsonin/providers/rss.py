from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from calendar import timegm
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Optional

import aiohttp
import feedparser

from ..models import Event, ProviderResult, Source
from .base import EventProvider
from .eventbrite import DEFAULT_CACHE_TTL, filter_events

log = logging.getLogger("red.whatsonin")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = _HTML_TAG_RE.sub("", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned or None


def _parse_mec_hour(hour: str) -> Optional[tuple]:
    """Parse '10:00 am' / '2:00 pm' to (hour, minute)."""
    if not hour:
        return None
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*", hour, re.IGNORECASE)
    if not m:
        return None
    h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ampm == "pm" and h != 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return h, mn


def _mec_datetime(date_str: Optional[str], hour_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        date_part = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    hm = _parse_mec_hour(hour_str) if hour_str else None
    if hm is None:
        return date_part.replace(tzinfo=timezone.utc)
    return date_part.replace(hour=hm[0], minute=hm[1], tzinfo=timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _parse_pubdate_string(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _from_struct_time(st: Optional[struct_time]) -> Optional[datetime]:
    if st is None:
        return None
    return datetime.fromtimestamp(timegm(st), tz=timezone.utc)


def _ns_get(entry, key: str) -> Optional[str]:
    """Pull a namespaced field from a feedparser entry. feedparser tends to
    expose unknown namespaces as 'ns_localname' (lowercased)."""
    val = entry.get(key)
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    return None


def _extract_event_dates(entry) -> tuple:
    """Return (start, end, source_label) where source_label is one of
    'mec', 'ev', 'pubdate'."""
    # Modern Events Calendar (mec: namespace)
    mec_start = _mec_datetime(
        _ns_get(entry, "mec_startdate"), _ns_get(entry, "mec_starthour")
    )
    if mec_start is not None:
        mec_end = _mec_datetime(
            _ns_get(entry, "mec_enddate"), _ns_get(entry, "mec_endhour")
        )
        return mec_start, mec_end, "mec"

    # RSS Event Module (ev: namespace) — Tribe Events Calendar and similar
    ev_start = _parse_iso(_ns_get(entry, "ev_startdate"))
    if ev_start is not None:
        ev_end = _parse_iso(_ns_get(entry, "ev_enddate"))
        return ev_start, ev_end, "ev"

    # Fallback: published date (post date, NOT event date)
    pub = _from_struct_time(entry.get("published_parsed")) or _parse_pubdate_string(
        entry.get("published")
    )
    return pub, None, "pubdate"


def _extract_venue(entry) -> Optional[str]:
    return _ns_get(entry, "mec_location") or _ns_get(entry, "ev_location")


def parse_rss(text: str) -> tuple:
    """Parse an RSS document into (events, warnings).

    Handles MEC (mec: namespace) and Tribe-style (ev: namespace) event RSS
    via feedparser, which tolerates real-world XML that strict parsers
    reject. Falls back to <pubDate> when no structured event date is found
    and emits a warning, since pubDate is the post-publish time, not the
    event time."""
    if not text or not text.strip():
        return [], []

    parsed = feedparser.parse(text)

    if parsed.bozo and not parsed.entries:
        exc = parsed.get("bozo_exception", "unknown error")
        return [], [f"RSS parse error: {exc}"]

    events: list = []
    pubdate_fallback_count = 0

    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        start, end, date_source = _extract_event_dates(entry)
        if date_source == "pubdate":
            pubdate_fallback_count += 1
        events.append(
            Event(
                title=title,
                start=start,
                end=end,
                venue=_extract_venue(entry),
                url=(entry.get("link") or None),
                source="rss",
                description=_strip_html(
                    entry.get("summary") or entry.get("description")
                ),
            )
        )

    warnings: list = []
    if pubdate_fallback_count:
        warnings.append(
            f"RSS feed has no structured event dates for {pubdate_fallback_count} "
            f"item(s). Falling back to <pubDate>, which is the post-publish time, "
            f"not the event time."
        )
    return events, warnings


class RssProvider(EventProvider):
    kind = "rss"
    name = "rss"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL,
    ):
        self._session = session
        self._cache_ttl = max(0, cache_ttl_seconds)
        self._cache: dict = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    def _cache_get(self, url: str):
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

    def _cache_set(self, url: str, events: list) -> None:
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
            log.info("rss cache hit url=%s cached_events=%d", url, len(cached))
            return ProviderResult(
                events=filter_events(cached, days=days, limit=limit),
                warnings=[],
            )

        try:
            async with self._session.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status >= 400:
                    log.warning("rss http %d url=%s", resp.status, url)
                    return ProviderResult(
                        events=[],
                        warnings=[],
                        error=f"RSS request failed ({resp.status}) for {url}.",
                    )
                text = await resp.text()
        except aiohttp.ClientError as exc:
            log.warning("rss request error url=%s err=%s", url, exc)
            return ProviderResult(
                events=[], warnings=[], error=f"RSS request error: {exc}"
            )

        parsed, parser_warnings = parse_rss(text)
        self._cache_set(url, parsed)
        if not parsed:
            log.warning("rss empty parse url=%s", url)
        else:
            log.info("rss fetched url=%s parsed_events=%d", url, len(parsed))

        return ProviderResult(
            events=filter_events(parsed, days=days, limit=limit),
            warnings=parser_warnings,
        )
