from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import Event, Place

DESCRIPTION_MAX = 4000

SOURCE_MARKERS = {
    "eventbrite": "ᴱ",
    "ics": "ᴵ",
    "manual": "ᴹ",
}
FALLBACK_MARKER = "◌"

TITLE_MAX = 80
VENUE_MAX = 40


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_date(start: Optional[datetime]) -> str:
    if start is None:
        return "Date TBA"
    return start.strftime("%a %-d %b")


def _format_relative(start: Optional[datetime]) -> str:
    if start is None:
        return ""
    dt = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"


def _format_title(title: str, url: Optional[str]) -> str:
    truncated = _truncate(title, TITLE_MAX)
    if url:
        return f"[{truncated}]({url})"
    return truncated


def _format_venue(venue: Optional[str]) -> str:
    if not venue:
        return ""
    return f"@ {_truncate(venue, VENUE_MAX)}"


def _source_marker(source: str) -> str:
    return SOURCE_MARKERS.get(source, FALLBACK_MARKER)


def format_source_counts(counts: dict) -> str:
    parts = [
        f"{_source_marker(name)} {name} ({n})"
        for name, n in counts.items()
        if n > 0
    ]
    return " · ".join(parts)


def format_cache_age(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    if seconds == 0:
        return "fresh"
    if seconds < 60:
        return f"cached {seconds}s ago"
    if seconds < 3600:
        return f"cached {seconds // 60} min ago"
    return f"cached {seconds // 3600} h ago"


def format_event_line(event: Event) -> str:
    parts = [_format_date(event.start)]
    rel = _format_relative(event.start)
    if rel:
        parts.append(rel)
    parts.append(_format_title(event.title, event.url))
    venue = _format_venue(event.venue)
    if venue:
        parts.append(venue)
    line = " · ".join(parts)
    return f"{line}   {_source_marker(event.source)}"


def _render_empty(place: Place, days: int) -> dict:
    description = (
        f"No events in the next {days} days. "
        f"Try `--days 60`, or add a source: "
        f"`[p]wsa source add {place.key} ics <url>`."
    )
    return {
        "title": f"What's on in {place.display_name}",
        "description": description,
    }


def render_places_listing(
    place: Place,
    events: list,
    warnings: list,
    *,
    days: int,
    source_counts: dict,
    cache_age_s: Optional[int],
    aggregated_parent: Optional[str] = None,
) -> dict:
    """Return a dict ready to splat into discord.Embed(**...)."""
    if not events:
        return _render_empty(place, days)

    header = f"_next {days} days_"
    if aggregated_parent:
        header += (
            f"\n_Showing {aggregated_parent}-wide events; no directory "
            f"available for {place.display_name}._"
        )
    header += "\n"

    footer_parts = []
    counts = format_source_counts(source_counts)
    if counts:
        footer_parts.append(counts)
    age = format_cache_age(cache_age_s)
    if age:
        footer_parts.append(age)
    footer = ("\n\n" + " · ".join(footer_parts)) if footer_parts else ""

    if warnings:
        n = len(warnings)
        suffix = "" if n == 1 else "s"
        footer += (
            f"\n⚠ {n} issue{suffix}. `[p]whatsonin diag {place.key}` for details."
        )

    truncation_template = "\n…and {n} more. Try `--limit 30`."
    reserve = len(truncation_template.format(n=len(events)))
    budget_for_events = DESCRIPTION_MAX - len(header) - len(footer) - reserve

    event_lines = []
    used = 0
    rendered = 0
    for event in events:
        line = format_event_line(event)
        if used + len(line) + 1 > budget_for_events:
            break
        event_lines.append(line)
        used += len(line) + 1
        rendered += 1

    body = "\n".join(event_lines)
    remaining = len(events) - rendered
    if remaining > 0:
        body += truncation_template.format(n=remaining)

    description = header + body + footer
    return {
        "title": f"What's on in {place.display_name}",
        "description": description,
    }
