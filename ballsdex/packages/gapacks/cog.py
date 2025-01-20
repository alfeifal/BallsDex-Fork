import discord
import logging
import random
import re

from discord.utils import get
from discord import app_commands
from discord.ext import commands

from ballsdex.settings import settings
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.settings import settings
from ballsdex.core.models import Player, BallInstance, specials, balls 
from ballsdex.packages.countryballs.countryball import CountryBall
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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.gaPacks")

class gaPacks(commands.Cog):
    """
    Simple vote commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command()
    @app_commands.checks.cooldown(1, 10, key=lambda i: i.user.id)
    async def vote(self, interaction: discord.Interaction,):
        """
        Get the link to the bot-vote Website.
        """
        await interaction.response.send_message(
                f"You can vote for {settings.bot_name} here: https://top.gg/user/578344716639604736\nOnce you have voted, you can make a ticket and claim a FREE CARD on our [Discord Server!]({settings.discord_invite})",
                ephemeral=True,
            )
        return
    
    @app_commands.command()
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def rarity_list(self, interaction: discord.Interaction):
        # DO NOT CHANGE THE CREDITS TO THE AUTHOR HERE!
        """
        Show the rarities of the dex - made by GamingadlerHD
        """
        # Filter enabled collectibles
        enabled_collectibles = [x for x in balls.values() if x.enabled]

        if not enabled_collectibles:
            await interaction.response.send_message(
                f"There are no collectibles registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        # Sort collectibles by rarity in ascending order
        sorted_collectibles = sorted(enabled_collectibles, key=lambda x: x.rarity)

        entries = []
        list1 = []
        list2 = []
        for collectible in sorted_collectibles:
            name = f"{collectible.country}"
            emoji = self.bot.get_emoji(collectible.emoji_id)

            if emoji:
                emote = str(emoji)
            else:
                emote = "N/A"
            # if you want the Rarity to only show full numbers like 1 or 12 use the code part here:
            # rarity = int(collectible.rarity)
            # otherwise you want to display numbers like 1.5, 5.3, 76.9 use the normal part.
            r = collectible.rarity
            if r in list2:
                list1.append(list1[-1])
            else:
                list1.append(len(list1) + 1)
            rarity = list1[-1]
            list2.append(r)

            entry = (name, f"{emote} Rarity: {rarity}")
            entries.append(entry)
        # This is the number of countryballs who are displayed at one page,
        # you can change this, but keep in mind: discord has an embed size limit.
        per_page = 5

        source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
        source.embed.description = (
            f"__**{settings.bot_name} rarity**__"
        )
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(
            name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
        )

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(
            ephemeral=True,
        )

        if settings.bot_name == "rocketleaguedex":
            await interaction.followup.send(f"```c\nPaint Rarities:\nTop 1. Mythical 🌌\nTop 2. Shiny ✨\nTop 3. Gold 🟨\nTop 3. Titanium White ⬜\nTop 5. Black ⬛\nTop 6. Cobalt 🟦\nTop 6. Crimson 🟥\nTop 6. Forest Green 🟩\nTop 6. Saffron 💛\nTop 6. Sky Blue 🩵\nTop 6. Pink 🩷\nTop 6. Purple 🟪\nTop 6. Lime 💚\nTop 6. Orange 🟧\nTop 6. Grey 🩶\nTop 6. Burnt Sienna 🟫\nTop 17. Unpainted ```",
            ephemeral=True,
        )

    @app_commands.command()
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def count_list(
            self,
            interaction: discord.Interaction,
            special: SpecialTransform | None = None, ):
        # DO NOT CHANGE THE CREDITS TO THE AUTHOR HERE!
        """
        Shows a count list of every character - made by GamingadlerHD and Mo Official

        Parameters
        ----------
        special: Special
        """
        # Filter enabled collectibles
        enabled_collectibles = [x for x in balls.values() if x.enabled]

        if not enabled_collectibles:
            await interaction.response.send_message(
                f"There are no collectibles registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        # Sort collectibles by rarity in ascending order
        sorted_collectibles = sorted(enabled_collectibles, key=lambda x: x.rarity)

        # Sort collectibles by rarity in ascending order

        entries = []
        nothingcheck = ""

        for collectible in sorted_collectibles:
            name = f"{collectible.country}"
            emoji = self.bot.get_emoji(collectible.emoji_id)

            if emoji:
                emote = str(emoji)
            else:
                emote = "N/A"

            filters = {}
            filters["ball"] = collectible
            filters["player__discord_id"] = interaction.user.id
            if special:
                filters["special"] = special

            count = await BallInstance.filter(**filters)
            countNum = len(count)
            # sorted_collectibles = sorted(enabled_collectibles.values(), key=lambda x: x.rarity)
            # if you want the Rarity to only show full numbers like 1 or 12 use the code part here:
            # rarity = int(collectible.rarity)
            # otherwise you want to display numbers like 1.5, 5.3, 76.9 use the normal part.
            if countNum != 0:
                entry = (name, f"{emote} Count: {countNum}")
                entries.append(entry)
                nothingcheck = "something lol"

        # This is the number of countryballs who are displayed at one page,
        # you can change this, but keep in mind: discord has an embed size limit.
        per_page = 5
        special_str = f" ({special.name})" if special else ""
        if nothingcheck == "":
            return await interaction.response.send_message(
                f"You have no {special_str} {settings.plural_collectible_name} yet.",
                ephemeral=True,
            )
        else:
            source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
            source.embed.description = (
                f"__**{settings.bot_name}{special_str} count**__"
            )
            source.embed.colour = discord.Colour.blurple()
            source.embed.set_author(
                name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
            )

            pages = Pages(source=source, interaction=interaction, compact=True)
            await pages.start(
                ephemeral=True,
            )
