from typing import TYPE_CHECKING

from .cog import Collector

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
<<<<<<< HEAD
    await bot.add_cog(Collector(bot))
=======
    await bot.add_cog(Collector(bot))
>>>>>>> upstream/master
