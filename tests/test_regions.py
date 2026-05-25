from pathlib import Path

import pytest

from whatsonin.places import PlaceResolver
from whatsonin.regions import RegionNotFoundError, list_regions, load_region

REGIONS_DIR = Path(__file__).resolve().parent.parent / "whatsonin" / "regions"


def test_list_regions_includes_tasmania_and_sydney():
    regions = list_regions(REGIONS_DIR)
    assert "tasmania" in regions
    assert "sydney" in regions


def test_load_tasmania_region():
    places, aliases = load_region("tasmania", REGIONS_DIR)
    assert "hobart" in places
    assert places["hobart"].sources[0].spec["slug"] == "australia--hobart"
    assert aliases["hbt"] == "hobart"


def test_load_unknown_region_raises():
    with pytest.raises(RegionNotFoundError):
        load_region("not-a-region", REGIONS_DIR)


def test_place_resolver_uses_region_pack():
    resolver = PlaceResolver("sydney", REGIONS_DIR)
    place = resolver.resolve("sydney")
    assert place is not None
    assert place.sources[0].spec["slug"] == "australia--sydney"


from whatsonin.models import Source


def test_load_region_old_eventbrite_slug_schema(tmp_path):
    region_dir = tmp_path / "old_schema"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n"
        "  - key: hobart\n"
        "    display_name: Hobart\n"
        "    eventbrite_slug: australia--hobart\n"
        "    aliases: [hbt]\n"
    )

    places, aliases = load_region("old_schema", tmp_path)
    place = places["hobart"]
    assert len(place.sources) == 1
    assert place.sources[0].kind == "eventbrite"
    assert place.sources[0].spec == {"slug": "australia--hobart"}
    assert place.aliases == ("hbt",)
    assert aliases["hbt"] == "hobart"


def test_load_region_new_sources_schema(tmp_path):
    region_dir = tmp_path / "new_schema"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n"
        "  - key: hobart\n"
        "    display_name: Hobart\n"
        "    sources:\n"
        "      - kind: eventbrite\n"
        "        spec: { slug: australia--hobart }\n"
        "      - kind: ics\n"
        "        spec: { url: 'https://example.com/cal.ics' }\n"
        "        label: Council calendar\n"
    )

    places, _ = load_region("new_schema", tmp_path)
    place = places["hobart"]
    assert len(place.sources) == 2
    assert place.sources[0] == Source(
        kind="eventbrite", spec={"slug": "australia--hobart"}
    )
    assert place.sources[1] == Source(
        kind="ics",
        spec={"url": "https://example.com/cal.ics"},
        label="Council calendar",
    )


def test_load_region_rejects_entry_with_neither_schema(tmp_path):
    region_dir = tmp_path / "bad"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n"
        "  - key: nowhere\n"
        "    display_name: Nowhere\n"
    )

    with pytest.raises(RegionNotFoundError):
        load_region("bad", tmp_path)


def test_aliases_normalize_symmetrically_with_user_input(tmp_path):
    """An alias written ONLY with dashes/underscores should still match user
    input typed with spaces (or vice versa). Demonstrates that aliases need
    the same _normalize as user input applied at load time."""
    region_dir = tmp_path / "test_region"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n"
        "  - key: nthhobart\n"
        "    display_name: North Hobart\n"
        "    eventbrite_slug: australia--hobart\n"
        "    aliases: ['nth_hobart']\n"  # underscore form only
    )

    resolver = PlaceResolver("test_region", tmp_path)
    # user types space form. Must still match the underscore alias
    assert resolver.resolve("nth hobart") is not None
    # user types dash form. Must still match
    assert resolver.resolve("nth-hobart") is not None
    # original form still works
    assert resolver.resolve("nth_hobart") is not None
