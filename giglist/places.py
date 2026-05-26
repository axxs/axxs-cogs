from __future__ import annotations

from pathlib import Path
from typing import Optional

from .models import Place
from .regions import (
    RegionNotFoundError,
    default_regions_dir,
    list_regions,
    load_region,
    normalize_name,
)


class PlaceResolver:
    """Resolve user placenames to place records for the active region."""

    MIN_PREFIX_LEN = 3

    def __init__(self, region: str, regions_dir: Optional[Path] = None):
        self.region = region
        self.regions_dir = regions_dir or default_regions_dir()
        self._places, self._aliases = load_region(region, self.regions_dir)

    def reload(self, region: str) -> None:
        self.region = region
        self._places, self._aliases = load_region(region, self.regions_dir)

    @staticmethod
    def available_regions(regions_dir: Optional[Path] = None) -> list[str]:
        return list_regions(regions_dir)

    def resolve(self, placename: str) -> Optional[Place]:
        normalized = normalize_name(placename)
        if not normalized:
            return None

        alias_key = self._aliases.get(normalized, normalized)
        alias_key = self._aliases.get(alias_key, alias_key)
        if alias_key in self._places:
            return self._places[alias_key]

        for key, place in self._places.items():
            if normalized == key or normalized == place.display_name.lower():
                return place

        if len(normalized) < self.MIN_PREFIX_LEN:
            return None

        matches = [
            place for key, place in self._places.items() if key.startswith(normalized)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def known_places(self) -> list[str]:
        seen = set()
        names: list[str] = []
        for place in self._places.values():
            if place.display_name not in seen:
                seen.add(place.display_name)
                names.append(place.display_name)
        return sorted(names, key=str.lower)


__all__ = ["PlaceResolver", "RegionNotFoundError"]
