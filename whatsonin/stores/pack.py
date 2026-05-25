from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..models import Place
from ..places import PlaceResolver, _primary_eventbrite_slug
from ..regions import default_regions_dir


class PackStore:
    """Read-only wrapper around a YAML region pack.

    Delegates to PlaceResolver for resolution (alias matching, prefix etc.)."""

    def __init__(self, region: str, regions_dir: Optional[Path] = None):
        self.region = region
        self._resolver = PlaceResolver(region, regions_dir or default_regions_dir())

    def resolve(self, name: str) -> Optional[Place]:
        return self._resolver.resolve(name)

    def known_places(self) -> list[tuple]:
        return [(name, "pack") for name in self._resolver.known_places()]

    def aggregated_parent(self, place: Optional[Place]) -> Optional[str]:
        return self._resolver.aggregated_parent(place)
