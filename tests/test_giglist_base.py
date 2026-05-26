from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
from aioresponses import aioresponses

from giglist.models import Event, Source
from giglist.providers.base import (
    MAX_IN_PROGRESS_SPAN,
    CachedHTTPProvider,
    filter_events,
)
from giglist.providers.jsonld import parse_jsonld_events

FIXED_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _ev(title, start, end=None, source="dummy"):
    return Event(title, start, end, None, None, source)


def test_filter_drops_past_and_respects_limit_and_window():
    events = [
        _ev("Past", FIXED_NOW - timedelta(hours=6)),
        _ev("Soon", FIXED_NOW + timedelta(days=1)),
        _ev("Later", FIXED_NOW + timedelta(days=5)),
        _ev("Beyond window", FIXED_NOW + timedelta(days=60)),
    ]
    kept = filter_events(events, days=30, limit=10, now=FIXED_NOW)
    assert [e.title for e in kept] == ["Soon", "Later"]
    assert len(filter_events(events, days=30, limit=1, now=FIXED_NOW)) == 1


def test_filter_keeps_in_progress_but_drops_multiyear_placeholder():
    in_progress = _ev(
        "On now", FIXED_NOW - timedelta(hours=1), FIXED_NOW + timedelta(hours=2)
    )
    placeholder = _ev(
        "Always on",
        FIXED_NOW - timedelta(days=400),
        FIXED_NOW + timedelta(days=400),
    )
    kept = filter_events([in_progress, placeholder], days=30, limit=10, now=FIXED_NOW)
    assert [e.title for e in kept] == ["On now"]
    assert MAX_IN_PROGRESS_SPAN.days == 180


class _DummyProvider(CachedHTTPProvider):
    kind = "dummy"
    name = "dummy"

    def _cache_key(self, source: Source) -> str:
        return source.spec["url"]

    def _build_url(self, source: Source) -> str:
        return source.spec["url"]

    def _parse(self, text: str):
        return parse_jsonld_events(text, "dummy")


def _html_one_event():
    import json as _json
    start = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
    payload = _json.dumps(
        {
            "@type": "Event",
            "name": "Test Gig",
            "startDate": start,
            "url": "https://x/1",
            "location": {"name": "The Venue"},
        }
    )
    return f'<script type="application/ld+json">{payload}</script>'


@pytest.mark.asyncio
async def test_cached_http_provider_fetches_and_parses():
    url = "https://example.test/listing"
    source = Source(kind="dummy", spec={"url": url})
    with aioresponses() as mocked:
        mocked.get(url, body=_html_one_event(), content_type="text/html")
        async with aiohttp.ClientSession() as session:
            prov = _DummyProvider(session, cache_ttl_seconds=600)
            result = await prov.fetch(source, days=365, limit=10)
    assert result.error is None
    assert [e.title for e in result.events] == ["Test Gig"]


@pytest.mark.asyncio
async def test_cached_http_provider_serves_second_call_from_cache():
    """Only one HTTP response is registered; a second fetch must hit cache
    rather than the network (aioresponses would otherwise error)."""
    url = "https://example.test/listing2"
    source = Source(kind="dummy", spec={"url": url})
    with aioresponses() as mocked:
        mocked.get(url, body=_html_one_event(), content_type="text/html")
        async with aiohttp.ClientSession() as session:
            prov = _DummyProvider(session, cache_ttl_seconds=600)
            first = await prov.fetch(source, days=365, limit=10)
            assert prov.cache_age_seconds(source) is not None
            second = await prov.fetch(source, days=365, limit=10)
    assert len(first.events) == len(second.events) == 1


@pytest.mark.asyncio
async def test_cached_http_provider_reports_http_error():
    url = "https://example.test/down"
    source = Source(kind="dummy", spec={"url": url})
    with aioresponses() as mocked:
        mocked.get(url, status=503)
        async with aiohttp.ClientSession() as session:
            prov = _DummyProvider(session, cache_ttl_seconds=0)
            result = await prov.fetch(source, days=30, limit=10)
    assert result.events == []
    assert result.error is not None
    assert "503" in result.error


@pytest.mark.asyncio
async def test_cache_hit_replays_parser_warnings_from_the_original_fetch():
    """A first fetch yielding a parser warning must surface that warning
    on every cache hit until the TTL expires — otherwise the user sees
    'no warnings' on a re-run and assumes the warning was transient."""
    url = "https://example.test/no-events"
    source = Source(kind="dummy", spec={"url": url})
    # An ld+json block that isn't an Event → parser returns no events with a
    # "no event data in this listing" warning.
    html_with_warning = (
        '<script type="application/ld+json">'
        '{"@type":"Organization","name":"Site"}'
        "</script>"
    )
    with aioresponses() as mocked:
        # Only ONE response registered — a second call going to the network
        # would error, so the second fetch must come from cache.
        mocked.get(url, body=html_with_warning, content_type="text/html")
        async with aiohttp.ClientSession() as session:
            prov = _DummyProvider(session, cache_ttl_seconds=600)
            first = await prov.fetch(source, days=365, limit=10)
            second = await prov.fetch(source, days=365, limit=10)
    assert first.warnings, "test setup expected the parser to warn"
    assert second.warnings == first.warnings, (
        f"warnings dropped on cache hit. first={first.warnings} "
        f"second={second.warnings}"
    )


@pytest.mark.asyncio
async def test_cached_http_provider_handles_timeout_gracefully():
    """asyncio.TimeoutError is NOT a subclass of aiohttp.ClientError, so a
    naïve `except aiohttp.ClientError` would let a slow upstream kill the
    whole multi-source aggregation. The base must catch it explicitly."""
    import asyncio

    url = "https://example.test/slow"
    source = Source(kind="dummy", spec={"url": url})
    with aioresponses() as mocked:
        mocked.get(url, exception=asyncio.TimeoutError())
        async with aiohttp.ClientSession() as session:
            prov = _DummyProvider(session, cache_ttl_seconds=0)
            result = await prov.fetch(source, days=30, limit=10)
    assert result.events == []
    assert result.error is not None
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
