import logging

import discord
import random
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING, Optional, cast

from ballsdex.core.models import BallInstance
from ballsdex.core.models import Player
from ballsdex.core.models import balls
from ballsdex.core.models import regimes
from ballsdex.core.utils.transformers import BallEnabledTransform
from ballsdex.core.utils.transformers import SpecialEnabledTransform
from ballsdex.core.utils.transformers import SpecialTransform
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.duos.cog")

DUOS_AVAILABLE = {
    "Messi & Cristiano Ronaldo": (10, 10),
    "Van Basten & Van Nistelrooy": (10, 10),
    "Maldini & Franz Beckenbauer": (10, 10),
    "Zidane & Ronaldinho": (10, 10),
    "Maradona & Pelé": (10, 10),
    "Ramos & Piqué": (10, 10),
    "Cruyff & Gullit": (10, 10),
    "Henry & Mbappé": (10, 20),
    "Neuer & Buffon": (10, 10),
}

class Duos(commands.GroupCog):
    """
    Dream duos Commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    ccadmin = app_commands.Group(name="admin", description="admin commands for dream duos cards")

    
    @app_commands.command()
    @app_commands.describe(duo="Select a dream duo to craft")
    @app_commands.choices(duo=[app_commands.Choice(name=name, value=name) for name in DUOS_AVAILABLE])
    async def craft(self, interaction: discord.Interaction, duo: app_commands.Choice[str]):
        """
        Craft a dream duo card.
        """
        duo_name = duo.value

        await interaction.response.defer(ephemeral=True, thinking=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        # Obtener requisitos
        first_player, second_player = duo_name.split(" & ")
        first_needed, second_needed = DUOS_AVAILABLE[duo_name]

        # Contar los jugadores del usuario
        first_count = await BallInstance.filter(player=player, ball__country=first_player).count()
        second_count = await BallInstance.filter(player=player, ball__country=second_player).count()

        # Verificar si el usuario tiene suficientes jugadores
        if first_count < first_needed or second_count < second_needed:
            return await interaction.followup.send(
                f"You need {first_needed} {first_player} and {second_needed} {second_player} "
                f"to craft the {duo_name} duo. You currently have {first_count} {first_player} "
                f"and {second_count} {second_player}.", 
                ephemeral=True
            )

        # Crear la nueva duo ball
        await BallInstance.create(
            ball=duo_name,
            player=player,
            attack_bonus=random.randint(-20, 20),
            health_bonus=random.randint(-20, 20),
        )

        await interaction.followup.send(
            f"Congrats! You have crafted the {duo_name} dream duo card!", ephemeral=True
        )

    @app_commands.command()
    async def list(self, interaction: discord.Interaction):
        """
        List all available dream duos.
        """
        duo_list = "\n".join(
            [f"**{duo}**: {req[0]} {duo.split(' & ')[0]} + {req[1]} {duo.split(' & ')[1]}" for duo, req in DUOS_AVAILABLE.items()]
        )
        await interaction.response.send_message(f"**Available Dream Duos:**\n{duo_list}", ephemeral=True)
        






  
