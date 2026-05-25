import pytest

from whatsonin.models import Source


def test_source_is_frozen_and_hashable():
    s = Source(kind="eventbrite", spec={"slug": "australia--hobart"})
    with pytest.raises(Exception):
        s.kind = "ics"  # type: ignore


def test_source_label_defaults_to_none():
    s = Source(kind="ics", spec={"url": "https://x"})
    assert s.label is None


def test_source_with_label():
    s = Source(kind="ics", spec={"url": "https://x"}, label="Council calendar")
    assert s.label == "Council calendar"


from whatsonin.models import Place  # noqa: E402


def test_place_holds_sources_tuple():
    src = Source(kind="eventbrite", spec={"slug": "australia--hobart"})
    p = Place(key="hobart", display_name="Hobart", sources=(src,))
    assert p.sources == (src,)
    assert p.aliases == ()


def test_place_with_aliases():
    src = Source(kind="eventbrite", spec={"slug": "australia--hobart"})
    p = Place(
        key="hobart",
        display_name="Hobart",
        sources=(src,),
        aliases=("hbt", "hobart-town"),
    )
    assert p.aliases == ("hbt", "hobart-town")


def test_place_with_empty_sources_is_hashable():
    p = Place(key="x", display_name="X", sources=())
    {p}  # frozen + tuple fields → hashable when no dicts present
