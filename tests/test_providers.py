import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aioresponses import aioresponses

from whatsonin.aggregator import merge_events
from whatsonin.models import Event, Source
from whatsonin.places import PlaceResolver
from whatsonin.providers.eventbrite import (
    EventbriteProvider,
    filter_events,
    parse_eventbrite_html,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_eventbrite_fixture():
    html = (FIXTURES / "eventbrite_hobart.html").read_text()
    events, warnings = parse_eventbrite_html(html)
    assert len(events) >= 1
    assert events[0].source == "eventbrite"
    assert events[0].title
    assert warnings == []


def test_parse_warns_when_no_jsonld_event_blocks():
    html = "<html><body><p>captcha or layout change</p></body></html>"
    events, warnings = parse_eventbrite_html(html)
    assert events == []
    assert warnings
    assert any("no event data" in w.lower() for w in warnings)


def test_parse_warns_when_jsonld_present_but_no_events():
    html = (
        '<html><script type="application/ld+json">'
        '{"@type":"Organization","name":"Eventbrite"}'
        "</script></html>"
    )
    events, warnings = parse_eventbrite_html(html)
    assert events == []
    assert warnings
    assert any("no event data" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_eventbrite_provider_uses_in_person_url_filter():
    """The /events--in-person/ URL excludes Eventbrite's worldwide online
    events that would otherwise bleed into city directories."""
    import aiohttp

    source = Source(kind="eventbrite", spec={"slug": "australia--hobart"})
    expected_url = "https://www.eventbrite.com/d/australia--hobart/events--in-person/"
    html = (FIXTURES / "eventbrite_hobart.html").read_text()

    with aioresponses() as mocked:
        mocked.get(expected_url, body=html, content_type="text/html")
        async with aiohttp.ClientSession() as session:
            provider = EventbriteProvider(session, cache_ttl_seconds=0)
            result = await provider.fetch(source, days=365, limit=5)

    # If the provider hit the wrong URL, aioresponses would raise/skip and
    # result.error would be set. Otherwise it parses the fixture as before.
    assert result.error is None
    assert len(result.events) >= 1


def test_merge_handles_mixed_naive_and_aware_datetimes():
    """Eventbrite can produce naive datetimes for date-only events; RSS
    always produces aware. Sorting must not raise when both are present."""
    naive = Event("All-day fair", datetime(2026, 6, 1), None, None, None, "eventbrite")
    aware = Event(
        "Evening talk", datetime(2026, 6, 2, 19, tzinfo=timezone.utc),
        None, None, None, "rss",
    )
    merged = merge_events([naive, aware], limit=10)
    assert [e.title for e in merged] == ["All-day fair", "Evening talk"]


def test_merge_dedupes_and_sorts():
    events = [
        Event("Duplicate", datetime(2026, 6, 2, tzinfo=timezone.utc), None, "Venue", None, "eventbrite"),
        Event("Duplicate", datetime(2026, 6, 2, tzinfo=timezone.utc), None, "Venue", None, "eventbrite"),
        Event("Earlier", datetime(2026, 6, 1, tzinfo=timezone.utc), None, "A", None, "eventbrite"),
    ]
    merged = merge_events(events, limit=10)
    assert len(merged) == 2
    assert merged[0].title == "Earlier"


def test_filter_events_respects_limit():
    html = (FIXTURES / "eventbrite_hobart.html").read_text()
    parsed, _ = parse_eventbrite_html(html)
    filtered = filter_events(parsed, days=365, limit=3)
    assert len(filtered) <= 3


FIXED_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_filter_excludes_event_that_started_hours_ago():
    long_past = FIXED_NOW - timedelta(hours=6)
    events = [Event("Earlier today", long_past, None, None, None, "eventbrite")]
    assert filter_events(events, days=30, limit=10, now=FIXED_NOW) == []


def test_filter_keeps_event_currently_in_progress():
    just_started = FIXED_NOW - timedelta(minutes=30)
    events = [Event("In progress", just_started, None, None, None, "eventbrite")]
    kept = filter_events(events, days=30, limit=10, now=FIXED_NOW)
    assert len(kept) == 1


def test_filter_keeps_event_with_future_end_even_if_started_in_past():
    # Multi-day event: started days ago, ends in the future.
    started = FIXED_NOW - timedelta(days=2)
    ends = FIXED_NOW + timedelta(days=2)
    events = [Event("Festival", started, ends, None, None, "eventbrite")]
    kept = filter_events(events, days=30, limit=10, now=FIXED_NOW)
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_eventbrite_provider_parses_fixture_html():
    import aiohttp

    html = (FIXTURES / "eventbrite_hobart.html").read_text()
    place = PlaceResolver("tasmania").resolve("hobart")
    url = f"https://www.eventbrite.com/d/{place.sources[0].spec["slug"]}/events--in-person/"

    with aioresponses() as mocked:
        mocked.get(url, body=html, content_type="text/html")
        async with aiohttp.ClientSession() as session:
            provider = EventbriteProvider(session, cache_ttl_seconds=600)
            result = await provider.fetch(place.sources[0], days=365, limit=5)

    assert result.error is None
    assert len(result.events) >= 1


@pytest.mark.skipif(
    os.environ.get("EVENTBRITE_LIVE") != "1",
    reason="live network test. Set EVENTBRITE_LIVE=1 to enable",
)
@pytest.mark.asyncio
async def test_live_eventbrite_returns_events():
    """Hits real Eventbrite. Run periodically to detect layout/JSON-LD
    changes that mocked tests cannot catch."""
    import aiohttp

    place = PlaceResolver("tasmania").resolve("hobart")
    async with aiohttp.ClientSession() as session:
        provider = EventbriteProvider(session, cache_ttl_seconds=0)
        result = await provider.fetch(place.sources[0], days=90, limit=10)

    assert result.error is None, f"live fetch errored: {result.error}"
    assert result.events, (
        "live fetch returned zero events. Eventbrite layout may have changed. "
        f"warnings={result.warnings}"
    )


@pytest.mark.asyncio
async def test_provider_logs_successful_fetch(caplog):
    import aiohttp

    html = (FIXTURES / "eventbrite_hobart.html").read_text()
    place = PlaceResolver("tasmania").resolve("hobart")
    url = f"https://www.eventbrite.com/d/{place.sources[0].spec["slug"]}/events--in-person/"

    with caplog.at_level(logging.INFO, logger="red.whatsonin"):
        with aioresponses() as mocked:
            mocked.get(url, body=html, content_type="text/html")
            async with aiohttp.ClientSession() as session:
                provider = EventbriteProvider(session, cache_ttl_seconds=0)
                await provider.fetch(place.sources[0], days=90, limit=5)

    messages = [r.getMessage() for r in caplog.records if r.name == "red.whatsonin"]
    assert any(place.sources[0].spec["slug"] in m for m in messages), (
        f"expected slug in INFO log; got {messages}"
    )


@pytest.mark.asyncio
async def test_provider_logs_warning_when_no_event_data(caplog):
    import aiohttp

    place = PlaceResolver("tasmania").resolve("hobart")
    url = f"https://www.eventbrite.com/d/{place.sources[0].spec["slug"]}/events--in-person/"

    with caplog.at_level(logging.WARNING, logger="red.whatsonin"):
        with aioresponses() as mocked:
            mocked.get(url, body="<html></html>", content_type="text/html")
            async with aiohttp.ClientSession() as session:
                provider = EventbriteProvider(session, cache_ttl_seconds=0)
                await provider.fetch(place.sources[0], days=90, limit=5)

    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == "red.whatsonin" and r.levelno >= logging.WARNING
    ]
    assert warnings, f"expected a WARNING log for empty parse; got {caplog.records}"


@pytest.mark.asyncio
async def test_eventbrite_provider_uses_cache():
    import aiohttp

    html = (FIXTURES / "eventbrite_hobart.html").read_text()
    place = PlaceResolver("tasmania").resolve("hobart")
    url = f"https://www.eventbrite.com/d/{place.sources[0].spec["slug"]}/events--in-person/"

    with aioresponses() as mocked:
        mocked.get(url, body=html, content_type="text/html")
        async with aiohttp.ClientSession() as session:
            provider = EventbriteProvider(session, cache_ttl_seconds=600)
            first = await provider.fetch(place.sources[0], days=365, limit=5)
            second = await provider.fetch(place.sources[0], days=365, limit=5)

    assert first.error is None
    assert second.error is None
    assert len(first.events) == len(second.events)
