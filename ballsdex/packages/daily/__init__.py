from typing import TYPE_CHECKING

from .cog import daily

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
<<<<<<< HEAD
    await bot.add_cog(daily(bot))
=======
    await bot.add_cog(daily(bot))
>>>>>>> upstream/master
