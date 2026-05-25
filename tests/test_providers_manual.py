import pytest

from whatsonin.models import Source
from whatsonin.providers.manual import ManualProvider, serialize_event, deserialize_event


def test_serialize_event_round_trip():
    raw = {
        "title": "Trivia Night",
        "start": "2026-06-10T19:00:00+00:00",
        "venue": "Republic Bar",
        "url": "https://example.com/trivia",
    }
    ev = deserialize_event(raw)
    assert ev.title == "Trivia Night"
    assert ev.start.isoformat() == "2026-06-10T19:00:00+00:00"
    assert ev.venue == "Republic Bar"
    assert ev.source == "manual"

    back = serialize_event(ev)
    assert back["title"] == raw["title"]
    assert back["start"] == raw["start"]
    assert back["venue"] == raw["venue"]
    assert back["url"] == raw["url"]


def test_deserialize_event_handles_missing_optional_fields():
    raw = {"title": "Date TBA event", "start": None}
    ev = deserialize_event(raw)
    assert ev.title == "Date TBA event"
    assert ev.start is None
    assert ev.venue is None


def test_deserialize_event_drops_invalid_start_silently():
    raw = {"title": "x", "start": "not-a-date"}
    ev = deserialize_event(raw)
    assert ev.start is None


@pytest.mark.asyncio
async def test_manual_provider_returns_stored_events():
    spec = {
        "events": [
            {"title": "A", "start": "2099-01-01T12:00:00+00:00", "venue": "Place A"},
            {"title": "B", "start": "2099-02-01T12:00:00+00:00"},
        ]
    }
    source = Source(kind="manual", spec=spec)
    provider = ManualProvider()
    result = await provider.fetch(source, days=99999, limit=10)
    assert result.error is None
    assert {e.title for e in result.events} == {"A", "B"}


@pytest.mark.asyncio
async def test_manual_provider_filters_past_events():
    spec = {
        "events": [
            {"title": "Past", "start": "2020-01-01T12:00:00+00:00"},
            {"title": "Future", "start": "2099-01-01T12:00:00+00:00"},
        ]
    }
    result = await ManualProvider().fetch(
        Source(kind="manual", spec=spec), days=99999, limit=10
    )
    titles = [e.title for e in result.events]
    assert titles == ["Future"]


@pytest.mark.asyncio
async def test_manual_provider_handles_empty_spec():
    result = await ManualProvider().fetch(
        Source(kind="manual", spec={"events": []}), days=30, limit=10
    )
    assert result.error is None
    assert result.events == []


@pytest.mark.asyncio
async def test_manual_provider_skips_unparseable_entries_with_warning():
    spec = {
        "events": [
            {"title": "Good", "start": "2099-01-01T12:00:00+00:00"},
            {"start": "2099-02-01T12:00:00+00:00"},  # missing title
        ]
    }
    result = await ManualProvider().fetch(
        Source(kind="manual", spec=spec), days=99999, limit=10
    )
    assert [e.title for e in result.events] == ["Good"]
    assert result.warnings  # something about the skipped entry
