from .guild import GuildStore
from .pack import PackStore
from .place import PlaceStore


async def copy_pack_place_to_guild(guild: GuildStore, pack, key: str) -> tuple:
    """If `key` exists only in the bundled pack (not the guild), copy the
    pack's place into the guild so subsequent mutations don't silently
    shadow the pack's sources. Returns the tuple of inherited sources;
    empty if no copy was performed (guild already has it, or pack doesn't)."""
    existing = await guild.get(key)
    if existing is not None:
        return ()
    if pack is None:
        return ()
    pack_place = pack.resolve(key)
    if pack_place is None:
        return ()
    await guild.add_place(pack_place.key, pack_place.display_name)
    for src in pack_place.sources:
        await guild.add_source(pack_place.key, src)
    for alias in pack_place.aliases:
        await guild.add_alias(pack_place.key, alias)
    return pack_place.sources


__all__ = ["GuildStore", "PackStore", "PlaceStore", "copy_pack_place_to_guild"]
