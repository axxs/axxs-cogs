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


def test_ticketmaster_source_uses_tasmania_dma():
    places, _ = load_region("tasmania", REGIONS_DIR)
    tm_sources = [s for s in places["hobart"].sources if s.kind == "ticketmaster"]
    assert tm_sources[0].spec.get("dmaId") in ("707", 707)


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


def test_load_region_rejects_entry_with_no_sources(tmp_path):
    region_dir = tmp_path / "broken"
    region_dir.mkdir()
    (region_dir / "places.yaml").write_text(
        "places:\n  - key: nowhere\n    display_name: Nowhere\n"
    )
    with pytest.raises(RegionNotFoundError):
        load_region("broken", tmp_path)
