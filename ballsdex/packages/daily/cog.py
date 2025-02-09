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
    Special,
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
        
    async def get_special(self) -> Optional[Special]:
        """Get a random special based on rarity and date validity."""
        now = datetime.datetime.now(datetime.timezone.utc)
        valid_specials = [
            special for special in specials.values()
            if (not special.start_date or special.start_date <= now)
            and (not special.end_date or special.end_date >= now)
            and not special.hidden
        ]
        
        for special in valid_specials:
            if random.random() < special.rarity:
                return special
        return None
        
    @app_commands.command()
    @app_commands.checks.cooldown(1, 86400, key=lambda i: i.user.id)
    async def pack(self, interaction: discord.Interaction):
        """
        Claim your daily pack! You can claim it once per day and receive 5 balls.
        """
        await interaction.response.defer(thinking=True)
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        # Create embed for the pack opening
        embed = discord.Embed(
            title="You've opened your pack successfully!",
            description="Here are your 5 new players:",
            color=0x3498db
        )

        # Get 5 random balls and create their instances
        log_message = f"{interaction.user} claimed their daily pack and received: "
        for i in range(5):
            # Get a random ball
            ball = await CountryBall.get_random()
            
            # Try to get a special
            special = await self.get_special()
            
            # Create and assign the instance
            instance = await BallInstance.create(
                ball=ball.model,
                player=player,
                attack_bonus=random.randint(-20, 20),
                health_bonus=random.randint(-20, 20),
                special=special,
            )
            
            # Add to embed
            special_text = f" [**{special.name}**]" if special else ""
            embed.add_field(
                name=f"Player {i+1}: {ball.name}{special_text}",
                value=f"`{instance.attack_bonus:+d} ATK / {instance.health_bonus:+d} HP`",
                inline=False
            )
            
            # Add to log message
            special_log = f" ({special.name})" if special else ""
            log_message += f"{ball.name}{special_log} ({instance.attack_bonus:+d} ATK, {instance.health_bonus:+d} HP), "

            # Add special catch phrase if applicable
            if special and special.catch_phrase:
                embed.add_field(
                    name="Congratulations.",
                    value=special.catch_phrase,
                    inline=False
                )

        embed.add_field(
            name="",
            value="**Your rewards have been given, come back tomorrow!**",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)
        
        # Remove trailing comma and space from log message
        log_message = log_message.rstrip(", ")
        await log_action(log_message, self.bot)
