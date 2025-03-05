import datetime
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, cast

import discord
from cachetools import TTLCache
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING
from tortoise.expressions import Q

from ballsdex.core.models import BallInstance, Player
from ballsdex.core.models import Trade as TradeModel
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.paginator import Pages
from ballsdex.core.utils.sorting import SortingChoices, sort_balls
from ballsdex.core.utils.transformers import (
    BallEnabledTransform,
    BallInstanceTransform,
    SpecialEnabledTransform,
    TradeCommandType,
)
from ballsdex.packages.trade.display import TradeViewFormat
from ballsdex.packages.trade.menu import BulkAddView, TradeMenu, TradeViewMenu
from ballsdex.packages.trade.trade_user import TradingUser
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class LineupManager:
    """
    Manages a user's lineup, tracking ball positions and interactions.
    """
    def __init__(self, user: discord.User, player: Player):
        """
        Initialize a LineupManager for a specific user.

        Parameters
        ----------
        user : discord.User
            The Discord user creating the lineup
        player : Player
            The corresponding player database model
        """
        self.user = user
        self.player = player
        self.lineup: Dict[str, Optional[BallInstance]] = {
            "GK": None,
            "LB": None,
            "CB1": None,
            "CB2": None,
            "RB": None,
            "LM": None,
            "CM1": None,
            "CM2": None,
            "RM": None,
            "ST1": None,
            "ST2": None
        }
        self.cancelled = False

    def add_ball(self, ball: BallInstanceTransform, position: str):
        """
        Add a countryball to a specific position in the lineup.
        """
        self.lineup[position] = ball

    def remove_ball(self, position: str) -> Optional[BallInstanceTransform]:
        """
        Remove a countryball from a specific position.
        """
        ball = self.lineup[position]
        self.lineup[position] = None
        return ball

    def is_position_filled(self, position: str) -> bool:
        """
        Check if a specific position is filled.
        """
        return self.lineup[position] is not None

    def is_lineup_complete(self) -> bool:
        """
        Check if all positions in the lineup are filled.
        """
        return all(self.lineup.values())

    def get_balls_display(self) -> str:
        """
        Generate a display of countryballs in the lineup.
        """
        display_lines = []
        for position, ball in self.lineup.items():
            if ball:
                display_lines.append(f"{position}: {ball.countryball.country}")
            else:
                display_lines.append(f"{position}: Empty")
        return "\n".join(display_lines)


class LineupMenu:
    """
    Manages the interactive menu for lineup creation.
    """
    def __init__(
        self, 
        cog: 'Lineup', 
        interaction: discord.Interaction, 
        manager: LineupManager
    ):
        """
        Initialize a LineupMenu.
        """
        self.cog = cog
        self.interaction = interaction
        self.manager = manager
        self.current_view: Optional[discord.ui.View] = None

    async def start(self):
        """
        Start the lineup creation process.
        """
        # Placeholder for initial setup
        pass

    def _get_manager(self, user: discord.User) -> LineupManager:
        """
        Get the LineupManager for a specific user.
        """
        if user.id == self.manager.user.id:
            return self.manager
        raise RuntimeError("User not in this lineup")

    async def user_cancel(self, manager: LineupManager):
        """
        Handle user cancellation of the lineup.
        """
        manager.cancelled = True
        # Remove the lineup from active lineups
        guild_id = self.interaction.guild_id
        channel_id = self.interaction.channel_id
        
        if guild_id in self.cog.lineups and channel_id in self.cog.lineups[guild_id]:
            self.cog.lineups[guild_id][channel_id].remove(self)

@app_commands.guild_only()
class Lineup(commands.GroupCog):
    """
    Create and manage team lineups using countryballs.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.lineups: TTLCache[int, dict[int, list[LineupMenu]]] = TTLCache(maxsize=999999, ttl=1800)
        self.POSITIONS = [
            "GK",   # Goalkeeper
            "LB",   # Left Back
            "CB1",  # Center Back 1
            "CB2",  # Center Back 2
            "RB",   # Right Back
            "LM",   # Left Midfielder
            "CM1",  # Central Midfielder 1
            "CM2",  # Central Midfielder 2
            "RM",   # Right Midfielder
            "ST1",  # Striker 1
            "ST2"   # Striker 2
        ]

    def get_lineup(
        self,
        interaction: discord.Interaction | None = None,
        *,
        channel: discord.TextChannel | None = None,
        user: discord.User | discord.Member = MISSING,
    ) -> tuple[LineupMenu, LineupManager] | tuple[None, None]:
        """
        Find an ongoing lineup creation for the given interaction.
        """
        guild: discord.Guild
        if interaction:
            guild = cast(discord.Guild, interaction.guild)
            channel = cast(discord.TextChannel, interaction.channel)
            user = interaction.user
        elif channel:
            guild = channel.guild
        else:
            raise TypeError("Missing interaction or channel")

        if guild.id not in self.lineups:
            self.lineups[guild.id] = defaultdict(list)
        if channel.id not in self.lineups[guild.id]:
            return (None, None)
        
        for lineup in self.lineups[guild.id][channel.id]:
            try:
                lineup_manager = lineup._get_manager(user)
                return (lineup, lineup_manager)
            except RuntimeError:
                continue
        
        return (None, None)

    @app_commands.command()
    async def begin(self, interaction: discord.Interaction["BallsDexBot"]):
        """
        Begin creating a lineup.
        """
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        # Check if user already has an ongoing lineup
        existing_lineup, _ = self.get_lineup(interaction)
        if existing_lineup:
            await interaction.response.send_message(
                "You already have an ongoing lineup creation.", ephemeral=True
            )
            return

        menu = LineupMenu(
            self, interaction, LineupManager(interaction.user, player)
        )
        self.lineups[interaction.guild.id][interaction.channel.id].append(menu)
        await menu.start()
        await interaction.response.send_message("Lineup creation started!", ephemeral=True)

    @app_commands.command()
    async def add(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
        position: str,
        special: SpecialEnabledTransform | None = None,
    ):
        """
        Add a countryball to a specific lineup position.

        Parameters
        ----------
        countryball: BallInstance
            The countryball you want to add to a position
        position: str
            The position in the lineup (GK, LB, CB1, CB2, RB, LM, CM1, CM2, RM, ST1, ST2)
        special: Special
            Filter the results of autocompletion to a special event. Ignored afterwards.
        """
        if not countryball:
            return

        # Validate position
        if position not in self.POSITIONS:
            await interaction.response.send_message(
                f"Invalid position. Choose from: {', '.join(self.POSITIONS)}", 
                ephemeral=True
            )
            return

        # Check ongoing lineup
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return

        # Check if position is already filled
        if lineup_manager.is_position_filled(position):
            await interaction.response.send_message(
                f"The {position} position is already filled.", ephemeral=True
            )
            return

        # Add countryball to lineup
        lineup_manager.add_ball(countryball, position)
        await interaction.response.send_message(
            f"{countryball.countryball.country} added to {position}.", ephemeral=True
        )

    @app_commands.command()
    async def remove(
        self,
        interaction: discord.Interaction,
        position: str,
    ):
        """
        Remove a countryball from a specific lineup position.

        Parameters
        ----------
        position: str
            The position to remove the countryball from
        """
        # Validate position
        if position not in self.POSITIONS:
            await interaction.response.send_message(
                f"Invalid position. Choose from: {', '.join(self.POSITIONS)}", 
                ephemeral=True
            )
            return

        # Check ongoing lineup
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return

        # Check if position is filled
        if not lineup_manager.is_position_filled(position):
            await interaction.response.send_message(
                f"No {settings.collectible_name} in the {position} position.", ephemeral=True
            )
            return

        # Remove countryball from position
        removed_ball = lineup_manager.remove_ball(position)
        await interaction.response.send_message(
            f"{settings.collectible_name.title()} removed from {position}.", ephemeral=True
        )

    @app_commands.command()
    async def view(self, interaction: discord.Interaction):
        """
        View the current lineup configuration.
        """
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return

        # Create lineup visualization
        lineup_display = (
            "       ST1   ST2     \n"
            "                    \n"
            "   LM   CM1  CM2   RM\n"
            "                    \n"
            "   LB   CB1  CB2   RB\n"
            "                    \n"
            "         GK         \n"
        )

        # Get players in positions
        players_display = lineup_manager.get_balls_display()
        await interaction.response.send_message(
            f"Current Lineup (4-4-2 Formation):\n```\n{lineup_display}\n```\n"
            f"{settings.collectible_name.title()}s:\n{players_display}", 
            ephemeral=True
        )

    @app_commands.command()
    async def cancel(self, interaction: discord.Interaction):
        """
        Cancel the current lineup creation.
        """
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return

        # Remove lineup from active lineups
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        self.lineups[guild_id][channel_id].remove(lineup)
        
        await interaction.response.send_message(
            "Lineup creation cancelled.", ephemeral=True
        )

# Additional supporting classes would be needed:
# 1. LineupMenu (similar to TradeMenu)
# 2. LineupManager (to manage ball positions, similar to TradingUser)