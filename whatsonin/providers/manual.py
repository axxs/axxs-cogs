from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..models import Event, ProviderResult, Source
from .base import EventProvider
from .eventbrite import filter_events

log = logging.getLogger("red.whatsonin")


def _parse_iso(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def deserialize_event(raw: dict) -> Event:
    return Event(
        title=str(raw.get("title", "")).strip(),
        start=_parse_iso(raw.get("start")),
        end=_parse_iso(raw.get("end")),
        venue=raw.get("venue") or None,
        url=raw.get("url") or None,
        source="manual",
        description=raw.get("description") or None,
    )


def serialize_event(event: Event) -> dict:
    def iso(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    return {
        "title": event.title,
        "start": iso(event.start),
        "end": iso(event.end),
        "venue": event.venue,
        "url": event.url,
        "description": event.description,
    }


class ManualProvider(EventProvider):
    kind = "manual"
    name = "manual"

    def __init__(self, *args, **kwargs):  # accept session etc. for symmetric API
        pass

    async def fetch(
        self, source: Source, *, days: int, limit: int
    ) -> ProviderResult:
        raw_events = source.spec.get("events") or []
        parsed: list[Event] = []
        warnings: list[str] = []

        for i, entry in enumerate(raw_events):
            try:
                ev = deserialize_event(entry)
            except Exception as exc:
                warnings.append(f"manual entry {i}: {exc}")
                continue
            if not ev.title:
                warnings.append(f"manual entry {i}: missing title; skipped")
                continue
            parsed.append(ev)

        return ProviderResult(
            events=filter_events(parsed, days=days, limit=limit),
            warnings=warnings,
        )
