from datetime import datetime, timezone

from giglist.aggregator import merge_events, merge_with_scope_priority
from giglist.models import Event


def _ev(title, start, end=None, venue=None, source="tasguide"):
    return Event(title, start, end, venue, None, source)


def test_merge_dedupes_by_title_date_venue():
    a = _ev("Duplicate", datetime(2026, 6, 2, tzinfo=timezone.utc), venue="Venue")
    b = _ev("Duplicate", datetime(2026, 6, 2, tzinfo=timezone.utc), venue="Venue", source="humanitix")
    c = _ev("Other", datetime(2026, 6, 3, tzinfo=timezone.utc))
    merged = merge_events([a, b, c], limit=10)
    assert len(merged) == 2


def test_merge_sorts_ascending_by_start():
    later = _ev("Later", datetime(2026, 6, 5, tzinfo=timezone.utc))
    earlier = _ev("Earlier", datetime(2026, 6, 1, tzinfo=timezone.utc))
    merged = merge_events([later, earlier], limit=10)
    assert [e.title for e in merged] == ["Earlier", "Later"]


def test_merge_in_progress_event_sorts_as_now_not_past_start():
    now = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
    on_now = _ev(
        "Ongoing exhibition",
        datetime(2026, 3, 7, tzinfo=timezone.utc),
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        source="humanitix",
    )
    tomorrow = _ev("Tomorrow", datetime(2026, 5, 27, tzinfo=timezone.utc))
    next_week = _ev("Next week", datetime(2026, 5, 28, tzinfo=timezone.utc))
    merged = merge_events([next_week, tomorrow, on_now], limit=10, now=now)
    assert [e.title for e in merged] == ["Ongoing exhibition", "Tomorrow", "Next week"]


def test_merge_respects_limit():
    events = [
        _ev(f"Gig {i}", datetime(2026, 6, i + 1, tzinfo=timezone.utc)) for i in range(5)
    ]
    assert len(merge_events(events, limit=2)) == 2


def test_merge_handles_mixed_naive_and_aware_datetimes_without_raising():
    naive = _ev("Naive", datetime(2026, 6, 1))
    aware = _ev("Aware", datetime(2026, 6, 2, tzinfo=timezone.utc))
    merged = merge_events([aware, naive], limit=10)
    assert [e.title for e in merged] == ["Naive", "Aware"]


# --- merge_with_scope_priority -------------------------------------------

NOW = datetime(2026, 5, 26, tzinfo=timezone.utc)


def test_local_event_is_guaranteed_a_slot_when_crowded_by_statewide():
    """The user's Launceston issue: a Launceston-tagged gig 3 weeks out
    must NOT be crowded out by a flood of imminent Hobart gigs. Local
    is reserved its slot in the result set."""
    local_far = _ev(
        "Local 3 weeks out", datetime(2026, 6, 20, tzinfo=timezone.utc),
        source="humanitix",
    )
    # 15 unique statewide events all sooner than the local one
    statewides = [
        _ev(
            f"Hobart Gig #{i}",
            datetime(2026, 5, 27, 18, i, tzinfo=timezone.utc),
            source="tasguide",
        )
        for i in range(15)
    ]
    result = merge_with_scope_priority(
        local=[local_far], statewide=statewides, limit=10, now=NOW
    )
    assert len(result) == 10
    assert "Local 3 weeks out" in [e.title for e in result]


def test_scope_priority_output_is_chronologically_sorted():
    """No jarring date jumps between scopes in the rendered list.
    Statewide events that fall before the local one appear above it."""
    local_late = _ev(
        "Local June", datetime(2026, 6, 20, tzinfo=timezone.utc), source="humanitix"
    )
    state_early = _ev(
        "State May 27", datetime(2026, 5, 27, tzinfo=timezone.utc), source="tasguide"
    )
    state_mid = _ev(
        "State June 5", datetime(2026, 6, 5, tzinfo=timezone.utc), source="tasguide"
    )
    result = merge_with_scope_priority(
        local=[local_late], statewide=[state_early, state_mid],
        limit=10, now=NOW,
    )
    assert [e.title for e in result] == ["State May 27", "State June 5", "Local June"]


def test_scope_priority_dedupes_giving_priority_to_local():
    """A gig that comes from both a local and a statewide source — e.g.
    'Georgia Maq Returns @ Altar Bar' returned by both humanitix Hobart
    and TM city=Hobart — appears once, attributed to local."""
    e_local = Event(
        "Same Gig", datetime(2026, 6, 1, 19, tzinfo=timezone.utc),
        None, "Venue", None, "humanitix",
    )
    e_state = Event(
        "Same Gig", datetime(2026, 6, 1, 19, tzinfo=timezone.utc),
        None, "Venue", None, "tasguide",
    )
    result = merge_with_scope_priority(local=[e_local], statewide=[e_state], limit=10, now=NOW)
    assert len(result) == 1
    assert result[0].source == "humanitix"


def test_scope_priority_respects_limit_filling_local_first():
    locals_ = [
        _ev(f"L{i}", datetime(2026, 6, i + 1, tzinfo=timezone.utc), source="humanitix")
        for i in range(5)
    ]
    states = [
        _ev(f"S{i}", datetime(2026, 6, i + 1, tzinfo=timezone.utc), source="tasguide")
        for i in range(5)
    ]
    result = merge_with_scope_priority(local=locals_, statewide=states, limit=3, now=NOW)
    assert len(result) == 3
    # All 3 are local because local fills first
    assert all(e.title.startswith("L") for e in result)


def test_scope_priority_empty_local_falls_through_to_statewide():
    """When local is empty (the small-town case), statewide fills the slot."""
    states = [_ev("Wide", datetime(2026, 6, 1, tzinfo=timezone.utc), source="tasguide")]
    result = merge_with_scope_priority(local=[], statewide=states, limit=10, now=NOW)
    assert [e.title for e in result] == ["Wide"]
