from __future__ import annotations

from datetime import datetime, timezone

from .models import Event


def _as_aware(dt):
    """Coerce a datetime to tz-aware UTC. Naive datetimes from one provider
    (e.g. Eventbrite all-day events) can't be compared with aware datetimes
    from another (RSS, ICS) without this."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def merge_events(events: list[Event], limit: int) -> list[Event]:
    """Dedupe, sort by start date, and cap results."""
    seen: set[tuple] = set()
    unique: list[Event] = []

    for event in events:
        key = event.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)

    sentinel = datetime.max.replace(tzinfo=timezone.utc)
    unique.sort(
        key=lambda e: (
            e.start is None,
            _as_aware(e.start) or sentinel,
        )
    )
    return unique[:limit]
