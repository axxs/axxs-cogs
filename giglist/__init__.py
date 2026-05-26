async def setup(bot):
    # Imported lazily so importing giglist submodules (e.g. in tests) does
    # not require redbot/discord to be installed.
    from .giglist import Giglist

    await bot.add_cog(Giglist(bot))
