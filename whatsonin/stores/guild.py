from __future__ import annotations

from typing import Optional

from ..models import Place, Source
from ..regions import normalize_name


class _GuildRef:
    """Minimal stand-in for discord.Guild. Red's Config.guild only reads
    .id, so this lets GuildStore accept a raw int (used in tests) or any
    object with an .id attribute (the real discord.Guild)."""
    __slots__ = ("id",)

    def __init__(self, id):
        self.id = id


class GuildStore:
    """Per-guild place storage backed by Red's Config.guild(...).

    Serializes Place records to plain dicts so they survive Config's JSON
    round-trip. Source.spec is already a plain dict; we add 'kind' and
    'label' alongside it."""

    KEY = "places"

    def __init__(self, config, guild_id):
        self._config = config
        if hasattr(guild_id, "id"):
            self._guild_ref = guild_id
        else:
            self._guild_ref = _GuildRef(guild_id)

    # ---- raw read/write ----

    async def _read_all(self) -> dict:
        group = self._config.guild(self._guild_ref)
        data = await group.places()
        if data is None:
            return {}
        return dict(data)

    async def _write_all(self, data: dict) -> None:
        group = self._config.guild(self._guild_ref)
        await group.places.set(data)

    # ---- query ----

    async def list_keys(self) -> list:
        data = await self._read_all()
        return sorted(data.keys())

    async def get(self, key: str) -> Optional[Place]:
        key = normalize_name(key)
        data = await self._read_all()
        raw = data.get(key)
        if raw is None:
            return None
        return _deserialize_place(raw)

    async def known_places(self) -> list[tuple]:
        data = await self._read_all()
        return [(raw["display_name"], "guild") for raw in data.values()]

    async def resolve(self, name: str) -> Optional[Place]:
        """Direct-key resolution against the guild's stored places. Supports
        aliases. Prefix matching not done here. The composition layer can add
        it if needed."""
        normalized = normalize_name(name)
        data = await self._read_all()
        if normalized in data:
            return _deserialize_place(data[normalized])
        for raw in data.values():
            if normalized in (raw.get("aliases") or ()):
                return _deserialize_place(raw)
        return None

    # ---- mutation ----

    async def add_place(self, key: str, display_name: str) -> Place:
        key = normalize_name(key)
        data = await self._read_all()
        if key not in data:
            data[key] = {
                "key": key,
                "display_name": display_name,
                "sources": [],
                "aliases": [],
            }
            await self._write_all(data)
        return _deserialize_place(data[key])

    async def remove_place(self, key: str) -> bool:
        key = normalize_name(key)
        data = await self._read_all()
        if key not in data:
            return False
        del data[key]
        await self._write_all(data)
        return True

    async def add_source(self, key: str, source: Source) -> Place:
        return await self._mutate(key, lambda raw: raw["sources"].append(_serialize_source(source)))

    async def remove_source(self, key: str, index: int) -> Place:
        def remove(raw):
            if 0 <= index < len(raw["sources"]):
                raw["sources"].pop(index)
        return await self._mutate(key, remove)

    async def add_alias(self, key: str, alias: str) -> Place:
        alias = normalize_name(alias)
        def add(raw):
            aliases = list(raw.get("aliases") or [])
            if alias and alias not in aliases:
                aliases.append(alias)
            raw["aliases"] = aliases
        return await self._mutate(key, add)

    async def remove_alias(self, key: str, alias: str) -> Place:
        alias = normalize_name(alias)
        def remove(raw):
            aliases = [a for a in (raw.get("aliases") or []) if a != alias]
            raw["aliases"] = aliases
        return await self._mutate(key, remove)

    async def update_source(self, key: str, index: int, *, mutator) -> Place:
        """Apply mutator(raw_source_dict) to the source at index."""
        def edit(raw):
            if 0 <= index < len(raw["sources"]):
                mutator(raw["sources"][index])
        return await self._mutate(key, edit)

    async def _mutate(self, key: str, fn) -> Place:
        key = normalize_name(key)
        data = await self._read_all()
        if key not in data:
            raise KeyError(f"No place '{key}' in this guild.")
        fn(data[key])
        await self._write_all(data)
        return _deserialize_place(data[key])


def _serialize_source(source: Source) -> dict:
    return {"kind": source.kind, "spec": dict(source.spec), "label": source.label}


def _deserialize_source(raw: dict) -> Source:
    return Source(
        kind=raw["kind"], spec=dict(raw.get("spec") or {}), label=raw.get("label")
    )


def _deserialize_place(raw: dict) -> Place:
    sources = tuple(_deserialize_source(s) for s in raw.get("sources", []))
    aliases = tuple(raw.get("aliases") or ())
    return Place(
        key=raw["key"],
        display_name=raw["display_name"],
        sources=sources,
        aliases=aliases,
    )
