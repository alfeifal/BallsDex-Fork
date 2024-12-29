import logging

import discord
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING, Optional, cast

from ballsdex.core.models import BallInstance
from ballsdex.core.models import Player
from ballsdex.core.models import specials
from ballsdex.core.models import balls
from ballsdex.core.utils.transformers import BallEnabledTransform
from ballsdex.core.utils.transformers import SpecialEnabledTransform
from ballsdex.core.utils.transformers import SpecialTransform
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.collector.cog")

# HOW TO USE THIS PACKAGE.
# STEP 1:
# CREATE A JSON FILE NAMED "collector.json".
# WRITE THE DATA IN THE FOLLOWING FORMAT.
# {"ITEM_1" : NUMBER_OF_INSTANCES_NEEDED_TO_GET_COLLECTOR_FOR_THIS_ITEM ,  "ITEM_2" : NUMBER_OF_INSTANCES_NEEDED_TO_GET_COLLECTOR_FOR_THIS_ITEM}
# FOR EXAMPLE:
# {"China" : 100, "Japan" : 50, "India" : 30}
# STEP 2:
# CREATE A SPECIAL EVENT NAMED "collector".
# SET THE DATES SUCH THAT THE EVENT ENDS EVEN BEFORE IT IS CREATED.
# FOR EXAMPLE:
# IF YOU ARE MAKING THE EVENT ON 1ST JULY 2024,
# SET THE END DATE OF THE EVENT ON 30TH JUNE 2024 OR EARLIER.
# STEP 3:
# CHECK IF IT WORKS AND ENSURE THAT IT DOES.
# THAT'S ALL 

class Collector(commands.GroupCog):
    """
    Collector commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
    
    @app_commands.command()
    async def card(
        self,
        interaction: discord.Interaction,
        countryball: BallEnabledTransform,
        ):
        """
        Get the collector card for a player.

        Parameters
        ----------
        countryball: Ball
            The player you want to obtain the collector card for.
        """
          
        if interaction.response.is_done():
            return
        assert interaction.guild
        filters = {}
        checkfilter = {}
        if countryball:
            filters["ball"] = countryball
        await interaction.response.defer(ephemeral=True, thinking=True)
        special = [x for x in specials.values() if x.name == "Collector"][0]
        checkfilter["special"] = special
        checkfilter["player__discord_id"] = interaction.user.id
        checkfilter["ball"] = countryball
        checkcounter = await BallInstance.filter(**checkfilter).count()
        if checkcounter >= 1:
            return await interaction.followup.send(
                f"You already have a {countryball.country} collector ball."
            )
        filters["player__discord_id"] = interaction.user.id
        balls = await BallInstance.filter(**filters).count()
        if settings.bot_name == "dragonballdex":
            collector_number = int(round((((500*countryball.rarity) + 3245)/116),-1))
        else:
            collector_number = int(round((((235 * countryball.rarity) + 3245) / 116), -1))
        country = f"{countryball.country}"
        player, created = await Player.get_or_create(discord_id=interaction.user.id)
        if balls >= collector_number:
            await interaction.followup.send(
                f"Congrats! You are now a {country} collector.", 
                ephemeral=True
            )
            await BallInstance.create(
            ball=countryball,
            player=player,
            attack_bonus=0,
            health_bonus=0,
            special=special,
            )
        else:
            await interaction.followup.send(
                f"You need {collector_number} {country} to create a collector ball. You currently have {balls}"
            )

    @app_commands.command()
    async def list(self, interaction: discord.Interaction["BallsDexBot"]):
        # DO NOT CHANGE THE CREDITS TO THE AUTHOR HERE!
        """
        Show the collector card list of the dex - made by GamingadlerHD and Mo Official
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
            if settings.bot_name == "dragonballdex":
                rarity1 = int(round((((500 * collectible.rarity) + 3245) / 116), -1))
            else:
                rarity1 = int(round((((235 * collectible.rarity) + 3245) / 116), -1))

            entry = (name, f"{emote} Amount required: {rarity1}")
            entries.append(entry)
        # This is the number of countryballs who are displayed at one page,
        # you can change this, but keep in mind: discord has an embed size limit.
        per_page = 5

        source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
        source.embed.description = (
            f"__**{settings.bot_name} Collector Card List**__"
        )
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(
            name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
        )

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(
            ephemeral=True,
        )
          
