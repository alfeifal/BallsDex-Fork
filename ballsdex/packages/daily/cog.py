import datetime
import logging
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button
from discord.utils import format_dt
from tortoise.exceptions import BaseORMException, DoesNotExist, IntegrityError
from tortoise.expressions import Q
from ballsdex.core.models import PrivacyPolicy
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.models import Player as PlayerModel

from ballsdex.core.models import (
    Ball,
    BallInstance,
    BlacklistedGuild,
    BlacklistedID,
    GuildConfig,
    Player,
    Trade,
    TradeObject,
    balls,
    specials,
)
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.logging import log_action
from ballsdex.core.utils.paginator import FieldPageSource, Pages, TextPageSource
from ballsdex.core.utils.transformers import (
    BallTransform,
    EconomyTransform,
    RegimeTransform,
    SpecialTransform,
    BallEnabledTransform,
    BallInstanceTransform,
    SpecialEnabledTransform,
    TradeCommandType,
)
from ballsdex.packages.countryballs.countryball import CountryBall
from ballsdex.packages.trade.display import TradeViewFormat, fill_trade_embed_fields
from ballsdex.packages.trade.trade_user import TradingUser
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.countryballs.cog import CountryBallsSpawner

log = logging.getLogger("ballsdex.packages.daily.cog")

class daily(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        
    @app_commands.command()
    @app_commands.checks.cooldown(1, 86400, key=lambda i: i.user.id)
    async def pack(self, interaction: discord.Interaction):
        """
        Claim your daily pack! You can claim it once per day.
        """
        await interaction.response.defer(thinking=True)
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        # Get a random ball
        ball = await CountryBall.get_random()

        # Create and assign the instance
        instance = await BallInstance.create(
            ball=ball.model,
            player=player,
            attack_bonus=random.randint(-20, 20),
            health_bonus=random.randint(-20, 20),
            special=None,
        )
        
        embed = discord.Embed(
            title="You've opened your pack successfully!",
            description=f"Your chosen player is: `{ball.name}`",
            color=0x3498db  # Color del Embed (puedes personalizarlo con el código hexadecimal)
    )
        embed.add_field(
            name="Player Stats",
            value=f"`{instance.attack_bonus:+d} ATK / {instance.health_bonus:+d} HP`",
            inline=False
    )    
        embed.add_field(
            name="",
            value="**Your reward has been given, come back tomorrow!**",
            inline=False
    )
        
        await interaction.followup.send(embed=embed)
    
        await log_action(
            f"{interaction.user} claimed their daily pack and received {ball.name} ({instance.attack_bonus:+d} ATK, {instance.health_bonus:+d} HP).",
            self.bot,
    )
