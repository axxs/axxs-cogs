from datetime import datetime, timezone

from giglist.aggregator import merge_events
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
