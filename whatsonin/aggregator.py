from __future__ import annotations

from datetime import datetime, timezone

from .models import Event


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

    unique.sort(
        key=lambda e: (
            e.start is None,
            e.start or datetime.max.replace(tzinfo=timezone.utc),
        )
    )
    return unique[:limit]
