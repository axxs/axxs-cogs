from __future__ import annotations

from typing import Optional

from ..models import Place


class PlaceStore:
    """Composition: guild-defined places shadow same-keyed pack places.

    Resolution order: guild first, then pack. No cross-store merging."""

    def __init__(self, *, guild, pack: Optional = None):
        self._guild = guild
        self._pack = pack

    async def resolve(self, name: str) -> Optional[Place]:
        if self._guild is not None:
            place = await self._guild.resolve(name)
            if place is not None:
                return place
        if self._pack is not None:
            return self._pack.resolve(name)
        return None

    async def known_places(self) -> list[tuple]:
        out: list = []
        if self._guild is not None:
            out.extend(await self._guild.known_places())
        if self._pack is not None:
            out.extend(self._pack.known_places())
        return out

    async def aggregated_parent(self, place: Place) -> Optional[str]:
        # Only the pack carries the aggregated_parent semantics for now.
        if self._pack is not None:
            return self._pack.aggregated_parent(place)
        return None
