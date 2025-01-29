import logging

import discord
import random
from discord import app_commands
from discord.ext import commands

from typing import TYPE_CHECKING, Optional, cast

from ballsdex.core.models import BallInstance
from ballsdex.core.models import Player
from ballsdex.core.models import balls
from ballsdex.core.models import specials
from ballsdex.core.utils.transformers import BallEnabledTransform
from ballsdex.core.utils.transformers import SpecialEnabledTransform
from ballsdex.core.utils.transformers import SpecialTransform
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.duos.cog")

DUOS_AVAILABLE = {
    "Lionel Andres Messi & Cristiano Ronaldo": (10, 10),
    "Van Basten & Van Nistelrooy": (10, 10),
    "Paolo Maldini & Franz Beckenbauer": (10, 10),
    "Zinedine Zidane & Ronaldinho Gaucho": (10, 10),
    "Diego Armando Maradona & Pele": (10, 10),
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

        Parameters
        ----------
        duo: Ball
            The players which duo card you want to claim.
        """
        duo_name = duo.value

        await interaction.response.defer(ephemeral=True, thinking=True)
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)

        # Obtener los nombres de los jugadores del dúo
        first_countryball, second_countryball = duo_name.split(" & ")
        first_needed, second_needed = DUOS_AVAILABLE[duo_name]

        # Contar los jugadores del usuario, excluyendo los especiales
        first_count = await BallInstance.filter(
            player=player, ball__country=first_countryball
        ).exclude(special=True).count()

        second_count = await BallInstance.filter(
            player=player, ball__country=second_countryball
        ).exclude(special=True).count()

        # Verificar si el usuario tiene suficientes jugadores
        if first_count < first_needed or second_count < second_needed:
            return await interaction.followup.send(
                f"You need {first_needed} {first_countryball} and {second_needed} {second_countryball} "
                f"to craft the {duo_name} duo. You currently have {first_count} {first_countryball} "
                f"and {second_count} {second_countryball}.", 
                ephemeral=True
            )

        # Pedir confirmación antes de eliminar las cartas
        confirm_message = await interaction.followup.send(
            f"Are you sure you want to craft the {duo_name} dream duo card? This will consume "
            f"{first_needed} {first_countryball} and {second_needed} {second_countryball}.",
            ephemeral=True,
            view=ConfirmChoiceView(
                interaction,
                accept_message=f"Confirmed, crafting the {duo_name} duo card...",
                cancel_message="Request cancelled.",
            )
        )

        # Esperar la confirmación
        view = ConfirmChoiceView(interaction, 
                                 accept_message=f"Confirmed, crafting the {duo_name} duo card...", 
                                 cancel_message="Request cancelled.")
        await view.wait()

        if not view.value:  # if the user did not confirm (cancelled)
            return await interaction.followup.send("Action cancelled.", ephemeral=True)

        # Eliminar las cartas necesarias
        first_countryball = await BallInstance.filter(
            player=player, ball__country=first_countryball
        ).exclude(special=True).limit(first_needed)
        
        second_countryball = await BallInstance.filter(
            player=player, ball__country=second_countryball
        ).exclude(special=True).limit(second_needed)

        for ball in first_countryball:
            await ball.delete()

        for ball in second_countryball:
            await ball.delete()

        # Crear la nueva duo ball
        await BallInstance.create(
            ball=duo_name,
            player=player,
            attack_bonus=random.randint(-20, 20),
            health_bonus=random.randint(-20, 20),
        )

        await interaction.followup.send(
            f"Congrats! You have crafted the {duo_name} dream duo card and the used {first_countryball} and "
            f"{second_countryball} cards have been deleted.", ephemeral=True
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
