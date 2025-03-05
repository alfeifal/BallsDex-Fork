from typing import TYPE_CHECKING

from .cog import RedeemCodes

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    await bot.add_cog(RedeemCodes(bot))