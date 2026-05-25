from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from whatsonin.models import Source
from whatsonin.providers.ics import IcsProvider, parse_ics


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_ICS = (FIXTURES / "sample.ics").read_text()


FIXED_NOW = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)


def test_parse_ics_extracts_events():
    events = parse_ics(SAMPLE_ICS)
    titles = [e.title for e in events]
    assert "Salamanca Market" in titles
    assert "Live Jazz at the Republic" in titles


def test_parse_ics_sets_required_event_fields():
    events = parse_ics(SAMPLE_ICS)
    market = next(e for e in events if e.title == "Salamanca Market")
    assert market.start == datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc)
    assert market.end == datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)
    assert market.venue == "Salamanca Place, Hobart"
    assert market.url == "https://example.org/salamanca"
    assert market.source == "ics"


def test_parse_ics_handles_optional_fields():
    events = parse_ics(SAMPLE_ICS)
    jazz = next(e for e in events if e.title == "Live Jazz at the Republic")
    assert jazz.url is None
    assert jazz.description is None


def test_parse_ics_empty_returns_empty_list():
    events = parse_ics("")
    assert events == []


def test_parse_ics_invalid_returns_empty_list():
    events = parse_ics("not a calendar")
    assert events == []


@pytest.mark.asyncio
async def test_ics_provider_fetches_and_parses():
    source = Source(kind="ics", spec={"url": "https://example.com/cal.ics"})

    with aioresponses() as mocked:
        mocked.get(source.spec["url"], body=SAMPLE_ICS, content_type="text/calendar")
        async with aiohttp.ClientSession() as session:
            provider = IcsProvider(session, cache_ttl_seconds=0)
            result = await provider.fetch(source, days=365, limit=10)

    assert result.error is None
    titles = [e.title for e in result.events]
    assert "Salamanca Market" in titles
    # past event filtered out by filter_events
    assert "Already happened" not in titles


@pytest.mark.asyncio
async def test_ics_provider_reports_http_error():
    source = Source(kind="ics", spec={"url": "https://example.com/missing.ics"})

    with aioresponses() as mocked:
        mocked.get(source.spec["url"], status=404)
        async with aiohttp.ClientSession() as session:
            provider = IcsProvider(session, cache_ttl_seconds=0)
            result = await provider.fetch(source, days=30, limit=10)

    assert result.events == []
    assert result.error and "404" in result.error


@pytest.mark.asyncio
async def test_ics_provider_caches_by_url():
    source = Source(kind="ics", spec={"url": "https://example.com/cal.ics"})

    with aioresponses() as mocked:
        # only register the response once. Second fetch must come from cache
        mocked.get(source.spec["url"], body=SAMPLE_ICS, content_type="text/calendar")
        async with aiohttp.ClientSession() as session:
            provider = IcsProvider(session, cache_ttl_seconds=600)
            first = await provider.fetch(source, days=365, limit=10)
            second = await provider.fetch(source, days=365, limit=10)

    assert first.error is None
    assert second.error is None
    assert len(first.events) == len(second.events)
