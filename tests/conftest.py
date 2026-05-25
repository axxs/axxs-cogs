import sys
from unittest.mock import MagicMock

# Red and discord are optional for unit tests of parsing/providers.
if "redbot" not in sys.modules:
    redbot = MagicMock()
    redbot.core = MagicMock()
    redbot.core.commands = MagicMock()
    redbot.core.Config = MagicMock()
    sys.modules["redbot"] = redbot
    sys.modules["redbot.core"] = redbot.core
    sys.modules["redbot.core.commands"] = redbot.core.commands
    sys.modules["redbot.core.Config"] = redbot.core.Config

if "discord" not in sys.modules:
    discord = MagicMock()
    discord.Color = MagicMock()
    discord.Embed = MagicMock()
    sys.modules["discord"] = discord


class FakeGuildConfigGroup:
    """In-memory stand-in for Red's per-guild Config group.

    Mirrors the surface that GuildStore uses: attribute access returns a
    'leaf' that you can call (async) to get the value, or call .set(value)
    on (async) to write it. Backed by a single dict per guild_id."""

    _stores: dict = {}

    def __init__(self, guild_id):
        self.guild_id = guild_id
        self._stores.setdefault(guild_id, {})

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        store = self._stores[self.guild_id]
        return _FakeLeaf(store, name)


class _FakeLeaf:
    def __init__(self, store, key):
        self._store = store
        self._key = key

    async def __call__(self):
        return self._store.get(self._key)

    async def set(self, value):
        self._store[self._key] = value


class FakeConfig:
    """Stand-in for Red's Config object. Only the .guild(...) entry point."""

    def __init__(self):
        FakeGuildConfigGroup._stores.clear()

    def guild(self, guild_or_id):
        gid = getattr(guild_or_id, "id", guild_or_id)
        return FakeGuildConfigGroup(gid)
