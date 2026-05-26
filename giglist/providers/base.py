from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from ..models import Event, ProviderResult, Source

log = logging.getLogger("red.giglist")

DEFAULT_CACHE_TTL = 600

# Same tunings whatsonin proved out: keep a just-started event visible for a
# short grace period, but discard "always on" placeholders with implausibly
# long spans (memberships, ticket-confirmation entries) that aren't real gigs.
PAST_EVENT_GRACE = timedelta(hours=2)
MAX_IN_PROGRESS_SPAN = timedelta(days=180)


class EventProvider(ABC):
    kind: str
    name: str  # log/display label; usually equal to kind

    @abstractmethod
    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        ...


def filter_events(
    events: list[Event],
    *,
    days: int,
    limit: int,
    now: Optional[datetime] = None,
) -> list[Event]:
    """Drop ended / too-old / beyond-window events and apply `limit`.

    `now` is injectable for deterministic tests."""
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

        # In-progress (started, not yet ended) — keep unless its span looks
        # like an always-on placeholder. Genuinely-upcoming long events fall
        # through to the cutoff check below and are kept on merit.
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
        log.info(
            "filter_events dropped %d in-progress event(s) over %d-day span cap",
            dropped_long_span,
            MAX_IN_PROGRESS_SPAN.days,
        )
    return filtered[:limit]


class CachedHTTPProvider(EventProvider):
    """Base for providers that fetch a single URL and parse the response text.

    Subclasses implement `_build_url`, `_cache_key`, and `_parse(text)`. The
    base handles the HTTP request, TTL cache, error mapping to ProviderResult,
    and post-fetch filtering."""

    kind = ""
    name = ""
    timeout_seconds = 20

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL,
        locale: str = "en-AU,en;q=0.9",
    ):
        self._session = session
        self._cache_ttl = max(0, cache_ttl_seconds)
        self._locale = locale
        # Cache entries carry both events and parser warnings so a recurring
        # upstream issue ("no event data in this listing") keeps surfacing
        # to the user across every cache hit, not only on the first fetch.
        self._cache: dict[str, tuple[float, list[Event], list[str]]] = {}

    # ---- subclass hooks ----------------------------------------------------

    def _cache_key(self, source: Source) -> str:
        raise NotImplementedError

    def _build_url(self, source: Source) -> str:
        raise NotImplementedError

    def _headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (compatible; GiglistRedCog/0.1; +https://github.com/)",
            "Accept-Language": self._locale,
        }

    def _safe_url(self, url: str) -> str:
        """The URL string used in logs and `ProviderResult.error`. Subclasses
        override this to strip secrets (e.g. `apikey=…`) before the URL is
        ever exposed to logs or the Discord channel. Default: identity."""
        return url

    def _parse(self, text: str) -> tuple[list[Event], list[str]]:
        raise NotImplementedError

    # ---- cache -------------------------------------------------------------

    def clear_cache(self) -> None:
        self._cache.clear()

    def _cache_get(
        self, key: str
    ) -> Optional[tuple[list[Event], list[str]]]:
        if self._cache_ttl <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, events, warnings = entry
        if time.monotonic() - cached_at > self._cache_ttl:
            del self._cache[key]
            return None
        return events, warnings

    def _cache_set(
        self, key: str, events: list[Event], warnings: list[str]
    ) -> None:
        if self._cache_ttl <= 0:
            return
        self._cache[key] = (time.monotonic(), events, list(warnings))

    def cache_age_seconds(self, source: Source) -> Optional[int]:
        entry = self._cache.get(self._cache_key(source))
        if entry is None:
            return None
        cached_at, *_ = entry
        return int(time.monotonic() - cached_at)

    # ---- fetch -------------------------------------------------------------

    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        key = self._cache_key(source)
        cached = self._cache_get(key)
        if cached is not None:
            cached_events, cached_warnings = cached
            log.info(
                "%s cache hit key=%s cached_events=%d",
                self.name, key, len(cached_events),
            )
            return ProviderResult(
                events=filter_events(cached_events, days=days, limit=limit),
                warnings=list(cached_warnings),
            )

        url = self._build_url(source)
        safe_url = self._safe_url(url)  # secrets-stripped URL for logs/errors
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as resp:
                if resp.status >= 400:
                    log.warning("%s http %d url=%s", self.name, resp.status, safe_url)
                    return ProviderResult(
                        events=[],
                        warnings=[],
                        error=f"{self.name} request failed ({resp.status}).",
                    )
                text = await resp.text()
        except asyncio.TimeoutError:
            # Not a subclass of aiohttp.ClientError — must be caught explicitly
            # so a slow upstream doesn't kill the whole multi-source aggregation.
            log.warning(
                "%s timed out after %ds url=%s",
                self.name, self.timeout_seconds, safe_url,
            )
            return ProviderResult(
                events=[],
                warnings=[],
                error=f"{self.name} timed out after {self.timeout_seconds}s.",
            )
        except aiohttp.ClientError as exc:
            log.warning("%s request error url=%s err=%s", self.name, safe_url, exc)
            return ProviderResult(
                events=[], warnings=[], error=f"{self.name} request error: {exc}"
            )

        parsed, parser_warnings = self._parse(text)
        self._cache_set(key, parsed, parser_warnings)
        if parser_warnings:
            for warning in parser_warnings:
                log.warning("%s parser url=%s: %s", self.name, safe_url, warning)
        else:
            log.info(
                "%s fetched url=%s parsed_events=%d",
                self.name, safe_url, len(parsed),
            )
        return ProviderResult(
            events=filter_events(parsed, days=days, limit=limit),
            warnings=list(parser_warnings),
        )
