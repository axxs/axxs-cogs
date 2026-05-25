from .whatsonin import Whatsonin


async def setup(bot):
    await bot.add_cog(Whatsonin(bot))
