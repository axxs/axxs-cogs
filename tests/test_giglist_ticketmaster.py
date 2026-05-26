import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from aioresponses import aioresponses

from giglist.models import Source
from giglist.providers.ticketmaster import TicketmasterProvider

SAMPLE_RESPONSE = {
    "_embedded": {
        "events": [
            {
                "name": "Sample Concert",
                "url": "https://www.ticketmaster.com.au/event/abc",
                "dates": {
                    "start": {
                        "localDate": "2026-12-31",
                        "localTime": "20:00:00",
                        "dateTime": "2026-12-31T09:00:00Z",
                    },
                    "timezone": "Australia/Hobart",
                },
                "_embedded": {
                    "venues": [
                        {"name": "Odeon Theatre", "city": {"name": "Hobart"}}
                    ]
                },
            },
            {
                "name": "Festival",
                "url": "https://www.ticketmaster.com.au/event/def",
                "dates": {"start": {"localDate": "2026-11-15"}},
                "_embedded": {"venues": [{"name": "Wrest Point"}]},
            },
        ]
    },
    "page": {"size": 100, "totalElements": 2, "totalPages": 1, "number": 0},
}

TM_URL_RE = re.compile(r"https://app\.ticketmaster\.com/discovery/v2/events\.json\?.*")


@pytest.mark.asyncio
async def test_ticketmaster_disabled_without_api_key_returns_clean_error():
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    async with aiohttp.ClientSession() as s:
        prov = TicketmasterProvider(s, api_key="", country="AU", cache_ttl_seconds=0)
        result = await prov.fetch(source, days=30, limit=10)
    assert result.events == []
    assert result.error is not None
    assert "api key" in result.error.lower()


@pytest.mark.asyncio
async def test_ticketmaster_fetches_and_parses_events():
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    with aioresponses() as m:
        m.get(TM_URL_RE, payload=SAMPLE_RESPONSE, content_type="application/json")
        async with aiohttp.ClientSession() as s:
            prov = TicketmasterProvider(
                s, api_key="dummy-key", country="AU", cache_ttl_seconds=0
            )
            result = await prov.fetch(source, days=365, limit=10)
    assert result.error is None
    titles = [e.title for e in result.events]
    assert "Sample Concert" in titles
    assert "Festival" in titles
    venues = {e.title: e.venue for e in result.events}
    assert venues["Sample Concert"] == "Odeon Theatre"
    assert all(e.source == "ticketmaster" for e in result.events)


@pytest.mark.asyncio
async def test_ticketmaster_url_includes_required_query_params():
    """The Discovery API call must carry apikey, classificationName=music,
    countryCode=AU and the spec-driven location param (e.g. dmaId=707)."""
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    captured = {}
    with aioresponses() as m:
        m.get(TM_URL_RE, payload=SAMPLE_RESPONSE, content_type="application/json")
        async with aiohttp.ClientSession() as s:
            prov = TicketmasterProvider(
                s, api_key="dummy-key", country="AU", cache_ttl_seconds=0
            )
            await prov.fetch(source, days=30, limit=10)
        # Inspect the actual URL that was requested
        for (method, url), calls in m.requests.items():
            if "ticketmaster" in str(url):
                captured["url"] = str(url)
                break
    assert "url" in captured, f"expected a ticketmaster call, got {list(m.requests.keys())}"
    qs = parse_qs(urlparse(captured["url"]).query)
    assert qs.get("apikey") == ["dummy-key"]
    assert qs.get("classificationName") == ["music"]
    assert qs.get("countryCode") == ["AU"]
    assert qs.get("dmaId") == ["707"]


_SECRET_KEY = "SUPER-SECRET-DO-NOT-LEAK-1234"


@pytest.mark.asyncio
async def test_ticketmaster_redacts_apikey_in_error_on_http_failure():
    """A 4xx/5xx from TM must NOT echo the apikey into ProviderResult.error,
    which the cog forwards to the Discord channel + diag command."""
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    with aioresponses() as m:
        m.get(TM_URL_RE, status=401, payload={"fault": "invalid key"})
        async with aiohttp.ClientSession() as s:
            prov = TicketmasterProvider(
                s, api_key=_SECRET_KEY, country="AU", cache_ttl_seconds=0
            )
            result = await prov.fetch(source, days=30, limit=10)
    assert result.error is not None
    assert _SECRET_KEY not in result.error, (
        f"API key leaked into error string: {result.error}"
    )


@pytest.mark.asyncio
async def test_ticketmaster_redacts_apikey_in_warning_log_on_http_failure(caplog):
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    with caplog.at_level(logging.WARNING, logger="red.giglist"):
        with aioresponses() as m:
            m.get(TM_URL_RE, status=401, payload={"fault": "invalid key"})
            async with aiohttp.ClientSession() as s:
                prov = TicketmasterProvider(
                    s, api_key=_SECRET_KEY, country="AU", cache_ttl_seconds=0
                )
                await prov.fetch(source, days=30, limit=10)
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert _SECRET_KEY not in messages, f"API key leaked into logs: {messages}"


@pytest.mark.asyncio
async def test_ticketmaster_no_key_error_points_at_actual_cog_command():
    """The 'no key configured' error must reference the real `[p]giglistset
    ticketmaster_key` subcommand, not the imaginary `[p]set ticketmaster_api_key`."""
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    async with aiohttp.ClientSession() as s:
        prov = TicketmasterProvider(s, api_key="", country="AU", cache_ttl_seconds=0)
        result = await prov.fetch(source, days=30, limit=10)
    assert result.error is not None
    assert "giglistset ticketmaster_key" in result.error
    assert "[p]set ticketmaster_api_key" not in result.error


@pytest.mark.asyncio
async def test_ticketmaster_uses_local_date_when_no_datetime():
    """Some events lack the precise ISO `dateTime`. Falling back to
    `localDate` (date-only) must still produce a usable Event with a date."""
    payload = {
        "_embedded": {
            "events": [
                {
                    "name": "Date-only Show",
                    "url": "https://x/y",
                    "dates": {"start": {"localDate": "2026-10-10"}},
                    "_embedded": {"venues": [{"name": "Venue"}]},
                }
            ]
        }
    }
    source = Source(kind="ticketmaster", spec={"dmaId": "707"})
    with aioresponses() as m:
        m.get(TM_URL_RE, payload=payload, content_type="application/json")
        async with aiohttp.ClientSession() as s:
            prov = TicketmasterProvider(
                s, api_key="dummy", country="AU", cache_ttl_seconds=0
            )
            result = await prov.fetch(source, days=365, limit=10)
    assert result.error is None
    assert len(result.events) == 1
    assert result.events[0].start is not None
    assert result.events[0].start.date().isoformat() == "2026-10-10"
