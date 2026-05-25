from datetime import datetime, timezone

from whatsonin.models import Event
from whatsonin.render import format_event_line


def _event(**overrides):
    base = dict(
        title="Mona Foma Closing Party",
        start=datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc),
        end=None,
        venue="MONA",
        url="https://www.eventbrite.com/e/123",
        source="eventbrite",
        description=None,
    )
    base.update(overrides)
    return Event(**base)


def test_format_event_line_full():
    line = format_event_line(_event())
    assert line.startswith("Fri 5 Jun")
    assert "<t:1780689600:R>" in line
    assert "[Mona Foma Closing Party](https://www.eventbrite.com/e/123)" in line
    assert "@ MONA" in line
    assert line.endswith("ᴱ")


def test_format_event_line_no_url_no_link_markdown():
    line = format_event_line(_event(url=None))
    assert "Mona Foma Closing Party" in line
    assert "[" not in line
    assert "@ MONA" in line


def test_format_event_line_no_venue():
    line = format_event_line(_event(venue=None))
    assert "@" not in line


def test_format_event_line_no_start_date_tba():
    line = format_event_line(_event(start=None))
    assert "Date TBA" in line
    assert "<t:" not in line


def test_format_event_line_truncates_long_title():
    long = "x" * 200
    line = format_event_line(_event(title=long))
    assert "x" * 80 + "…" in line
    assert "x" * 81 not in line


def test_format_event_line_truncates_long_venue():
    long = "y" * 100
    line = format_event_line(_event(venue=long))
    assert "y" * 40 + "…" in line
    assert "y" * 41 not in line


def test_format_event_line_unknown_source_uses_fallback_marker():
    line = format_event_line(_event(source="weather-balloon"))
    assert line.endswith("◌")


from whatsonin.render import format_cache_age  # noqa: E402


def test_format_cache_age_none_returns_empty():
    assert format_cache_age(None) == ""


def test_format_cache_age_zero_is_fresh():
    assert format_cache_age(0) == "fresh"


def test_format_cache_age_seconds():
    assert format_cache_age(45) == "cached 45s ago"


def test_format_cache_age_minutes():
    assert format_cache_age(60) == "cached 1 min ago"
    assert format_cache_age(240) == "cached 4 min ago"


def test_format_cache_age_hours():
    assert format_cache_age(3600) == "cached 1 h ago"
    assert format_cache_age(7800) == "cached 2 h ago"


from whatsonin.render import format_source_counts  # noqa: E402


def test_format_source_counts_single():
    assert format_source_counts({"eventbrite": 5}) == "ᴱ eventbrite (5)"


def test_format_source_counts_multiple_known_sources():
    out = format_source_counts({"eventbrite": 5, "ics": 2, "manual": 1})
    assert out == "ᴱ eventbrite (5) · ᴵ ics (2) · ᴹ manual (1)"


def test_format_source_counts_unknown_source_uses_fallback():
    assert format_source_counts({"weather-balloon": 3}) == "◌ weather-balloon (3)"


def test_format_source_counts_empty_returns_empty_string():
    assert format_source_counts({}) == ""


def test_format_source_counts_skips_zero_counts():
    assert format_source_counts({"eventbrite": 5, "ics": 0}) == "ᴱ eventbrite (5)"


from whatsonin.models import Place, Source  # noqa: E402
from whatsonin.render import render_places_listing  # noqa: E402


HOBART = Place(
    key="hobart",
    display_name="Hobart",
    sources=(Source(kind="eventbrite", spec={"slug": "australia--hobart"}),),
)


def _events(count, *, source="eventbrite"):
    return [
        Event(
            title=f"Event {i}",
            start=datetime(2026, 6, 5 + i, 20, 0, tzinfo=timezone.utc),
            end=None,
            venue=f"Venue {i}",
            url=f"https://example.com/{i}",
            source=source,
            description=None,
        )
        for i in range(count)
    ]


def test_render_places_listing_multi_event_returns_embed_dict():
    events = _events(3)
    result = render_places_listing(
        HOBART,
        events,
        warnings=[],
        days=30,
        source_counts={"eventbrite": 3},
        cache_age_s=240,
    )

    assert result["title"] == "What's on in Hobart"
    desc = result["description"]
    assert "next 30 days" in desc
    for i in range(3):
        assert f"Event {i}" in desc
        assert f"Venue {i}" in desc
    assert "ᴱ eventbrite (3)" in desc
    assert "cached 4 min ago" in desc


def test_render_places_listing_embeds_links_for_event_urls():
    result = render_places_listing(
        HOBART, _events(1), [], days=30, source_counts={"eventbrite": 1}, cache_age_s=None,
    )
    assert "[Event 0](https://example.com/0)" in result["description"]


def test_render_places_listing_strips_cache_age_when_none():
    result = render_places_listing(
        HOBART, _events(1), [], days=30, source_counts={"eventbrite": 1}, cache_age_s=None,
    )
    assert "ᴱ eventbrite (1)" in result["description"]
    assert "cached" not in result["description"]


def test_render_places_listing_empty_includes_helpful_prompt():
    result = render_places_listing(
        HOBART, events=[], warnings=[], days=30, source_counts={}, cache_age_s=None,
    )
    desc = result["description"]
    assert "No events in the next 30 days" in desc
    assert "--days 60" in desc
    assert "[p]wsa source add hobart" in desc


def test_render_places_listing_warnings_renders_summary_line():
    result = render_places_listing(
        HOBART,
        _events(2),
        warnings=["eventbrite: no event data", "ics: timeout"],
        days=30,
        source_counts={"eventbrite": 2},
        cache_age_s=None,
    )
    desc = result["description"]
    assert "⚠ 2 issue" in desc
    assert "[p]whatsonin diag hobart" in desc
    assert "timeout" not in desc


def test_render_places_listing_single_warning_uses_singular():
    result = render_places_listing(
        HOBART,
        _events(1),
        warnings=["eventbrite: no event data"],
        days=30,
        source_counts={"eventbrite": 1},
        cache_age_s=None,
    )
    assert "⚠ 1 issue" in result["description"]


def test_render_places_listing_no_warnings_no_warn_line():
    result = render_places_listing(
        HOBART, _events(1), [], days=30, source_counts={"eventbrite": 1}, cache_age_s=None,
    )
    assert "⚠" not in result["description"]


def test_render_places_listing_includes_aggregated_parent_note():
    sandy = Place(
        key="sandy bay",
        display_name="Sandy Bay",
        sources=(Source(kind="eventbrite", spec={"slug": "australia--hobart"}),),
    )
    result = render_places_listing(
        sandy,
        _events(1),
        [],
        days=30,
        source_counts={"eventbrite": 1},
        cache_age_s=None,
        aggregated_parent="Hobart",
    )
    desc = result["description"]
    assert "Showing Hobart-wide events" in desc
    assert "Sandy Bay" in desc


def test_render_places_listing_truncates_when_description_overflows():
    big = [
        Event(
            title="x" * 80,
            start=datetime(2026, 6, 5 + (i // 24), (i % 24), 0, tzinfo=timezone.utc),
            end=None,
            venue="y" * 40,
            url=f"https://example.com/{i}",
            source="eventbrite",
            description=None,
        )
        for i in range(50)
    ]
    result = render_places_listing(
        HOBART, big, [], days=30, source_counts={"eventbrite": 50}, cache_age_s=None,
    )
    desc = result["description"]
    assert len(desc) <= 4000
    assert "…and" in desc
    assert "--limit" in desc
