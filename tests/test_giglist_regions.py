from pathlib import Path

import pytest

from giglist.models import Source
from giglist.places import PlaceResolver
from giglist.regions import RegionNotFoundError, list_regions, load_region

REGIONS_DIR = Path(__file__).resolve().parent.parent / "giglist" / "regions"


def test_list_regions_includes_tasmania():
    assert "tasmania" in list_regions(REGIONS_DIR)


def test_load_tasmania_pack_has_three_core_places():
    places, _ = load_region("tasmania", REGIONS_DIR)
    assert "hobart" in places
    assert "launceston" in places
    assert "tasmania" in places


def test_load_tasmania_pack_covers_major_regional_towns():
    """Pack should resolve the same Tasmanian towns whatsonin ships."""
    places, _ = load_region("tasmania", REGIONS_DIR)
    for key in (
        "devonport", "burnie", "ulverstone", "smithton",
        "queenstown", "strahan",
        "george town", "st helens",
        "sandy bay", "north hobart", "battery point", "glenorchy",
        "new norfolk", "richmond", "port arthur",
    ):
        assert key in places, f"missing place: {key!r}"


def test_burnie_omits_humanitix_because_slug_404s_upstream():
    """Humanitix has no `au--tas--burnie` page. The pack must ship Burnie
    without a Humanitix source so we don't surface a recurring 404."""
    places, _ = load_region("tasmania", REGIONS_DIR)
    burnie_kinds = sorted(s.kind for s in places["burnie"].sources)
    assert "humanitix" not in burnie_kinds
    assert burnie_kinds == ["tasguide", "ticketmaster"]


def test_devonport_resolves_and_targets_devonport_humanitix():
    places, aliases = load_region("tasmania", REGIONS_DIR)
    devonport = places["devonport"]
    humanitix = [s for s in devonport.sources if s.kind == "humanitix"]
    assert humanitix and humanitix[0].spec.get("slug") == "au--tas--devonport"


def test_aliases_resolve_for_suburbs_and_hyphenated_keys():
    resolver = PlaceResolver("tasmania", REGIONS_DIR)
    # Hyphenated user input must normalise the same as the space-form key
    assert resolver.resolve("north-hobart").key == "north hobart"
    assert resolver.resolve("port-arthur").key == "port arthur"
    assert resolver.resolve("st-helens").key == "st helens"
    # Disambiguating alias for Queenstown (vs the NZ city of the same name)
    assert resolver.resolve("queenstown-tas").key == "queenstown"


def test_hobart_place_bundles_all_three_providers():
    places, _ = load_region("tasmania", REGIONS_DIR)
    kinds = sorted(s.kind for s in places["hobart"].sources)
    assert kinds == ["humanitix", "tasguide", "ticketmaster"]


def test_hobart_humanitix_source_targets_hobart_music():
    places, _ = load_region("tasmania", REGIONS_DIR)
    humanitix_sources = [s for s in places["hobart"].sources if s.kind == "humanitix"]
    assert len(humanitix_sources) == 1
    assert humanitix_sources[0].spec.get("slug") == "au--tas--hobart"
    assert humanitix_sources[0].spec.get("category") == "music"


def test_hobart_ticketmaster_source_is_city_narrowed():
    """Hobart's TM source uses `city=Hobart` (local) rather than
    dmaId=707, so a Hobart query doesn't include Devonport/Launceston
    touring shows. The statewide dmaId stays on the 'tasmania' place."""
    places, _ = load_region("tasmania", REGIONS_DIR)
    tm = [s for s in places["hobart"].sources if s.kind == "ticketmaster"][0]
    assert tm.spec.get("city") == "Hobart"
    assert "dmaId" not in tm.spec
    assert tm.scope == "local"
    # The statewide place still uses dmaId
    statewide_tm = [
        s for s in places["tasmania"].sources if s.kind == "ticketmaster"
    ][0]
    assert statewide_tm.spec.get("dmaId") in ("707", 707)


def test_place_resolver_handles_aliases_and_prefix():
    resolver = PlaceResolver("tasmania", REGIONS_DIR)
    # 'hbt' alias for hobart
    place = resolver.resolve("hbt")
    assert place is not None and place.key == "hobart"
    # 'tas' alias for tasmania (statewide)
    statewide = resolver.resolve("tas")
    assert statewide is not None and statewide.key == "tasmania"
    # Prefix match
    assert resolver.resolve("hob").key == "hobart"


def test_source_scope_defaults_to_local():
    """Sources without an explicit `scope:` default to 'local' so existing
    YAML and existing tests continue to work unchanged."""
    s = Source(kind="humanitix", spec={"slug": "x"})
    assert s.scope == "local"


def test_load_region_parses_scope_field(tmp_path):
    region_dir = tmp_path / "scoped"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n"
        "  - key: x\n"
        "    display_name: X\n"
        "    sources:\n"
        "      - kind: humanitix\n"
        "        spec: { slug: y }\n"
        "      - kind: tasguide\n"
        "        spec: { path: category/music }\n"
        "        scope: statewide\n"
    )
    places, _ = load_region("scoped", tmp_path)
    sources = places["x"].sources
    assert sources[0].scope == "local"  # default
    assert sources[1].scope == "statewide"  # explicit


def test_devonport_uses_city_narrowed_ticketmaster_and_marks_tasguide_statewide():
    """Small towns should use TM `city=<City>` (narrow) — not dmaId=707 —
    so a Devonport query doesn't dump Hobart gigs into the result. Tasguide
    has no music+region combo, so it stays statewide-scoped."""
    places, _ = load_region("tasmania", REGIONS_DIR)
    devonport = places["devonport"]
    tm = [s for s in devonport.sources if s.kind == "ticketmaster"][0]
    assert tm.spec.get("city") == "Devonport"
    assert "dmaId" not in tm.spec
    assert tm.scope == "local"
    tg = [s for s in devonport.sources if s.kind == "tasguide"][0]
    assert tg.scope == "statewide"
    hx = [s for s in devonport.sources if s.kind == "humanitix"][0]
    assert hx.scope == "local"


def test_tasmania_statewide_place_marks_all_sources_statewide():
    """The 'tasmania' place is deliberately statewide; no source should be
    'local' there or the scope-note logic would mis-fire on it."""
    places, _ = load_region("tasmania", REGIONS_DIR)
    for src in places["tasmania"].sources:
        assert src.scope == "statewide", f"{src.kind} on tasmania must be statewide"


def test_load_region_rejects_entry_with_no_sources(tmp_path):
    region_dir = tmp_path / "broken"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n  - key: nowhere\n    display_name: Nowhere\n"
    )
    with pytest.raises(RegionNotFoundError):
        load_region("broken", tmp_path)
