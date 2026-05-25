from whatsonin.places import PlaceResolver


def test_resolve_hobart_case_insensitive():
    resolver = PlaceResolver("tasmania")
    place = resolver.resolve("HOBART")
    assert place is not None
    assert place.display_name == "Hobart"
    assert place.sources[0].spec["slug"] == "australia--hobart"


def test_resolve_alias_north_hobart():
    resolver = PlaceResolver("tasmania")
    place = resolver.resolve("north hobart")
    assert place is not None
    assert place.display_name == "North Hobart"


def test_resolve_unknown_place():
    resolver = PlaceResolver("tasmania")
    assert resolver.resolve("melbourne") is None


def test_known_places_sorted():
    resolver = PlaceResolver("tasmania")
    names = resolver.known_places()
    assert "Hobart" in names
    assert names == sorted(names, key=str.lower)


def test_reload_switches_region():
    resolver = PlaceResolver("tasmania")
    assert resolver.resolve("hobart") is not None
    assert resolver.resolve("parramatta") is None

    resolver.reload("sydney")
    assert resolver.resolve("parramatta") is not None
    assert resolver.resolve("hobart") is None


def test_resolver_does_not_silently_strip_trailing_words():
    # User types "hobart tasmania". Current loose match would silently
    # discard "tasmania" and return Hobart. We want None so the cog can
    # tell the user we didn't understand them.
    resolver = PlaceResolver("tasmania")
    assert resolver.resolve("hobart tasmania") is None


def test_resolver_rejects_too_short_prefix():
    # Two-char prefixes are too ambiguous across packs; require >=3 chars.
    resolver = PlaceResolver("tasmania")
    assert resolver.resolve("ho") is None


def test_resolver_rejects_ambiguous_prefix():
    # "n" prefix matches both "new norfolk" and "north hobart". Must be None,
    # not whichever happens to come first in YAML order.
    resolver = PlaceResolver("tasmania")
    assert resolver.resolve("n") is None


def test_resolver_still_accepts_unambiguous_prefix():
    # "queens" uniquely prefixes "queenstown". Should still resolve.
    resolver = PlaceResolver("tasmania")
    place = resolver.resolve("queens")
    assert place is not None
    assert place.display_name == "Queenstown"


def test_aggregated_parent_for_suburb_returns_city_display_name():
    # Sandy Bay shares the australia--hobart slug with Hobart, so listings
    # are actually Hobart-wide. The resolver should flag it.
    resolver = PlaceResolver("tasmania")
    sandy_bay = resolver.resolve("sandy bay")
    assert resolver.aggregated_parent(sandy_bay) == "Hobart"


def test_aggregated_parent_returns_none_for_canonical_place():
    # Hobart itself is the canonical owner of australia--hobart. Not aggregated.
    resolver = PlaceResolver("tasmania")
    hobart = resolver.resolve("hobart")
    assert resolver.aggregated_parent(hobart) is None


def test_aggregated_parent_returns_none_when_slug_unique():
    # Launceston is the only place with its slug.
    resolver = PlaceResolver("tasmania")
    launceston = resolver.resolve("launceston")
    assert resolver.aggregated_parent(launceston) is None
