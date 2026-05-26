from datetime import timezone

from giglist.providers.jsonld import parse_datetime, parse_jsonld_events


def test_parses_standalone_event_block_tasguide_form():
    html = (
        '<script type="application/ld+json">'
        '{"@context":"http://schema.org","@type":"Event","name":"Open Mic",'
        '"url":"https://tasguide.com.au/event/1/open-mic",'
        '"startDate":"2026-05-26T19:00:00","endDate":"2026-05-26T22:00:00",'
        '"location":{"@type":"Place","name":"Republic Bar"}}'
        "</script>"
    )
    events, warnings = parse_jsonld_events(html, "tasguide")
    assert warnings == []
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Open Mic"
    assert ev.venue == "Republic Bar"
    assert ev.url == "https://tasguide.com.au/event/1/open-mic"
    assert ev.source == "tasguide"


def test_parses_itemlist_with_extra_tag_attributes_humanitix_form():
    """Humanitix tags carry extra attributes (id=, data-next-head). The
    eventbrite-exact regex would miss these; the parser must be permissive."""
    html = (
        '<script id="itemlist-json-ld" type="application/ld+json">'
        '{"@type":"ItemList","itemListElement":[{"@type":"ListItem","position":1,'
        '"item":{"@type":"Event","name":"Natty Waves 2026",'
        '"startDate":"2026-06-12T06:00:00.000Z",'
        '"url":"https://events.humanitix.com/natty-waves-2026",'
        '"location":{"@type":"Place","name":"Hobart Historic Cruises"}}}]}'
        "</script>"
    )
    events, warnings = parse_jsonld_events(html, "humanitix")
    assert warnings == []
    assert [e.title for e in events] == ["Natty Waves 2026"]
    assert events[0].source == "humanitix"


def test_naive_local_datetime_is_localized_to_aware_utc():
    # 2026-05-26 is winter in Tasmania (AEST = UTC+10), so 19:00 local -> 09:00 UTC.
    dt = parse_datetime("2026-05-26T19:00:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 9


def test_utc_z_with_milliseconds_parses():
    dt = parse_datetime("2026-06-12T06:00:00.000Z")
    assert dt is not None
    assert dt.astimezone(timezone.utc).hour == 6


def test_venue_falls_back_to_address_when_no_location_name():
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Event","name":"Gig","location":{"@type":"Place",'
        '"address":{"@type":"PostalAddress","streetAddress":"1 Main St",'
        '"addressLocality":"Hobart","addressRegion":"TAS"}}}'
        "</script>"
    )
    events, _ = parse_jsonld_events(html, "tasguide")
    assert events[0].venue == "1 Main St, Hobart, TAS"


def test_warns_when_no_jsonld_blocks():
    events, warnings = parse_jsonld_events("<html><body>blocked</body></html>", "tasguide")
    assert events == []
    assert warnings
    assert any("no event data" in w.lower() for w in warnings)


def test_malformed_json_block_is_skipped():
    html = (
        '<script type="application/ld+json">{ not valid json </script>'
        '<script type="application/ld+json">'
        '{"@type":"Event","name":"Good One"}</script>'
    )
    events, warnings = parse_jsonld_events(html, "tasguide")
    assert [e.title for e in events] == ["Good One"]
    assert warnings == []


def test_event_without_name_is_dropped():
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Event","url":"https://x"}</script>'
    )
    events, warnings = parse_jsonld_events(html, "tasguide")
    assert events == []
    assert warnings  # no usable events -> warning
