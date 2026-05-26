from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from giglist.models import Source
from giglist.providers.jsonld import parse_jsonld_events
from giglist.providers.tasguide import TasguideProvider

FIXTURES = Path(__file__).parent / "fixtures"
TASGUIDE_FIXTURE = FIXTURES / "tasguide_music.html"


def test_parse_real_tasguide_fixture_yields_events():
    """Fixture is three real <script ld+json> Event blocks captured from
    https://tasguide.com.au/category/music."""
    html = TASGUIDE_FIXTURE.read_text()
    events, warnings = parse_jsonld_events(html, "tasguide")
    assert warnings == []
    titles = [e.title for e in events]
    assert "Open Mic" in titles
    assert any("Lunchbox" in t for t in titles)
    assert all(e.source == "tasguide" for e in events)


_MINIMAL_EVENT_HTML = (
    '<script type="application/ld+json">'
    '{"@type":"Event","name":"Probe Gig","startDate":"2026-12-31T20:00:00",'
    '"location":{"name":"Probe Venue"}}'
    "</script>"
)


@pytest.mark.asyncio
async def test_tasguide_default_path_resolves_to_category_music():
    """Tasguide canonicalises /music → /category/music. The provider must
    hit the canonical URL directly so aiohttp doesn't need to follow a 302."""
    source = Source(kind="tasguide", spec={})
    expected = "https://tasguide.com.au/category/music"
    with aioresponses() as m:
        m.get(expected, body=_MINIMAL_EVENT_HTML, content_type="text/html")
        async with aiohttp.ClientSession() as s:
            prov = TasguideProvider(s, cache_ttl_seconds=0)
            result = await prov.fetch(source, days=365 * 10, limit=10)
    assert result.error is None
    assert [e.title for e in result.events] == ["Probe Gig"]


@pytest.mark.asyncio
async def test_tasguide_custom_path_is_used():
    source = Source(kind="tasguide", spec={"path": "events/south"})
    expected = "https://tasguide.com.au/events/south"
    with aioresponses() as m:
        m.get(expected, body=_MINIMAL_EVENT_HTML, content_type="text/html")
        async with aiohttp.ClientSession() as s:
            prov = TasguideProvider(s, cache_ttl_seconds=0)
            result = await prov.fetch(source, days=365 * 10, limit=10)
    assert result.error is None
    assert [e.title for e in result.events] == ["Probe Gig"]
