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


def merge_events(
    events: list[Event], limit: int, *, now: datetime | None = None
) -> list[Event]:
    """Dedupe, sort by start date, and cap results.

    Events already under way (started in the past, end still in the future)
    sort as if they start today, so they interleave with genuinely-upcoming
    events instead of sinking to the bottom under their past start date."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    seen: set[tuple] = set()
    unique: list[Event] = []

    for event in events:
        key = event.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)

    sentinel = datetime.max.replace(tzinfo=timezone.utc)

    def sort_key(event: Event) -> tuple:
        start = _as_aware(event.start)
        if start is None:
            return (True, sentinel)
        end = _as_aware(event.end)
        if start < now and end is not None and end >= now:
            return (False, now)
        return (False, start)

    unique.sort(key=sort_key)
    return unique[:limit]
