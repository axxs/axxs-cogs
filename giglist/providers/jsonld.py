from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..models import Event

# Tasmanian gigs: Tasguide emits naive local datetimes (e.g. "2026-05-26T19:00:00")
# and Humanitix emits UTC ("...Z"). We normalise everything to tz-aware *local*
# time so the rendered date/time matches what a Hobart user expects (a late gig
# at 00:30 stays on its real calendar day) while instant-based comparisons in
# the aggregator/filter still work across mixed sources.
try:  # stdlib on 3.9+, but tzdata may be absent on minimal systems
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Australia/Hobart")
except Exception:  # pragma: no cover - fallback when tzdata unavailable
    LOCAL_TZ = timezone(timedelta(hours=10))  # AEST


# Permissive: Humanitix wraps JSON-LD in tags with extra attributes
# (id="itemlist-json-ld", data-next-head=""), which the stricter
# eventbrite-exact regex (`type="application/ld+json">`) would miss.
_LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def parse_datetime(value: Any, *, local_tz=LOCAL_TZ) -> Optional[datetime]:
    """Parse an ISO 8601 string to a tz-aware datetime in local_tz.

    Naive values are assumed to already be local; aware values are converted."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)
    return dt.astimezone(local_tz)


def _venue_from_location(location: Any) -> Optional[str]:
    if not isinstance(location, dict):
        return None
    name = location.get("name")
    if name:
        return str(name).strip()
    address = location.get("address")
    if isinstance(address, dict):
        parts = [
            str(address.get(k) or "")
            for k in ("streetAddress", "addressLocality", "addressRegion")
        ]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return None


def _parse_schema_event(
    item: dict, source: str, *, local_tz=LOCAL_TZ
) -> Optional[Event]:
    title = item.get("name")
    if not title:
        return None
    url = item.get("url")
    description = item.get("description")
    return Event(
        title=str(title).strip(),
        start=parse_datetime(item.get("startDate"), local_tz=local_tz),
        end=parse_datetime(item.get("endDate"), local_tz=local_tz),
        venue=_venue_from_location(item.get("location")),
        url=url if isinstance(url, str) else None,
        source=source,
        description=description if isinstance(description, str) else None,
    )


def _collect_events(data: Any, source: str, *, local_tz, out: list[Event]) -> None:
    """Walk a decoded JSON-LD value, appending any schema.org Events found.

    Handles a bare Event, an ItemList of ListItem→item, a top-level list, and
    an @graph wrapper — the shapes seen across Tasguide and Humanitix."""
    if isinstance(data, list):
        for element in data:
            _collect_events(element, source, local_tz=local_tz, out=out)
        return
    if not isinstance(data, dict):
        return

    node_type = data.get("@type")
    if node_type == "Event":
        parsed = _parse_schema_event(data, source, local_tz=local_tz)
        if parsed:
            out.append(parsed)
        return
    if node_type == "ItemList":
        for element in data.get("itemListElement", []):
            if isinstance(element, dict):
                _collect_events(
                    element.get("item"), source, local_tz=local_tz, out=out
                )
        return
    if "@graph" in data:
        _collect_events(data["@graph"], source, local_tz=local_tz, out=out)


def parse_jsonld_events(
    html: str, source: str, *, local_tz=LOCAL_TZ
) -> tuple[list[Event], list[str]]:
    """Parse schema.org Event JSON-LD out of a page into (events, warnings)."""
    events: list[Event] = []
    blocks_found = 0
    for match in _LD_JSON_RE.finditer(html or ""):
        blocks_found += 1
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        _collect_events(data, source, local_tz=local_tz, out=events)

    warnings: list[str] = []
    if not events:
        if blocks_found == 0:
            warnings.append(
                f"{source} returned no event data (page may be blocked, "
                "rate-limited, or restructured)."
            )
        else:
            warnings.append(f"{source} returned no event data in this listing.")
    return events, warnings
