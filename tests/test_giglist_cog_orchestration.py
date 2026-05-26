"""Unit tests for the giglist source-aggregation orchestration.

The Giglist cog class itself can't be instantiated under test (Red's
`commands.Cog` is MagicMocked by conftest, so subclassing breaks), so the
loop body is extracted into a module-level `gather_events_for_place`
function and the cog method becomes a thin wrapper. These tests exercise
the function directly with stubbed providers."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from giglist.giglist import gather_events_for_place
from giglist.models import Event, Place, ProviderResult, Source


def _stubbed_lookups(providers_by_kind, *, enable_flags=None):
    """Return (get_provider, source_enabled) coroutines for injection."""
    flags = enable_flags or {}

    async def get_provider(kind):
        return providers_by_kind.get(kind)

    async def source_enabled(kind):
        return flags.get(kind, True)

    return get_provider, source_enabled


def _stub_provider(name, *, fetch_result=None, fetch_exc=None):
    prov = MagicMock()
    prov.name = name
    if fetch_exc is not None:
        prov.fetch = AsyncMock(side_effect=fetch_exc)
    else:
        prov.fetch = AsyncMock(return_value=fetch_result)
    prov.cache_age_seconds = MagicMock(return_value=None)
    return prov


def _future_event(title, source="humanitix"):
    return Event(
        title=title,
        start=datetime(2099, 1, 1, 12, tzinfo=timezone.utc),
        end=None,
        venue="V",
        url="https://x",
        source=source,
    )


NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fetch_for_place_isolates_provider_crash_and_continues():
    """A provider raising an unexpected exception must NOT prevent the
    other sources from being fetched; the crash must surface as a warning
    and a diag entry, with subsequent providers' events preserved."""
    crashing = _stub_provider("tasguide", fetch_exc=RuntimeError("blew up"))
    working = _stub_provider(
        "humanitix",
        fetch_result=ProviderResult(events=[_future_event("Survivor")], warnings=[]),
    )
    get_provider, source_enabled = _stubbed_lookups(
        {"tasguide": crashing, "humanitix": working}
    )
    place = Place(
        key="test",
        display_name="Test",
        sources=(
            Source(kind="tasguide", spec={"path": "category/music"}),
            Source(kind="humanitix", spec={"slug": "x"}),
        ),
    )

    events, warnings, diag = await gather_events_for_place(
        place,
        get_provider=get_provider,
        source_enabled=source_enabled,
        days=90,
        limit=10,
        now=NOW,
    )

    assert [e.title for e in events] == ["Survivor"]
    assert diag[0]["error"] is not None and "tasguide" in diag[0]["error"].lower()
    assert diag[1]["events"] == 1
    assert any("tasguide" in w.lower() for w in warnings)


@pytest.mark.asyncio
async def test_fetch_for_place_aggregates_per_source_errors_without_crashing():
    """Two providers returning ProviderResult.error and one succeeding —
    the successful events still come through, both errors land in diag."""
    erroring_1 = _stub_provider(
        "tasguide",
        fetch_result=ProviderResult(events=[], warnings=[], error="blocked"),
    )
    erroring_2 = _stub_provider(
        "humanitix",
        fetch_result=ProviderResult(events=[], warnings=[], error="403"),
    )
    working = _stub_provider(
        "ticketmaster",
        fetch_result=ProviderResult(
            events=[_future_event("Touring Show", source="ticketmaster")], warnings=[]
        ),
    )
    get_provider, source_enabled = _stubbed_lookups(
        {
            "tasguide": erroring_1,
            "humanitix": erroring_2,
            "ticketmaster": working,
        }
    )
    place = Place(
        key="test",
        display_name="Test",
        sources=(
            Source(kind="tasguide", spec={}),
            Source(kind="humanitix", spec={"slug": "x"}),
            Source(kind="ticketmaster", spec={"dmaId": "707"}),
        ),
    )

    events, warnings, diag = await gather_events_for_place(
        place,
        get_provider=get_provider,
        source_enabled=source_enabled,
        days=90,
        limit=10,
        now=NOW,
    )
    assert [e.title for e in events] == ["Touring Show"]
    assert diag[0]["error"] == "blocked"
    assert diag[1]["error"] == "403"
    assert diag[2]["events"] == 1


@pytest.mark.asyncio
async def test_fetch_for_place_reports_disabled_source_without_calling_provider():
    """When `enable_humanitix=False`, the humanitix provider must NOT be
    invoked, and diag must show the disabled state clearly."""
    humanitix = _stub_provider(
        "humanitix",
        fetch_result=ProviderResult(events=[_future_event("Should Not Appear")], warnings=[]),
    )
    get_provider, source_enabled = _stubbed_lookups(
        {"humanitix": humanitix},
        enable_flags={"humanitix": False},
    )
    place = Place(
        key="test",
        display_name="Test",
        sources=(Source(kind="humanitix", spec={"slug": "x"}),),
    )

    events, warnings, diag = await gather_events_for_place(
        place,
        get_provider=get_provider,
        source_enabled=source_enabled,
        days=90,
        limit=10,
        now=NOW,
    )
    assert events == []
    humanitix.fetch.assert_not_called()
    assert diag[0]["error"] is not None and "disabled" in diag[0]["error"].lower()
