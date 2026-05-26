from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from giglist.models import Source
from giglist.providers.humanitix import HumanitixProvider
from giglist.providers.jsonld import parse_jsonld_events

FIXTURES = Path(__file__).parent / "fixtures"
HUMANITIX_FIXTURE = FIXTURES / "humanitix_hobart_music.html"


def test_parse_real_humanitix_fixture_yields_itemlist_events():
    """Fixture is the real ItemList JSON-LD block captured from
    https://humanitix.com/au/events/au--tas--hobart/music. The block sits in
    a <script id="itemlist-json-ld" type="application/ld+json"> tag, which
    only matches with a tag regex tolerant of extra attributes."""
    html = HUMANITIX_FIXTURE.read_text()
    events, warnings = parse_jsonld_events(html, "humanitix")
    assert warnings == []
    # Humanitix renders the first 5 events server-side
    assert len(events) == 5
    assert all(e.source == "humanitix" for e in events)
    titles = [e.title for e in events]
    assert "Natty Waves 2026" in titles


_MINIMAL_EVENT_HTML = (
    '<script type="application/ld+json">'
    '{"@type":"ItemList","itemListElement":[{"@type":"ListItem","position":1,'
    '"item":{"@type":"Event","name":"Probe Gig",'
    '"startDate":"2026-12-31T09:00:00.000Z",'
    '"location":{"@type":"Place","name":"Probe Venue"},'
    '"url":"https://events.humanitix.com/probe"}}]}'
    "</script>"
)


@pytest.mark.asyncio
async def test_humanitix_builds_slug_and_category_url():
    source = Source(
        kind="humanitix", spec={"slug": "au--tas--hobart", "category": "music"}
    )
    expected = "https://humanitix.com/au/events/au--tas--hobart/music"
    with aioresponses() as m:
        m.get(expected, body=_MINIMAL_EVENT_HTML, content_type="text/html")
        async with aiohttp.ClientSession() as s:
            prov = HumanitixProvider(s, cache_ttl_seconds=0)
            result = await prov.fetch(source, days=365, limit=10)
    assert result.error is None
    assert [e.title for e in result.events] == ["Probe Gig"]


@pytest.mark.asyncio
async def test_humanitix_works_without_category():
    source = Source(kind="humanitix", spec={"slug": "au--tas--hobart"})
    expected = "https://humanitix.com/au/events/au--tas--hobart"
    with aioresponses() as m:
        m.get(expected, body=_MINIMAL_EVENT_HTML, content_type="text/html")
        async with aiohttp.ClientSession() as s:
            prov = HumanitixProvider(s, cache_ttl_seconds=0)
            result = await prov.fetch(source, days=365, limit=10)
    assert result.error is None
    assert len(result.events) == 1
