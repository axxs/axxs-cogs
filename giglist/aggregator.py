from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from .models import Event


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a datetime to tz-aware UTC. Naive datetimes from one provider
    can't be compared with aware datetimes from another without this. Only
    safe for sort/comparison — display rendering relies on the original
    local-tz aware value (see jsonld.parse_datetime)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _normalise_now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _sort_by_date(events: list[Event], now: datetime) -> list[Event]:
    """Sort events by start date ascending. In-progress events (started in
    the past, end still in the future) sort as if they start *now*, so they
    interleave with genuinely-upcoming events rather than sinking to the
    bottom under their past start date. Date-TBA events sort last."""
    sentinel = datetime.max.replace(tzinfo=timezone.utc)

    def sort_key(event: Event) -> tuple:
        start = _as_aware(event.start)
        if start is None:
            return (True, sentinel)
        end = _as_aware(event.end)
        if start < now and end is not None and end >= now:
            return (False, now)
        return (False, start)

    return sorted(events, key=sort_key)


def _dedupe_into(events: Iterable[Event], seen: set, out: list[Event]) -> None:
    for event in events:
        key = event.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(event)


def merge_events(
    events: list[Event], limit: int, *, now: Optional[datetime] = None
) -> list[Event]:
    """Dedupe, sort by start date, cap. The single-scope merge used when
    every source contributes equally (e.g. the statewide 'tasmania' place)."""
    now = _normalise_now(now)
    seen: set[tuple] = set()
    unique: list[Event] = []
    _dedupe_into(events, seen, unique)
    return _sort_by_date(unique, now)[:limit]


def merge_with_scope_priority(
    *,
    local: list[Event],
    statewide: list[Event],
    limit: int,
    now: Optional[datetime] = None,
) -> list[Event]:
    """Build the merged listing for places that mix `local` and
    `statewide` sources.

    Local events are *guaranteed slots* — a Launceston-tagged gig three
    weeks out is never crowded out by a flood of Hobart gigs tomorrow.
    Statewide events fill the remaining slots after local is reserved.
    The final output is then sorted by date so the user reads a clean
    chronological list with no jarring date jumps between scopes.

    Dedupe gives priority to local: a gig present in both lists shows
    once, attributed to the local source."""
    now = _normalise_now(now)
    seen: set[tuple] = set()
    local_unique: list[Event] = []
    statewide_unique: list[Event] = []
    _dedupe_into(local, seen, local_unique)
    _dedupe_into(statewide, seen, statewide_unique)

    # Reserve up to `limit` slots for local (the guarantee). Fill any
    # remainder from statewide, taking the soonest events first so a long
    # tail of statewide gigs doesn't push out the imminent ones.
    sorted_local = _sort_by_date(local_unique, now)
    sorted_statewide = _sort_by_date(statewide_unique, now)
    local_kept = sorted_local[:limit]
    remaining = max(0, limit - len(local_kept))
    statewide_kept = sorted_statewide[:remaining]

    # Final order is chronological across both scopes.
    return _sort_by_date(local_kept + statewide_kept, now)
