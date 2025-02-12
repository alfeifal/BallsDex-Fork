import logging
import random
from random import randint

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, button
from tortoise.exceptions import DoesNotExist
from tortoise.transactions import atomic

from typing import TYPE_CHECKING, Optional, cast, Dict, Any, Tuple

from ballsdex.core.models import BallInstance, Player, Ball, specials
from ballsdex.core.utils.transformers import (
    BallEnabledTransform,
    BallTransform,
    SpecialEnabledTransform,
    SpecialTransform,
)
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.utils.sorting import SortingChoices, sort_balls
from ballsdex.settings import settings
from ballsdex.core.utils.logging import log_action

log = logging.getLogger("ballsdex.packages.duos.cog")

# =====================================
# Define your duos and their requirements here
# =====================================

DUOS_AVAILABLE: Dict[str, Dict[str, Any]] = {
    "Messi & Cristiano": {
        'requirements': {
            'Lionel Andres Messi': 20, 
            'Cristiano Ronaldo': 20,
        },
        'description': "The ultimate football duo combining the talents of Messi and CR7"
    },
    "Neuer & Buffon": {
        'requirements': {
            'Manuel Neuer': 20,
            'Gianluigi Buffon': 20,
        },
        'description': "Two legendary goalkeepers unite"
    },
    "Henry & Mbappe": {
        'requirements': {
            'Thierry Henry': 20,
            'Kylian Mbappe': 20,
        },
        'description': "French striking legends across generations"
    },
    "Cruyff & Gullit": {
        'requirements': {
            'Johan Cruyff': 20,
            'Ruud Gullit': 20,
        },
        'description': "The Dutch masters of total football"
    },
    "Maradona & Pele": {
        'requirements': {
            'Diego Armando Maradona': 20,
            'Edson Arantes Pele': 20,
        },
        'description': "The greatest of all time unite"
    },
    "Zizou & Dinho": {
        'requirements': {
            'Zinedine Zidane': 20,
            'Ronaldinho Gaucho': 20,
        },
        'description': "Masters of football artistry"
    },
    "Iniesta & Modric": {
        'requirements': {
            'Andres Iniesta': 20,
            'Luka Modric': 20,
        },
        'description': "The masters of the midfield"
    },
    "Baggio & Totti": {
        'requirements': {
            'Roberto Baggio': 20,
            'Francesco Totti': 20,
        },
        'description': "The greatest who died in different ways"
    },
    "Xavi & Pirlo": {
        'requirements': {
            'Xavi Hernandez': 20,
            'Andrea Pirlo': 20,
        },
        'description': "This kind of magic doesnt happen twice"
    },
    "Maldini & Franz B.": {
        'requirements': {
            'Paolo Maldini': 20,
            'Franz Beckenbauer': 20,
        },
        'description': "The ultimate defensive partnership"
    },
    "Van Basten & Van Nistelrooy": {
        'requirements': {
            'Marco Van Basten': 20,
            'Van Nistelrooy': 20,
        },
        'description': "Dutch striking perfection"
    },
    "Ramos & Pique": {
        'requirements': {
            'Sergio Ramos': 20,
            'Gerard Pique': 20,
        },
        'description': "Spain's legendary center-back pairing"
    },
}

class Duos(commands.GroupCog):
    """A cog for managing duo cards that can be crafted from combining individual cards."""
    
    def __init__(self, bot):
        self.bot = bot

    async def _get_player_and_check_duo(
        self, 
        interaction: discord.Interaction, 
        duo_name: str
    ) -> tuple[Optional[Player], Optional[Ball], Optional[str]]:
        """Helper method to get player and check duo validity."""
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        if duo_name not in DUOS_AVAILABLE:
            await interaction.followup.send(
                "The selected duo might be not in game yet and its just a preview.",
                ephemeral=True
            )
            return None, None, None
            
        duo_ball = await Ball.filter(country=duo_name).first()
        if not duo_ball:
            await interaction.followup.send(
                "This duo card is not available yet and its just a `preview` for future duo cards.",
                ephemeral=True
            )
            return None, None, None
            
        return player, duo_ball, duo_name

    async def _check_requirements(
        self,
        player: Player,
        requirements: dict[str, int],
        is_boost: bool = False
    ) -> Tuple[Dict[str, Tuple[Ball, list[BallInstance]]], Dict[str, Tuple[int, int]]]:
        """
        Check if a player has the required balls for crafting/boosting.
        
        Args:
            player: The player to check
            requirements: Dictionary of ball name to required amount
            is_boost: If True, only requires 1 of each ball instead of full amount
            
        Returns:
            Tuple of:
            - Dictionary mapping ball name to (Ball, list of instances)
            - Dictionary mapping ball name to (current_amount, required_amount)
        """
        boost_instances: Dict[str, Tuple[Ball, list[BallInstance]]] = {}
        progress_tracking: Dict[str, Tuple[int, int]] = {}

        for ball_name, required_amount in requirements.items():
            ball = await Ball.get_or_none(country=ball_name)
            if not ball:
                progress_tracking[ball_name] = (0, 1 if is_boost else required_amount)
                continue

            # Get available regular instances of this ball
            instances = await BallInstance.filter(
                player=player,
                ball=ball,
                special=None,  # Only regular balls
                locked=None,  # Not locked
                favorite=False,  # Not favorited
            )

            current_amount = len(instances)
            required = 1 if is_boost else required_amount
            progress_tracking[ball_name] = (current_amount, required)
            
            if current_amount >= required:
                boost_instances[ball_name] = (ball, instances)

        return boost_instances, progress_tracking

    def format_progress_message(
        self, 
        progress_tracking: Dict[str, Tuple[int, int]], 
        is_boost: bool = False
    ) -> str:
        """Format the progress message showing current/required amounts for each ball."""
        missing_items = []
        for ball_name, (current, required) in progress_tracking.items():
            if current >= required:
                missing_items.append(f"• {ball_name} ✅")
            else:
                if is_boost:
                    missing_items.append(f"• {ball_name}: `{current}/{required}`")
                else:
                    missing_items.append(f"• {ball_name}: `{current}/{required}`")
        
        action = "boost" if is_boost else "craft"
        return f"You don't have enough regular (non-special) balls to {action}:\n" + "\n".join(missing_items)

    @app_commands.command()
    @app_commands.describe(duo="Select a duo to create")
    @app_commands.choices(duo=[app_commands.Choice(name=name, value=name) for name in DUOS_AVAILABLE])
    async def craft(self, interaction: discord.Interaction, duo: app_commands.Choice[str]):
        """Craft a duo card using the required players."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            player, duo_ball, duo_name = await self._get_player_and_check_duo(interaction, duo.value)
            if not all([player, duo_ball, duo_name]):
                return
                
            # Check if player already has this duo
            existing_duo = await BallInstance.filter(player=player, ball=duo_ball).exists()
            if existing_duo:
                return await interaction.followup.send(
                    f"You already have a {duo_name} duo card! Use the `/duos boost` command to improve its stats instead.",
                    ephemeral=True
                )

            # Check requirements with progress tracking
            required_instances, progress_tracking = await self._check_requirements(
                player,
                DUOS_AVAILABLE[duo_name]['requirements'],
                is_boost=False
            )

            if len(required_instances) < len(DUOS_AVAILABLE[duo_name]['requirements']):
                return await interaction.followup.send(
                    self.format_progress_message(progress_tracking, is_boost=False),
                    ephemeral=True
                )

            # Ask for confirmation
            requirements_text = "\n".join(
                f"- {name}: {required_amount}" 
                for name, required_amount in DUOS_AVAILABLE[duo_name]['requirements'].items()
            )
            confirm_view = ConfirmChoiceView(
                interaction,
                accept_message=f"Creating the duo card {duo_name}...",
                cancel_message="Crafting canceled.",
            )
            
            await interaction.followup.send(
                f"Are you sure you want to craft the duo card {duo_name}? This will consume:\n`{requirements_text}`",
                ephemeral=True,
                view=confirm_view
            )

            await confirm_view.wait()
            if not confirm_view.value:
                return

            @atomic()
            async def create_duo():
                for _, (_, instances) in required_instances.items():
                    for instance in instances[:20]:  # Solo usar los primeros 20
                        await instance.delete()
                
                await BallInstance.create(
                    ball=duo_ball,
                    player=player,
                    attack_bonus=randint(-20, 20),
                    health_bonus=randint(-20, 20),
                    special=None,
                )

            await create_duo()

            await interaction.followup.send(
                f"Successfully crafted the {duo_name} duo card!",
                ephemeral=True
            )
            
            await log_action(
                f"{interaction.user} crafted a {duo_name} duo card.",
                self.bot
            )

        except Exception as e:
            log.exception("Error while crafting duo card")
            await interaction.followup.send(
                "An error occurred while crafting the duo card. Please try again later.",
                ephemeral=True
            )

    @app_commands.command()
    @app_commands.describe(duo="The duo card to boost")
    @app_commands.choices(duo=[app_commands.Choice(name=name, value=name) for name in DUOS_AVAILABLE])
    async def boost(self, interaction: discord.Interaction, duo: app_commands.Choice[str]):
        """Boost a duo card's stats using additional players."""
        await interaction.response.defer(ephemeral=True)
    
        try:
            player, duo_ball, duo_name = await self._get_player_and_check_duo(interaction, duo.value)
            if not all([player, duo_ball, duo_name]):
                return
            
            duo_instance = await BallInstance.filter(player=player, ball=duo_ball).first()
            if not duo_instance:
                return await interaction.followup.send(
                    f"You must get a `{duo_name}` card before boosting it, use `/duo craft`.",
                    ephemeral=True
                )

            # Check if already at max stats
            if duo_instance.attack_bonus >= 20 and duo_instance.health_bonus >= 20:
                return await interaction.followup.send(
                    f"Your {duo_name} duo card cannot be boosted further, it's already maxed",
                    ephemeral=True
                )

            # Check requirements with progress tracking for boost
            boost_instances, progress_tracking = await self._check_requirements(
                player,
                DUOS_AVAILABLE[duo_name]['requirements'],
                is_boost=True
            )

            if len(boost_instances) < len(DUOS_AVAILABLE[duo_name]['requirements']):
                return await interaction.followup.send(
                    self.format_progress_message(progress_tracking, is_boost=True),
                    ephemeral=True
                )

            # Calculate boost amount based on current stats
            boost_amount = min(
                20 - duo_instance.attack_bonus,
                20 - duo_instance.health_bonus,
                1  # Always boost by 1
            )

            if boost_amount <= 0:
                return await interaction.followup.send(
                    f"You don't have a {duo_name} duo card yet, claim it by using /duos craft.",
                    ephemeral=True
                )

            # Ask for confirmation
            requirements_text = "\n".join(
                f"- {name}: 1" for name in boost_instances.keys()
            )
            confirm_view = ConfirmChoiceView(
                interaction,
                accept_message=f"Boosting {duo_name}...",
                cancel_message="Boost canceled.",
            )

            await interaction.followup.send(
                f"Are you sure you want to boost your {duo_name} duo card? This will consume:\n`{requirements_text}`\n"
                f"This will give +1 to both ATK and HP.",
                ephemeral=True,
                view=confirm_view
            )

            await confirm_view.wait()
            if not confirm_view.value:
                return

            @atomic()
            async def boost_duo():
                for _, (_, instances) in boost_instances.items():
                    # Only delete one instance of each required ball
                    await instances[0].delete()

                duo_instance.attack_bonus += boost_amount
                duo_instance.health_bonus += boost_amount
                await duo_instance.save()

            await boost_duo()

            await interaction.followup.send(
                f"Successfully boosted your {duo_name} duo card! New stats: "
                f"`ATK:{duo_instance.attack_bonus:+d}% HP:{duo_instance.health_bonus:+d}`%",
                ephemeral=True
            )

            await log_action(
                f"{interaction.user} boosted their {duo_name} duo card by +{boost_amount} using "
                f"one of each required ball.",
                self.bot
            )

        except Exception as e:
            log.exception("Error while boosting duo card")
            await interaction.followup.send(
                "An error occurred while boosting the duo card. Please try again later.",
                ephemeral=True
            )
            
    @app_commands.command()
    async def list(self, interaction: discord.Interaction):
        """Show all available duos and their requirements."""
        await interaction.response.defer(ephemeral=True)

        try:
            embed = discord.Embed(
                title="Available Dream Duos",
                description="Here are all the available duo cards you can craft:",
                color=0x3498db
            )

            for duo_name, duo_info in DUOS_AVAILABLE.items():
                # Get the ball model for this duo
                duo_ball = await Ball.filter(country=duo_name).first()
                if not duo_ball:
                    continue

                # Get the duo emoji
                emoji = f"{self.bot.get_emoji(duo_ball.emoji_id)}" if duo_ball.emoji_id else "👥"

                # Format requirements with emojis
                requirements_lines = []
                for req_name, amount in duo_info['requirements'].items():
                    # Get the required ball model and its emoji
                    req_ball = await Ball.filter(country=req_name).first()
                    if req_ball:
                        req_emoji = f"{self.bot.get_emoji(req_ball.emoji_id)}" if req_ball.emoji_id else "🔵"
                        requirements_lines.append(f"• {req_emoji} {req_name}: {amount} needed")
                    else:
                        requirements_lines.append(f"• {req_name}: {amount} needed")

                requirements = "\n".join(requirements_lines)

                # Add description if available
                description = f"\n\n*{duo_info.get('description', '')}*" if duo_info.get('description') else ""

                field_value = f"**Requirements:**\n{requirements}{description}"

                embed.add_field(
                    name=f"{emoji} {duo_name}",
                    value=field_value,
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            log.exception("Error while listing duos")
            await interaction.followup.send(
                "An error occurred while listing the duos. Please try again later.",
                ephemeral=True
            )
