"""Unit tests for the giglist source-aggregation orchestration.

The Giglist cog class itself can't be instantiated under test (Red's
`commands.Cog` is MagicMocked by conftest, so subclassing breaks), so the
loop body is extracted into a module-level `gather_events_for_place`
function and the cog method becomes a thin wrapper. These tests exercise
the function directly with stubbed providers."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from giglist.giglist import _build_scope_note, gather_events_for_place
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
async def test_gather_events_for_place_carries_source_scope_in_diag():
    """diag entries must include each source's `scope` so the cog can
    compute when to show the 'no Place-specific gigs' note."""
    local_src = Source(kind="humanitix", spec={"slug": "x"})  # default scope=local
    statewide_src = Source(
        kind="tasguide", spec={"path": "category/music"}, scope="statewide"
    )
    place = Place(
        key="z", display_name="Z", sources=(local_src, statewide_src)
    )
    no_events = ProviderResult(events=[], warnings=[])
    some = ProviderResult(events=[_future_event("Wide", source="tasguide")], warnings=[])
    get_provider, source_enabled = _stubbed_lookups(
        {
            "humanitix": _stub_provider("humanitix", fetch_result=no_events),
            "tasguide": _stub_provider("tasguide", fetch_result=some),
        }
    )

    _, _, diag = await gather_events_for_place(
        place,
        get_provider=get_provider,
        source_enabled=source_enabled,
        days=30,
        limit=10,
        now=NOW,
    )
    assert diag[0]["scope"] == "local"
    assert diag[1]["scope"] == "statewide"


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
async def test_gather_events_for_place_guarantees_local_slot_against_statewide_volume():
    """The user's Launceston bug: a Launceston gig 3 weeks out must not be
    crowded out by a flood of imminent Hobart gigs. The result is
    chronologically sorted but local is reserved its slot."""
    local_src = Source(kind="humanitix", spec={"slug": "x"}, scope="local")
    statewide_src = Source(
        kind="tasguide", spec={"path": "y"}, scope="statewide"
    )
    place = Place(
        key="z", display_name="Z", sources=(local_src, statewide_src)
    )

    local_far = Event(
        "Local 3 weeks", datetime(2026, 6, 20, 19, tzinfo=timezone.utc),
        None, "L Venue", "https://x", "humanitix",
    )
    # 15 unique statewide events sooner than the local one — without
    # the guarantee, these would crowd out the local event entirely.
    statewides = [
        Event(
            f"Hobart Gig #{i}",
            datetime(2026, 5, 27, 18, i, tzinfo=timezone.utc),
            None, "Venue", "https://x", "tasguide",
        )
        for i in range(15)
    ]
    get_provider, source_enabled = _stubbed_lookups(
        {
            "humanitix": _stub_provider(
                "humanitix",
                fetch_result=ProviderResult(events=[local_far], warnings=[]),
            ),
            "tasguide": _stub_provider(
                "tasguide",
                fetch_result=ProviderResult(events=statewides, warnings=[]),
            ),
        }
    )

    events, _, _ = await gather_events_for_place(
        place,
        get_provider=get_provider,
        source_enabled=source_enabled,
        days=90,
        limit=10,
        now=datetime(2026, 5, 26, tzinfo=timezone.utc),
    )
    titles = [e.title for e in events]
    assert "Local 3 weeks" in titles  # guaranteed slot
    assert len(events) == 10
    # Output is chronological: earliest first
    starts = [e.start for e in events]
    assert starts == sorted(starts)


@pytest.mark.asyncio
async def test_gather_events_for_place_uses_prefetch_headroom():
    """Per-source prefetch must be larger than the user's display `limit`
    so the priority merge has events to actually choose from. Otherwise
    statewide could return its first N chronological events and crowd
    out a small-town local event we haven't even fetched yet."""
    captured = {}

    async def get_provider(kind):
        prov = MagicMock()
        prov.name = kind
        prov.cache_age_seconds = MagicMock(return_value=None)

        async def fetch(source, *, days, limit):
            captured["limit"] = limit
            return ProviderResult(events=[], warnings=[])

        prov.fetch = fetch
        return prov

    async def source_enabled(kind):
        return True

    place = Place(
        key="z", display_name="Z",
        sources=(Source(kind="tasguide", spec={"path": "x"}, scope="statewide"),),
    )
    await gather_events_for_place(
        place,
        get_provider=get_provider,
        source_enabled=source_enabled,
        days=30,
        limit=10,
        now=NOW,
    )
    # The actual user-facing limit is 10; provider must see materially more
    assert captured["limit"] >= 30, f"prefetch limit too low: {captured['limit']}"


def test_build_scope_note_returns_note_when_local_empty_but_statewide_has_events():
    place = Place(key="devonport", display_name="Devonport", sources=())
    diag = {
        0: {"scope": "statewide", "events": 10, "error": None},
        1: {"scope": "local", "events": 0, "error": None},
        2: {"scope": "local", "events": 0, "error": None},
    }
    note = _build_scope_note(place, diag, days=30)
    assert note is not None
    assert "Devonport-specific" in note
    assert "30 days" in note
    assert "wider" in note


def test_build_scope_note_returns_none_when_local_has_events():
    place = Place(key="hobart", display_name="Hobart", sources=())
    diag = {
        0: {"scope": "statewide", "events": 5, "error": None},
        1: {"scope": "local", "events": 3, "error": None},
    }
    assert _build_scope_note(place, diag, days=30) is None


def test_build_scope_note_returns_none_for_statewide_only_place():
    """The `tasmania` (statewide) place has no local sources — must not
    trigger a 'no Tasmania-specific gigs' note."""
    place = Place(key="tasmania", display_name="Tasmania", sources=())
    diag = {
        0: {"scope": "statewide", "events": 10, "error": None},
        1: {"scope": "statewide", "events": 2, "error": None},
    }
    assert _build_scope_note(place, diag, days=30) is None


def test_build_scope_note_returns_none_when_local_errored():
    """A local source that errored out shouldn't be counted as 'local
    found nothing' — its real status is unknown."""
    place = Place(key="devonport", display_name="Devonport", sources=())
    diag = {
        0: {"scope": "statewide", "events": 5, "error": None},
        1: {"scope": "local", "events": 0, "error": "humanitix 503"},
    }
    # No successful local sources, so we don't trigger the note
    assert _build_scope_note(place, diag, days=30) is None


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
