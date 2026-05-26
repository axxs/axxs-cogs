from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import Event, Place

DESCRIPTION_MAX = 4000

# Distinct unicode superscripts so a glance at a line tells you the source.
SOURCE_MARKERS = {
    "tasguide": "ᵀ",
    "humanitix": "ᴴ",
    "ticketmaster": "ᵐ",
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


def _is_on_now(event: Event, now: datetime) -> bool:
    if event.start is None or event.end is None:
        return False
    start = event.start if event.start.tzinfo else event.start.replace(tzinfo=timezone.utc)
    end = event.end if event.end.tzinfo else event.end.replace(tzinfo=timezone.utc)
    return start < now <= end


def format_event_line(event: Event, now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if _is_on_now(event, now):
        # Show the gig as currently on rather than its past start date.
        parts = [_format_date(now), "on now", f"ends {_format_relative(event.end)}"]
    else:
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


def _render_empty(place: Place, days: int, warnings: Optional[list] = None) -> dict:
    description = (
        f"No gigs in the next {days} days. Try `--days 60`, or check "
        f"`[p]giglistplaces` for other places."
    )
    if warnings:
        # Empty results with warnings mean the silence is upstream-broken,
        # not "quiet weekend" — say so explicitly and point at diag.
        n = len(warnings)
        suffix = "" if n == 1 else "s"
        description += (
            f"\n⚠ {n} source issue{suffix}. "
            f"`[p]giglistdiag {place.key}` for details."
        )
    description += (
        "\n_`[p]help giglist` for flags · `[p]giglistplaces` for other places._"
    )
    return {
        "title": f"Gigs in {place.display_name}",
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
    scope_note: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Return a dict ready to splat into discord.Embed(**...).

    `scope_note`: when set, rendered above the event list. Used by the cog
    to say "No <Place>-specific gigs; showing wider listings below" when
    every local source returned 0 but statewide sources had events."""
    if not events:
        return _render_empty(place, days, warnings=warnings)
    if now is None:
        now = datetime.now(timezone.utc)

    header = f"_next {days} days_\n"
    if scope_note:
        header += f"{scope_note}\n"

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
            f"\n⚠ {n} issue{suffix}. `[p]giglistdiag {place.key}` for details."
        )

    footer += "\n_`[p]help giglist` for flags · `[p]giglistplaces` for other places._"

    truncation_template = "\n…and {n} more. Try `--limit 30`."
    reserve = len(truncation_template.format(n=len(events)))
    budget_for_events = DESCRIPTION_MAX - len(header) - len(footer) - reserve

    body = _render_body(place, events, now=now, budget=budget_for_events)
    description = header + body + footer
    return {
        "title": f"Gigs in {place.display_name}",
        "description": description,
    }


def _render_body(place: Place, events: list, *, now: datetime, budget: int) -> str:
    """Emit either a flat date-sorted list (when all events share one
    scope, or are un-scoped) or two clearly-labelled sections — `**Local
    in <Place>** _(N)_` followed by `**Wider Tasmania** _(N)_` — when
    both scopes are present. Section ordering is local → wider so a
    glance shows what's actually in the place first."""
    local_events = [e for e in events if getattr(e, "scope", None) == "local"]
    statewide_events = [e for e in events if getattr(e, "scope", None) == "statewide"]
    sectioned = bool(local_events) and bool(statewide_events)

    if sectioned:
        sections = [
            (f"**Local in {place.display_name}** _({len(local_events)})_", local_events),
            (f"**Wider Tasmania** _({len(statewide_events)})_", statewide_events),
        ]
    else:
        sections = [("", events)]

    body_parts: list[str] = []
    used = 0
    rendered = 0
    truncated = False
    for i, (heading, group) in enumerate(sections):
        if truncated:
            break
        if heading:
            block = ("\n" if i > 0 else "") + heading
            if used + len(block) > budget:
                truncated = True
                break
            body_parts.append(block)
            used += len(block) + 1  # +1 for the join "\n"
        for event in group:
            line = format_event_line(event, now=now)
            if used + len(line) + 1 > budget:
                truncated = True
                break
            body_parts.append(line)
            used += len(line) + 1
            rendered += 1

    body = "\n".join(body_parts)
    remaining = len(events) - rendered
    if remaining > 0:
        body += "\n…and {n} more. Try `--limit 30`.".format(n=remaining)
    return body
