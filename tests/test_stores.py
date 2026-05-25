import pytest

from tests.conftest import FakeConfig
from whatsonin.models import Source
from whatsonin.stores import (
    GuildStore,
    PackStore,
    PlaceStore,
    copy_pack_place_to_guild,
)


# ---------- PackStore ----------

def test_pack_store_loads_tasmania():
    pack = PackStore("tasmania")
    place = pack.resolve("hobart")
    assert place is not None
    assert place.sources[0].kind == "eventbrite"


def test_pack_store_resolve_unknown_returns_none():
    pack = PackStore("tasmania")
    assert pack.resolve("melbourne") is None


def test_pack_store_known_places_includes_canonical_names():
    pack = PackStore("tasmania")
    names = [name for name, _ in pack.known_places()]
    assert "Hobart" in names


# ---------- GuildStore ----------

@pytest.mark.asyncio
async def test_guild_store_starts_empty():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    assert await store.list_keys() == []


@pytest.mark.asyncio
async def test_guild_store_add_and_get_place():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    place = await store.add_place("brisbane", display_name="Brisbane")
    assert place.key == "brisbane"
    assert place.display_name == "Brisbane"

    fetched = await store.get("brisbane")
    assert fetched is not None
    assert fetched.display_name == "Brisbane"


@pytest.mark.asyncio
async def test_guild_store_add_source_appends():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    await store.add_place("brisbane", display_name="Brisbane")
    place = await store.add_source(
        "brisbane",
        Source(kind="eventbrite", spec={"slug": "australia--brisbane"}),
    )
    assert len(place.sources) == 1
    assert place.sources[0].kind == "eventbrite"

    place = await store.add_source(
        "brisbane", Source(kind="ics", spec={"url": "https://x"})
    )
    assert len(place.sources) == 2


@pytest.mark.asyncio
async def test_guild_store_remove_source_by_index():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    await store.add_place("brisbane", display_name="Brisbane")
    await store.add_source("brisbane", Source(kind="eventbrite", spec={"slug": "a"}))
    await store.add_source("brisbane", Source(kind="ics", spec={"url": "https://b"}))
    place = await store.remove_source("brisbane", 0)
    assert len(place.sources) == 1
    assert place.sources[0].kind == "ics"


@pytest.mark.asyncio
async def test_guild_store_remove_place():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    await store.add_place("brisbane", display_name="Brisbane")
    assert await store.remove_place("brisbane") is True
    assert await store.get("brisbane") is None
    assert await store.remove_place("nonexistent") is False


@pytest.mark.asyncio
async def test_guild_store_aliases():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    await store.add_place("brisbane", display_name="Brisbane")
    place = await store.add_alias("brisbane", "bne")
    assert "bne" in place.aliases
    place = await store.remove_alias("brisbane", "bne")
    assert "bne" not in place.aliases


@pytest.mark.asyncio
async def test_guild_store_normalizes_input_keys_and_aliases():
    config = FakeConfig()
    store = GuildStore(config, guild_id=1)
    # mixed case + underscore in user input
    await store.add_place("North_Hobart", display_name="North Hobart")
    assert await store.get("north hobart") is not None
    place = await store.add_alias("north hobart", "NTH-HOBART")
    assert "nth hobart" in place.aliases


@pytest.mark.asyncio
async def test_guild_store_per_guild_isolation():
    config = FakeConfig()
    s1 = GuildStore(config, guild_id=1)
    s2 = GuildStore(config, guild_id=2)
    await s1.add_place("brisbane", display_name="Brisbane")
    assert await s2.get("brisbane") is None


# ---------- PlaceStore (composition) ----------

@pytest.mark.asyncio
async def test_place_store_resolves_guild_then_pack():
    config = FakeConfig()
    guild = GuildStore(config, guild_id=1)
    pack = PackStore("tasmania")
    composed = PlaceStore(guild=guild, pack=pack)

    # pack-only: hobart
    place = await composed.resolve("hobart")
    assert place is not None
    assert place.sources[0].spec["slug"] == "australia--hobart"

    # guild-only: brisbane
    await guild.add_place("brisbane", display_name="Brisbane")
    await guild.add_source(
        "brisbane", Source(kind="eventbrite", spec={"slug": "australia--brisbane"})
    )
    place = await composed.resolve("brisbane")
    assert place is not None
    assert place.sources[0].spec["slug"] == "australia--brisbane"


@pytest.mark.asyncio
async def test_place_store_guild_shadows_pack_for_same_key():
    config = FakeConfig()
    guild = GuildStore(config, guild_id=1)
    pack = PackStore("tasmania")
    composed = PlaceStore(guild=guild, pack=pack)

    # guild override of an existing pack place
    await guild.add_place("hobart", display_name="Hobart (custom)")
    await guild.add_source(
        "hobart", Source(kind="ics", spec={"url": "https://custom.example/cal.ics"})
    )
    place = await composed.resolve("hobart")
    assert place is not None
    assert place.display_name == "Hobart (custom)"
    assert place.sources[0].kind == "ics"


# ---------- copy_pack_place_to_guild ----------

@pytest.mark.asyncio
async def test_copy_pack_place_to_guild_inherits_sources_aliases_display_name():
    config = FakeConfig()
    guild = GuildStore(config, guild_id=1)
    pack = PackStore("tasmania")

    inherited = await copy_pack_place_to_guild(guild, pack, "hobart")

    # Returned sources match what was in the pack
    assert len(inherited) >= 1
    assert any(s.kind == "eventbrite" for s in inherited)

    # The guild place is now self-contained with all pack data
    place = await guild.get("hobart")
    assert place is not None
    assert place.display_name == "Hobart"
    assert place.sources == inherited
    assert "hbt" in place.aliases  # pack aliases preserved


@pytest.mark.asyncio
async def test_copy_pack_place_to_guild_noop_when_already_in_guild():
    config = FakeConfig()
    guild = GuildStore(config, guild_id=1)
    pack = PackStore("tasmania")

    # Pre-existing guild place
    await guild.add_place("hobart", display_name="Hobart (custom)")
    await guild.add_source(
        "hobart", Source(kind="ics", spec={"url": "https://x"})
    )

    inherited = await copy_pack_place_to_guild(guild, pack, "hobart")

    assert inherited == ()  # no inheritance performed
    place = await guild.get("hobart")
    assert place.display_name == "Hobart (custom)"
    assert place.sources[0].kind == "ics"  # unchanged


@pytest.mark.asyncio
async def test_copy_pack_place_to_guild_noop_when_pack_missing_key():
    config = FakeConfig()
    guild = GuildStore(config, guild_id=1)
    pack = PackStore("tasmania")

    inherited = await copy_pack_place_to_guild(guild, pack, "melbourne")

    assert inherited == ()
    assert await guild.get("melbourne") is None


@pytest.mark.asyncio
async def test_copy_pack_place_to_guild_handles_none_pack():
    """No active pack at all — function returns empty without raising."""
    config = FakeConfig()
    guild = GuildStore(config, guild_id=1)
    inherited = await copy_pack_place_to_guild(guild, None, "anything")
    assert inherited == ()
