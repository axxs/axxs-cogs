from datetime import datetime, timezone

from giglist.models import Event, Place, Source
from giglist.render import (
    SOURCE_MARKERS,
    format_event_line,
    format_source_counts,
    render_places_listing,
)


def _place(key="hobart", display="Hobart"):
    return Place(
        key=key,
        display_name=display,
        sources=(Source(kind="tasguide", spec={"path": "category/music"}),),
    )


def test_source_markers_exist_for_all_three_kinds():
    assert SOURCE_MARKERS["tasguide"]
    assert SOURCE_MARKERS["humanitix"]
    assert SOURCE_MARKERS["ticketmaster"]
    # Markers are distinct so a glance at the line tells you the source.
    assert (
        len({SOURCE_MARKERS["tasguide"], SOURCE_MARKERS["humanitix"], SOURCE_MARKERS["ticketmaster"]})
        == 3
    )


def test_format_event_line_includes_title_venue_and_marker():
    event = Event(
        title="Test Gig",
        start=datetime(2026, 7, 1, 19, tzinfo=timezone.utc),
        end=None,
        venue="Republic Bar",
        url="https://x",
        source="tasguide",
    )
    line = format_event_line(event, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert "Test Gig" in line
    assert "Republic Bar" in line
    assert SOURCE_MARKERS["tasguide"] in line
    assert "https://x" in line  # markdown link to ticket page


def test_render_title_says_gigs_in_place():
    payload = render_places_listing(
        _place(),
        events=[
            Event("Gig A", datetime(2026, 7, 1, tzinfo=timezone.utc), None, None, None, "tasguide"),
        ],
        warnings=[],
        days=30,
        source_counts={"tasguide": 1},
        cache_age_s=None,
    )
    assert "Gigs in Hobart" == payload["title"]


def test_render_empty_state_mentions_days_flag_and_other_places():
    payload = render_places_listing(
        _place(), events=[], warnings=[], days=30, source_counts={}, cache_age_s=None
    )
    assert "No gigs" in payload["description"]
    assert "--days" in payload["description"]
    assert "giglistplaces" in payload["description"]


def test_render_empty_state_surfaces_warnings_with_diag_hint():
    """If every source failed, the 'no gigs' message must NOT hide the
    failure — it must show a `⚠ N issue(s) — giglistdiag` line so the user
    knows the silence is upstream-broken, not just a quiet weekend."""
    place = _place()
    payload = render_places_listing(
        place,
        events=[],
        warnings=["tasguide: blocked", "humanitix: 403"],
        days=30,
        source_counts={},
        cache_age_s=None,
    )
    desc = payload["description"]
    assert "No gigs" in desc
    assert "⚠" in desc
    assert "2" in desc and "issue" in desc  # plural count of source failures
    assert "giglistdiag" in desc
    assert place.key in desc


def test_render_shows_scope_note_above_event_list_when_provided():
    """When local sources came up empty but statewide context is being
    shown, the renderer prefixes a clear note so the title isn't misleading."""
    event = Event(
        title="Statewide Gig",
        start=datetime(2026, 7, 1, 19, tzinfo=timezone.utc),
        end=None,
        venue="A Venue",
        url="https://x",
        source="tasguide",
    )
    payload = render_places_listing(
        _place(),
        events=[event],
        warnings=[],
        days=30,
        source_counts={"tasguide": 1},
        cache_age_s=None,
        scope_note=(
            "_No Devonport-specific gigs in the next 30 days. "
            "Showing wider Tasmania listings below._"
        ),
    )
    desc = payload["description"]
    assert "No Devonport-specific gigs" in desc
    # Note must appear above the event line, not buried in the footer
    assert desc.index("No Devonport-specific gigs") < desc.index("Statewide Gig")


def test_render_omits_scope_note_when_not_provided():
    """No regression for the common case: omitting scope_note must not
    insert any 'wider listings' wording."""
    event = Event(
        title="A Gig", start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        end=None, venue=None, url=None, source="tasguide",
    )
    payload = render_places_listing(
        _place(), events=[event], warnings=[], days=30,
        source_counts={"tasguide": 1}, cache_age_s=None,
    )
    assert "wider" not in payload["description"]
    assert "showing" not in payload["description"].lower()


def _ev(title, start, *, scope=None, source="tasguide", venue="V"):
    e = Event(
        title=title, start=start, end=None, venue=venue,
        url="https://x", source=source,
    )
    e.scope = scope
    return e


def test_render_emits_two_sections_when_local_and_statewide_both_present():
    """The user's request: when a place has both local hits and statewide
    context, label them in clearly separated sections so a glance shows
    which gigs are actually in the place vs wider Tasmania context."""
    local_e = _ev(
        "Launceston Lunchbox",
        datetime(2026, 5, 26, 13, tzinfo=timezone.utc),
        scope="local", source="humanitix",
    )
    statewide_e1 = _ev(
        "Hobart Open Mic",
        datetime(2026, 5, 27, 19, tzinfo=timezone.utc),
        scope="statewide",
    )
    statewide_e2 = _ev(
        "Hobart Trivia",
        datetime(2026, 5, 28, 19, tzinfo=timezone.utc),
        scope="statewide",
    )
    payload = render_places_listing(
        Place(key="launceston", display_name="Launceston", sources=()),
        events=[local_e, statewide_e1, statewide_e2],
        warnings=[],
        days=30,
        source_counts={"humanitix": 1, "tasguide": 2},
        cache_age_s=None,
    )
    desc = payload["description"]
    # Both section headers are present
    assert "Local in Launceston" in desc
    assert "Wider" in desc  # "Wider Tasmania" or "Wider listings"
    # Counts appear in/near each header
    assert "(1)" in desc and "(2)" in desc
    # Local section comes above the wider one
    assert desc.index("Local in Launceston") < desc.index("Wider")
    # Local event is in the local section (above 'Wider')
    assert desc.index("Launceston Lunchbox") < desc.index("Wider")
    # Statewide events are below the 'Wider' header
    assert desc.index("Wider") < desc.index("Hobart Open Mic")
    assert desc.index("Wider") < desc.index("Hobart Trivia")


def test_render_no_sections_when_all_events_are_statewide():
    """Devonport (or `[p]giglist tasmania`) — no local hits to separate —
    must NOT emit empty-section headers. Flat list."""
    events = [
        _ev("A", datetime(2026, 5, 27, tzinfo=timezone.utc), scope="statewide"),
        _ev("B", datetime(2026, 5, 28, tzinfo=timezone.utc), scope="statewide"),
    ]
    payload = render_places_listing(
        Place(key="tasmania", display_name="Tasmania", sources=()),
        events=events, warnings=[], days=30,
        source_counts={"tasguide": 2}, cache_age_s=None,
    )
    desc = payload["description"]
    assert "Local in" not in desc
    assert "Wider" not in desc


def test_render_no_sections_when_all_events_are_local():
    events = [
        _ev("A", datetime(2026, 5, 27, tzinfo=timezone.utc), scope="local"),
        _ev("B", datetime(2026, 5, 28, tzinfo=timezone.utc), scope="local"),
    ]
    payload = render_places_listing(
        Place(key="hobart", display_name="Hobart", sources=()),
        events=events, warnings=[], days=30,
        source_counts={"humanitix": 2}, cache_age_s=None,
    )
    desc = payload["description"]
    assert "Local in" not in desc
    assert "Wider" not in desc


def test_render_existing_unscoped_events_remain_flat():
    """Back-compat: events without a scope attribute (older tests, future
    sources) render as a flat list — no spurious sectioning."""
    e = Event(
        "Plain", datetime(2026, 5, 27, tzinfo=timezone.utc),
        None, "V", "https://x", "tasguide",
    )
    # No scope attribute set
    payload = render_places_listing(
        Place(key="x", display_name="X", sources=()),
        events=[e], warnings=[], days=30,
        source_counts={"tasguide": 1}, cache_age_s=None,
    )
    desc = payload["description"]
    assert "Local in" not in desc
    assert "Wider" not in desc
    assert "Plain" in desc


def test_render_source_counts_footer_uses_markers():
    counts_line = format_source_counts({"tasguide": 3, "humanitix": 2, "ticketmaster": 1})
    assert SOURCE_MARKERS["tasguide"] in counts_line
    assert SOURCE_MARKERS["humanitix"] in counts_line
    assert SOURCE_MARKERS["ticketmaster"] in counts_line
    assert "tasguide (3)" in counts_line
