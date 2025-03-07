from typing import TYPE_CHECKING

<<<<<<< HEAD
from .cog import Boss
=======
from ballsdex.packages.boss.cog import Boss
>>>>>>> upstream/master

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
<<<<<<< HEAD
    await bot.add_cog(Boss(bot))
=======
    await bot.add_cog(Boss(bot))
>>>>>>> upstream/master
