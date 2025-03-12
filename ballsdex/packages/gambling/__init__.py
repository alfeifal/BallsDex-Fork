from typing import TYPE_CHECKING

from ballsdex.packages.gambling.cog import Gambling

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Gambling(bot))