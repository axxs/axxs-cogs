"""Live-network tests against the real source sites.

Skipped by default. Enable individually:
    GIGLIST_TASGUIDE_LIVE=1 pytest tests/test_giglist_live.py
    GIGLIST_HUMANITIX_LIVE=1 pytest tests/test_giglist_live.py

These exist to catch upstream layout/JSON-LD changes that mocked fixture
tests cannot. Run periodically rather than on every commit."""

import os

import aiohttp
import pytest

from giglist.models import Source
from giglist.providers.humanitix import HumanitixProvider
from giglist.providers.tasguide import TasguideProvider


@pytest.mark.skipif(
    os.environ.get("GIGLIST_TASGUIDE_LIVE") != "1",
    reason="live network test. Set GIGLIST_TASGUIDE_LIVE=1 to enable",
)
@pytest.mark.asyncio
async def test_live_tasguide_returns_events():
    source = Source(kind="tasguide", spec={"path": "category/music"})
    async with aiohttp.ClientSession() as s:
        prov = TasguideProvider(s, cache_ttl_seconds=0)
        result = await prov.fetch(source, days=90, limit=10)
    assert result.error is None, f"live fetch errored: {result.error}"
    assert result.events, (
        "live fetch returned zero events. Tasguide layout may have changed. "
        f"warnings={result.warnings}"
    )


@pytest.mark.skipif(
    os.environ.get("GIGLIST_HUMANITIX_LIVE") != "1",
    reason="live network test. Set GIGLIST_HUMANITIX_LIVE=1 to enable",
)
@pytest.mark.asyncio
async def test_live_humanitix_returns_events():
    source = Source(
        kind="humanitix", spec={"slug": "au--tas--hobart", "category": "music"}
    )
    async with aiohttp.ClientSession() as s:
        prov = HumanitixProvider(s, cache_ttl_seconds=0)
        result = await prov.fetch(source, days=180, limit=10)
    assert result.error is None, f"live fetch errored: {result.error}"
    assert result.events, (
        "live fetch returned zero events. Humanitix layout may have changed. "
        f"warnings={result.warnings}"
    )
