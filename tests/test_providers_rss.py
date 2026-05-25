from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from whatsonin.models import Source
from whatsonin.providers.rss import RssProvider, parse_rss


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_MEC = (FIXTURES / "sample_rss_mec.xml").read_text()
SAMPLE_TRIBE = (FIXTURES / "sample_rss_tribe.xml").read_text()
SAMPLE_PLAIN = (FIXTURES / "sample_rss_plain.xml").read_text()


def test_parse_mec_extracts_two_events():
    events, _ = parse_rss(SAMPLE_MEC)
    titles = [e.title for e in events]
    assert "Kinship Connections" in titles
    assert "Empower Golf Clinic" in titles


def test_parse_mec_combines_start_date_and_hour():
    events, _ = parse_rss(SAMPLE_MEC)
    kinship = next(e for e in events if e.title == "Kinship Connections")
    # 2026-05-26 at 10:00 am, naive treated as UTC
    assert kinship.start == datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)
    assert kinship.end == datetime(2026, 5, 26, 11, 0, tzinfo=timezone.utc)
    assert kinship.venue == "The Hub"
    assert kinship.url == "https://example.com/event/kinship-connections"
    assert kinship.source == "rss"


def test_parse_mec_pm_hour_handling():
    events, _ = parse_rss(SAMPLE_MEC)
    golf = next(e for e in events if e.title == "Empower Golf Clinic")
    assert golf.start == datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)


def test_parse_tribe_uses_iso_startdate():
    events, _ = parse_rss(SAMPLE_TRIBE)
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Open Mic Night"
    assert ev.start == datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)  # +10:00 → 09:00 UTC
    assert ev.venue == "Town Hall"


def test_parse_plain_rss_falls_back_to_pubdate_and_warns():
    events, warnings = parse_rss(SAMPLE_PLAIN)
    assert len(events) == 1
    # pubDate Mon, 20 May 2026 10:00:00 +1000 → 00:00 UTC
    assert events[0].start == datetime(2026, 5, 20, 0, 0, tzinfo=timezone.utc)
    assert warnings  # warn user that pubDate isn't a real event date


def test_parse_rss_empty_returns_empty():
    events, _ = parse_rss("")
    assert events == []


def test_parse_rss_invalid_returns_empty_with_warning():
    events, warnings = parse_rss("not xml at all")
    assert events == []
    assert warnings


def test_parse_rss_strips_html_from_description():
    events, _ = parse_rss(SAMPLE_MEC)
    kinship = next(e for e in events if e.title == "Kinship Connections")
    assert kinship.description is not None
    assert "<" not in kinship.description
    assert "Weekly gathering" in kinship.description


@pytest.mark.asyncio
async def test_rss_provider_fetches_and_parses():
    source = Source(kind="rss", spec={"url": "https://example.com/events/feed/"})

    with aioresponses() as mocked:
        mocked.get(
            source.spec["url"], body=SAMPLE_MEC, content_type="application/rss+xml"
        )
        async with aiohttp.ClientSession() as session:
            provider = RssProvider(session, cache_ttl_seconds=0)
            result = await provider.fetch(source, days=365, limit=10)

    assert result.error is None
    titles = [e.title for e in result.events]
    assert "Kinship Connections" in titles


@pytest.mark.asyncio
async def test_rss_provider_reports_http_error():
    source = Source(kind="rss", spec={"url": "https://example.com/missing.xml"})

    with aioresponses() as mocked:
        mocked.get(source.spec["url"], status=404)
        async with aiohttp.ClientSession() as session:
            provider = RssProvider(session, cache_ttl_seconds=0)
            result = await provider.fetch(source, days=30, limit=10)

    assert result.events == []
    assert result.error and "404" in result.error


@pytest.mark.asyncio
async def test_rss_provider_caches_by_url():
    source = Source(kind="rss", spec={"url": "https://example.com/feed.xml"})

    with aioresponses() as mocked:
        mocked.get(source.spec["url"], body=SAMPLE_MEC, content_type="text/xml")
        async with aiohttp.ClientSession() as session:
            provider = RssProvider(session, cache_ttl_seconds=600)
            first = await provider.fetch(source, days=365, limit=10)
            second = await provider.fetch(source, days=365, limit=10)

    assert first.error is None
    assert second.error is None
    assert len(first.events) == len(second.events)
