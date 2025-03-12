import datetime
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, cast, Dict, List, Tuple, Any
import io
import shutil
import os
import discord
from cachetools import TTLCache
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING
from tortoise.expressions import Q
from ballsdex.core.models import BallInstance, Player, Ball, Economy
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
import sqlite3
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont
import asyncio
import logging

# Set up logging to only log errors
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

class LineupDatabase:
    def __init__(self):
        self.db_path = Path("ballsdex/data/lineups.db")
        self.db_path.parent.mkdir(exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_lineups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                formation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                chemistry REAL NOT NULL,
                positions TEXT NOT NULL,
                UNIQUE(player_id, name)
            )
        """)
        conn.commit()
        conn.close()

    def save_lineup(self, player_id, name, formation, chemistry, positions):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM saved_lineups WHERE player_id = ?", (player_id,))
        count = cursor.fetchone()[0]
        if count >= 3:
            conn.close()
            return False
        cursor.execute("""
            INSERT INTO saved_lineups (player_id, name, formation, created_at, chemistry, positions)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (player_id, name, formation, datetime.datetime.utcnow().isoformat(), chemistry, json.dumps(positions)))
        lineup_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return lineup_id  # Return the ID of the newly created lineup

    def update_lineup(self, lineup_id: int, chemistry: float, positions: Dict[str, Any]):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE saved_lineups 
            SET chemistry = ?, positions = ?, created_at = ?
            WHERE id = ?
        """, (chemistry, json.dumps(positions), datetime.datetime.utcnow().isoformat(), lineup_id))
        conn.commit()
        conn.close()

    def get_lineups(self, player_id):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM saved_lineups WHERE player_id = ? ORDER BY created_at DESC", (player_id,))
        lineups = cursor.fetchall()
        conn.close()
        return lineups

    def delete_lineup(self, player_id, name):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM saved_lineups WHERE player_id = ? AND name = ?", (player_id, name))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted

class LineupSelectionView(discord.ui.View):
    def __init__(self, cog: 'Lineup', interaction: discord.Interaction, saved_lineups: list):
        super().__init__(timeout=180)  # Increased timeout to 180 seconds for more flexibility
        self.cog = cog
        self.interaction = interaction
        self.saved_lineups = saved_lineups if saved_lineups and isinstance(saved_lineups, list) else []
        self.selected_lineup = None
        
        # Create the select menu options
        self.select_options = [
            discord.SelectOption(label=f"{lineup['name']} (Chem: {lineup['chemistry']}%)", value=str(lineup['id']))
            for lineup in self.saved_lineups
        ]
        if not self.select_options:
            self.select_options = [discord.SelectOption(label="No lineups available", value="none", disabled=True)]
        
        # Add the select menu
        self.lineup_select = LineupSelect(self.select_options)
        self.add_item(self.lineup_select)
        
        # Add the view button
        self.view_button = LineupViewButton()
        self.add_item(self.view_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.interaction.user.id

class LineupSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Select a saved lineup",
            options=options,
            custom_id="lineup_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        
        if not view.saved_lineups:
            await interaction.response.edit_message(content="No lineups available to select.", view=None)
            view.stop()
            return
            
        view.selected_lineup = next((lineup for lineup in view.saved_lineups if str(lineup['id']) == self.values[0]), None)
        
        if not view.selected_lineup:
            await interaction.response.edit_message(content="Invalid lineup selected.", view=view)
            return
            
        for option in self.options:
            option.default = option.value == self.values[0]
            
        await interaction.response.edit_message(
            content=f"Selected lineup: {view.selected_lineup['name']}", 
            view=view
        )

class LineupViewButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="View", 
            style=discord.ButtonStyle.primary, 
            custom_id="view_button"
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        
        if not view.selected_lineup:
            await interaction.response.send_message("Please select a lineup first.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)  # Defer immediately to avoid timeout
        
        try:
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            manager = LineupManager(interaction.user, player, view.selected_lineup["formation"])
            manager.lineup_id = int(view.selected_lineup["id"])  # Set the lineup_id for this saved lineup
            positions = json.loads(view.selected_lineup["positions"])
            
            for position, data in positions.items():
                if data and "ball_id" in data:
                    try:
                        ball_instance = await BallInstance.get(id=data["ball_id"])
                        # Only fetch countryball if it exists, otherwise proceed without it
                        if not hasattr(ball_instance, 'countryball'):
                            try:
                                await ball_instance.fetch_related('countryball')
                            except Exception as rel_error:
                                logger.error(f"Failed to fetch 'countryball' for BallInstance {data['ball_id']}: {rel_error}")
                        manager.add_ball(ball_instance, position)
                    except BallInstance.DoesNotExist:
                        logger.error(f"BallInstance with ID {data['ball_id']} not found for position {position}")
                        await interaction.followup.send(f"Error: BallInstance with ID {data['ball_id']} not found.", ephemeral=True)
                        return
            
            # Load the lineup into the active session
            menu = LineupMenu(view.cog, interaction, manager)
            guild_id = interaction.guild.id
            channel_id = interaction.channel.id
            
            if guild_id not in view.cog.lineups:
                view.cog.lineups[guild_id] = defaultdict(list)
                
            # Clear any existing lineups for this user in the channel
            view.cog.lineups[guild_id][channel_id] = [
                m for m in view.cog.lineups[guild_id][channel_id] 
                if m.manager.user.id != interaction.user.id
            ]
            
            view.cog.lineups[guild_id][channel_id].append(menu)
            
            # Generate and send the lineup
            image_io = await view.cog._generate_lineup_image(manager, bot=view.cog.bot)
            image_file = discord.File(image_io, filename="lineup.png")
            
            balls_info = []
            for position, ball in manager.lineup.items():
                if ball:
                    country_name = getattr(ball, 'countryball', None) and ball.countryball.country or "Unknown"
                    economy_info = ""
                    if getattr(ball, 'countryball', None) and ball.countryball.cached_economy:
                        economy_name = ball.countryball.cached_economy.name
                        is_suitable = manager.is_ball_suitable_for_position(ball, position)
                        economy_info = f" - Economy: {economy_name}{' ✅' if is_suitable else ' ⚠️'}"
                    balls_info.append(f"{position}: {country_name}{economy_info}")
                else:
                    balls_info.append(f"{position}: Empty")
                    
            balls_display = "\n".join(balls_info)
            chemistry = manager.calculate_team_chemistry()
            chemistry_emoji = "⭐⭐⭐" if chemistry >= 80 else "⭐⭐" if chemistry >= 50 else "⭐"
            
            embed = discord.Embed(
                title=f"Lineup {view.selected_lineup['name']}",
                description=f"Formation: {view.selected_lineup['formation']}\nChemistry: {chemistry}%",
                color=discord.Color.green()
            )
            embed.add_field(name="Positions", value=balls_display, inline=False)
            embed.add_field(name="Team chemistry", value=f"{chemistry}% {chemistry_emoji}", inline=True)
            filled_positions = sum(1 for ball in manager.lineup.values() if ball is not None)
            total_positions = len(manager.lineup)
            embed.add_field(name="Progress", value=f"{filled_positions}/{total_positions} positions", inline=True)
            embed.set_image(url="attachment://lineup.png")
            
            await interaction.followup.send(
                content="Lineup viewed successfully.",
                embed=embed,
                file=image_file,
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Unexpected error in view_lineup: {e}")
            await interaction.followup.send(f"Unexpected error: {str(e)}", ephemeral=True)

class FormationTypes:
    FORMATIONS = {
        "4-4-2": {
            "positions": ["GK", "LB", "CB1", "CB2", "RB", "LM", "CM1", "CM2", "RM", "ST1", "ST2"],
            "display": [
                ["", "ST1", "ST2", ""],
                ["", "", "", ""],
                ["LM", "CM1", "CM2", "RM"],
                ["", "", "", ""],
                ["LB", "CB1", "CB2", "RB"],
                ["", "", "", ""],
                ["", "GK", "", ""]
            ],
            "field_coordinates": {
                "GK": (50, 300),
                "LB": (150, 120),
                "CB1": (150, 240),
                "CB2": (150, 360),
                "RB": (150, 480),
                "LM": (450, 120),
                "CM1": (450, 240),
                "CM2": (450, 360),
                "RM": (450, 480),
                "ST1": (660, 240),
                "ST2": (660, 360)
            }
        },
        "4-3-3": {
            "positions": ["GK", "LB", "CB1", "CB2", "RB", "CM1", "CM2", "CM3", "LW", "ST", "RW"],
            "display": [
                ["LW", "", "ST", "", "RW"],
                ["", "", "", "", ""],
                ["", "CM1", "CM2", "CM3", ""],
                ["", "", "", "", ""],
                ["LB", "CB1", "", "CB2", "RB"],
                ["", "", "", "", ""],
                ["", "", "GK", "", ""]
            ],
            "field_coordinates": {
                "GK": (50, 300),
                "LB": (180, 150),
                "CB1": (150, 240),
                "CB2": (150, 360),
                "RB": (180, 510),
                "CM1": (450, 180),
                "CM2": (450, 300),
                "CM3": (450, 420),
                "LW": (660, 120),
                "ST": (750, 300),
                "RW": (660, 480)
            }
        },
        "3-5-2": {
            "positions": ["GK", "CB1", "CB2", "CB3", "LWB", "CM1", "CM2", "CM3", "RWB", "ST1", "ST2"],
            "display": [
                ["", "ST1", "ST2", ""],
                ["", "", "", ""],
                ["LWB", "CM1", "CM2", "CM3", "RWB"],
                ["", "", "", "", ""],
                ["", "CB1", "CB2", "CB3", ""],
                ["", "", "", "", ""],
                ["", "", "GK", "", ""]
            ],
            "field_coordinates": {
                "GK": (50, 300),
                "CB1": (150, 180),
                "CB2": (150, 300),
                "CB3": (150, 420),
                "LWB": (450, 120),
                "CM1": (450, 240),
                "CM2": (450, 360),
                "CM3": (450, 480),
                "RWB": (450, 600),
                "ST1": (750, 240),
                "ST2": (750, 360)
            }
        }
    }

class LineupManager:
    def __init__(self, user: discord.User, player: Player, formation: str = "4-4-2"):
        self.user = user
        self.player = player
        self.formation = formation
        self.lineup: Dict[str, Optional[BallInstance]] = {
            position: None for position in FormationTypes.FORMATIONS[formation]["positions"]
        }
        self.position_economy_mapping: Dict[str, List[str]] = {
            "GK": ["GK"],
            "defense": ["LB", "CB1", "CB2", "CB3", "RB"],
            "balanced": ["LM", "CM1", "CM2", "CM3", "RM", "LWB", "RWB"],
            "attack": ["ST", "ST1", "ST2", "LW", "RW"]
        }
        self.economy_position_mapping: Dict[str, List[str]] = {}
        for eco_type, positions in self.position_economy_mapping.items():
            for pos in positions:
                if pos not in self.economy_position_mapping:
                    self.economy_position_mapping[pos] = []
                self.economy_position_mapping[pos].append(eco_type)
        self.cancelled = False
        self.lineup_id: Optional[int] = None  # Track the ID of the saved lineup

    def add_ball(self, ball: BallInstanceTransform, position: str):
        self.lineup[position] = ball

    def remove_ball(self, position: str) -> Optional[BallInstanceTransform]:
        ball = self.lineup[position]
        self.lineup[position] = None
        return ball

    def is_position_filled(self, position: str) -> bool:
        return self.lineup[position] is not None

    def is_lineup_complete(self) -> bool:
        return all(self.lineup.values())

    def get_balls_display(self) -> str:
        display_lines = []
        for position, ball in self.lineup.items():
            if ball:
                display_lines.append(f"{position}: {ball.countryball.country}")
            else:
                display_lines.append(f"{position}: Empty")
        return "\n".join(display_lines)

    def is_ball_suitable_for_position(self, ball: BallInstanceTransform, position: str) -> bool:
        if not ball.countryball.cached_economy:
            return False
        economy_name = ball.countryball.cached_economy.name.lower()
        recommended_economies = self.economy_position_mapping.get(position, [])
        return any(economy_name == eco.lower() for eco in recommended_economies)

    def calculate_team_chemistry(self) -> float:
        filled_positions = sum(1 for ball in self.lineup.values() if ball is not None)
        if filled_positions == 0:
            return 0
        matching_positions = sum(1 for pos, ball in self.lineup.items() 
                               if ball and self.is_ball_suitable_for_position(ball, pos))
        return round((matching_positions / filled_positions) * 100, 1)

    def get_formation_display(self) -> List[str]:
        formation_matrix = FormationTypes.FORMATIONS[self.formation]["display"]
        visual_matrix = []
        for row in formation_matrix:
            visual_row = []
            for position in row:
                if not position:
                    visual_row.append("   ")
                elif position in self.lineup:
                    ball = self.lineup.get(position)
                    if ball:
                        visual_row.append(f" ⚽ ")
                    else:
                        visual_row.append(f" {position} ")
                else:
                    visual_row.append(f" {position} ")
            visual_matrix.append("".join(visual_row))
        return visual_matrix

class FormationSelectionView(discord.ui.View):
    def __init__(self, cog: 'Lineup', interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.cog = cog
        self.interaction = interaction
        self.selected_formation = "4-4-2"

    @discord.ui.select(
        placeholder="Select a formation",
        options=[
            discord.SelectOption(label="4-4-2", description="Classic formation with two forwards", default=True),
            discord.SelectOption(label="4-3-3", description="Attacking formation with three forwards"),
            discord.SelectOption(label="3-5-2", description="Formation with three center-backs and wing-backs")
        ]
    )
    async def select_formation(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_formation = select.values[0]
        for option in select.options:
            option.default = option.label == self.selected_formation
        await interaction.response.edit_message(
            content=f"Selected formation: {self.selected_formation}", 
            view=self
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = ConfirmChoiceView(
            interaction,
            accept_message="Formation confirmed!",
            cancel_message="Formation selection cancelled."
        )
        await interaction.response.send_message(
            f"Do you want to confirm the {self.selected_formation} formation?",
            view=confirm_view,
            ephemeral=True
        )
        await confirm_view.wait()
        if confirm_view.value is True:
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            menu = LineupMenu(
                self.cog, interaction, LineupManager(interaction.user, player, self.selected_formation)
            )
            self.cog.lineups[interaction.guild.id][interaction.channel.id].append(menu)
            embed = discord.Embed(
                title="Lineup creation started",
                description=f"Formation: {self.selected_formation}",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Available commands",
                value="• `/lineup add` - Add a ball to a position\n"
                      "• `/lineup remove` - Remove a ball from a position\n"
                      "• `/lineup view` - View current lineup\n"
                      "• `/lineup suggest` - Suggest balls for empty positions\n"
                      "• `/lineup cancel` - Cancel creation"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()

class LineupMenu:
    def __init__(
        self, 
        cog: 'Lineup', 
        interaction: discord.Interaction, 
        manager: LineupManager
    ):
        self.cog = cog
        self.interaction = interaction
        self.manager = manager
        self.current_view: Optional[discord.ui.View] = None

    async def start(self):
        pass

    def _get_manager(self, user: discord.User) -> LineupManager:
        if user.id == self.manager.user.id:
            return self.manager
        raise RuntimeError("User not in this lineup")

    async def user_cancel(self, manager: LineupManager):
        manager.cancelled = True
        guild_id = self.interaction.guild_id
        channel_id = self.interaction.channel_id
        if guild_id in self.cog.lineups and channel_id in self.cog.lineups[guild_id]:
            self.cog.lineups[guild_id][channel_id].remove(self)

class ForcePlacementView(discord.ui.View):
    def __init__(self, user: discord.Member, interaction: discord.Interaction, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.user = user
        self.interaction = interaction
        self.value = None
        self.responded = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user.id
    
    async def _handle_response(self, interaction: discord.Interaction, content: str, value: bool):
        if not self.responded:
            self.responded = True
            self.value = value
            if not self.interaction.response.is_done():
                await self.interaction.edit_original_response(content=content, view=None)
            else:
                await self.interaction.followup.send(content=content, ephemeral=True)
            self.stop()

    @discord.ui.button(label="Place anyway", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_response(interaction, "Placing ball in the position...", True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_response(interaction, "Operation cancelled.", False)

class SuggestAutocompleteView(discord.ui.View):
    def __init__(self, lineup_manager: LineupManager, suggestions: Dict[str, List[BallInstance]], db: LineupDatabase):
        super().__init__(timeout=60)
        self.lineup_manager = lineup_manager
        self.suggestions = suggestions
        self.db = db

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.lineup_manager.user.id

    @discord.ui.button(label="Autocomplete", style=discord.ButtonStyle.primary, custom_id="autocomplete_button")
    async def autocomplete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        # Add the first suggested ball to each empty position
        placements = []
        for position, balls in self.suggestions.items():
            if balls and not self.lineup_manager.is_position_filled(position):
                ball = balls[0]  # Take the first suggested ball
                self.lineup_manager.add_ball(ball, position)
                placements.append(f"{ball.countryball.country} to {position}")

        # Update the database if this is a saved lineup
        if self.lineup_manager.lineup_id:
            positions_data = {}
            for pos, ball in self.lineup_manager.lineup.items():
                if ball:
                    positions_data[pos] = {
                        "ball_id": ball.id,
                        "country": ball.countryball.country,
                        "economy": ball.countryball.cached_economy.name if ball.countryball.cached_economy else "Unknown"
                    }
                else:
                    positions_data[pos] = None
            chemistry = self.lineup_manager.calculate_team_chemistry()
            self.db.update_lineup(self.lineup_manager.lineup_id, chemistry, positions_data)

        # Prepare the response
        chemistry = self.lineup_manager.calculate_team_chemistry()
        chemistry_emoji = "⭐⭐⭐" if chemistry >= 80 else "⭐⭐" if chemistry >= 50 else "⭐"
        
        if placements:
            embed = discord.Embed(
                title="Lineup Autocompleted",
                description="The following balls were added to your lineup:",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Placements",
                value="\n".join(placements),
                inline=False
            )
            embed.add_field(
                name="Team Chemistry",
                value=f"{chemistry}% {chemistry_emoji}",
                inline=True
            )
        else:
            embed = discord.Embed(
                title="No Changes Made",
                description="No suitable balls were added to the lineup.",
                color=discord.Color.yellow()
            )

        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()

class LineupSelectionForCommandView(discord.ui.View):
    def __init__(self, cog: 'Lineup', interaction: discord.Interaction, saved_lineups: list, command_type: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.interaction = interaction
        self.saved_lineups = saved_lineups if saved_lineups and isinstance(saved_lineups, list) else []
        self.selected_lineup = None
        self.command_type = command_type
        self.select_options = [
            discord.SelectOption(label=f"{lineup['name']} (Chem: {lineup['chemistry']}%)", value=str(lineup['id']))
            for lineup in self.saved_lineups
        ]
        
        if not self.select_options:
            self.select_options = [discord.SelectOption(label="No lineups available", value="none")]
            
        self.add_item(
            discord.ui.Select(
                placeholder="Select a saved lineup",
                options=self.select_options,
                custom_id="lineup_select"
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.interaction.user.id

    async def select_lineup(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not self.saved_lineups:
            await interaction.response.edit_message(content="No lineups available to select.", view=None)
            self.stop()
            return
        self.selected_lineup = next((lineup for lineup in self.saved_lineups if str(lineup['id']) == select.values[0]), None)
        if not self.selected_lineup:
            await interaction.response.edit_message(content="Invalid lineup selected.", view=self)
            return
        for option in select.options:
            option.default = option.value == select.values[0]
        await interaction.response.edit_message(
            content=f"Selected lineup: {self.selected_lineup['name']}", 
            view=self
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_lineup:
            await interaction.response.send_message("Please select a lineup first.", ephemeral=True)
            return
            
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        manager = LineupManager(interaction.user, player, self.selected_lineup["formation"])
        manager.lineup_id = int(self.selected_lineup["id"])  # Set the lineup_id for this saved lineup
        positions = json.loads(self.selected_lineup["positions"])
        for position, data in positions.items():
            if data and "ball_id" in data:
                try:
                    ball_instance = await BallInstance.get(id=data["ball_id"])
                    await ball_instance.fetch_related('countryball')
                    manager.add_ball(ball_instance, position)
                except BallInstance.DoesNotExist:
                    logger.error(f"BallInstance with ID {data['ball_id']} not found for position {position}")
        
        menu = LineupMenu(self.cog, interaction, manager)
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        if guild_id not in self.cog.lineups:
            self.cog.lineups[guild_id] = defaultdict(list)
        # Clear any existing lineups for this user in the channel
        self.cog.lineups[guild_id][channel_id] = [
            m for m in self.cog.lineups[guild_id][channel_id] 
            if m.manager.user.id != interaction.user.id
        ]
        self.cog.lineups[guild_id][channel_id].append(menu)
        
        await interaction.response.edit_message(
            content=f"Loaded lineup '{self.selected_lineup['name']}' for {self.command_type}.", 
            view=None
        )
        self.stop()

@app_commands.guild_only()
class Lineup(commands.GroupCog):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.lineups: TTLCache[int, dict[int, list[LineupMenu]]] = TTLCache(maxsize=999999, ttl=1800)
        self.POSITIONS = []
        self.db = LineupDatabase()
        self.resources_path = os.path.join(os.path.dirname(__file__), 'resources')
        os.makedirs(self.resources_path, exist_ok=True)
        self.field_png_path = os.path.join(self.resources_path, 'field.png')

    def get_lineup(
        self,
        interaction: discord.Interaction | None = None,
        *,
        channel: discord.TextChannel | None = None,
        user: discord.User | discord.Member = MISSING,
    ) -> tuple[LineupMenu, LineupManager] | tuple[None, None]:
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

    async def _generate_lineup_image(self, lineup_manager: LineupManager, bot: discord.Client = None) -> io.BytesIO:
        if not os.path.exists(self.field_png_path):
            placeholder_image = Image.new('RGB', (800, 600), color='gray')
            draw = ImageDraw.Draw(placeholder_image)
            draw.text((10, 10), "Field PNG not found.", fill='white')
            placeholder_io = io.BytesIO()
            placeholder_image.save(placeholder_io, format="PNG")
            placeholder_io.seek(0)
            return placeholder_io

        field_image = Image.open(self.field_png_path).convert("RGBA")

        wild_card_path = os.path.join(os.path.dirname(__file__), 'resources', 'wild-cards')
        os.makedirs(wild_card_path, exist_ok=True)

        base_media_path = "/code/admin_panel/media/"

        draw = ImageDraw.Draw(field_image)
        try:
            font_path = os.path.join(os.path.dirname(__file__), 'resources', 'DejaVuSans.ttf')
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, 24)
            else:
                # Try a more commonly available font
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
                except Exception:
                    font = ImageFont.load_default().font_variant(size=24)
        except Exception as e:
            logger.error(f"Error loading font: {e}, using default font")
            font = ImageFont.load_default().font_variant(size=24)

        field_coordinates = FormationTypes.FORMATIONS[lineup_manager.formation]["field_coordinates"]

        for position, coords in field_coordinates.items():
            x, y = coords
            ball = lineup_manager.lineup.get(position)
            if not ball:
                circle_radius = 30
                circle_bbox = [x - circle_radius, y - circle_radius, x + circle_radius, y + circle_radius]
                draw.ellipse(circle_bbox, fill=(255, 255, 255, 77))

        for position, coords in field_coordinates.items():
            x, y = coords
            ball_instance = lineup_manager.lineup.get(position)

            if ball_instance:
                countryball = getattr(ball_instance, 'countryball', None)
                if not countryball:
                    continue

                wild_card_file = getattr(countryball, 'wild_card', None)
                if not wild_card_file:
                    continue

                source_wild_card_path = os.path.join(base_media_path, wild_card_file)
                wild_card_filename = f"{ball_instance.id}_{wild_card_file}"
                target_wild_card_path = os.path.join(wild_card_path, wild_card_filename)

                # Check if the wild card image already exists in the target directory
                if not os.path.exists(target_wild_card_path):
                    try:
                        if os.path.exists(source_wild_card_path):
                            shutil.copy(source_wild_card_path, target_wild_card_path)
                        else:
                            continue
                    except Exception as e:
                        logger.error(f"Error copying wild card for {countryball.country}: {e}")
                        continue

                if os.path.exists(target_wild_card_path):
                    try:
                        wild_card_image = Image.open(target_wild_card_path).convert("RGBA")
                    except Exception as e:
                        logger.error(f"Error opening image {target_wild_card_path}: {e}")
                        continue

                    wild_card_image = wild_card_image.resize((80, 80), Image.LANCZOS)

                    image_x = int(x - (80 / 2))
                    image_y = int(y - (80 / 2))

                    field_image.paste(wild_card_image, (image_x, image_y), wild_card_image)
                    wild_card_image.close()

                    short_name = (countryball.short_name or countryball.country).upper()
                    text_x = image_x + (80 // 2)
                    text_y = image_y + 80 + 15

                    try:
                        text_bbox = draw.textbbox((0, 0), short_name, font=font, anchor="mt")
                        text_width = text_bbox[2] - text_bbox[0]
                        text_height = text_bbox[3] - text_bbox[1]
                    except AttributeError:
                        text_width = len(short_name) * 12
                        text_height = 24

                    outline_offsets = [
                        (-2, -2), (-2, 0), (-2, 2),
                        (0, -2), (0, 2),
                        (2, -2), (2, 0), (2, 2)
                    ]
                    for dx, dy in outline_offsets:
                        draw.text((text_x + dx, text_y + dy), short_name, fill="black", font=font, anchor="mt")

                    draw.text((text_x, text_y), short_name, fill="white", font=font, anchor="mt")

        final_image_io = io.BytesIO()
        field_image.save(final_image_io, format="PNG")
        final_image_io.seek(0)

        return final_image_io

    @app_commands.command(description="Start creating a new lineup by selecting a formation.")
    async def begin(self, interaction: discord.Interaction["BallsDexBot"]):
        existing_lineup, _ = self.get_lineup(interaction)
        if existing_lineup:
            await interaction.response.send_message(
                "You already have an ongoing lineup creation.", ephemeral=True
            )
            return
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        if guild_id not in self.lineups:
            self.lineups[guild_id] = defaultdict(list)
        view = FormationSelectionView(self, interaction)
        await interaction.response.send_message(
            "Select a formation for your lineup:", 
            view=view,
            ephemeral=True
        )

    @app_commands.command(description="Add a countryball to a specific position in the lineup.")
    @app_commands.describe(
        countryball="The countryball to add to a position",
        position="The position in the lineup (e.g., GK, LB, CB1)",
        special="Filter the results to a special event (optional)",
        force="Force placement even if not optimal for the position (optional)"
    )
    async def add(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
        position: str,
        special: SpecialEnabledTransform | None = None,
        force: bool = False,
    ):
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            player_id = interaction.user.id
            saved_lineups = self.db.get_lineups(player_id)
            if not saved_lineups:
                await interaction.response.send_message(
                    "You do not have an ongoing lineup creation. Use `/lineup begin` or select a saved lineup with `/lineup view`.", 
                    ephemeral=True
                )
                return
            view = LineupSelectionForCommandView(self, interaction, saved_lineups, "add")
            await interaction.response.send_message(
                "Select a lineup to add a ball to:", 
                view=view,
                ephemeral=True
            )
            return
        formation_positions = FormationTypes.FORMATIONS[lineup_manager.formation]["positions"]
        if position not in formation_positions:
            await interaction.response.send_message(
                f"Invalid position for formation {lineup_manager.formation}. Choose from: {', '.join(formation_positions)}", 
                ephemeral=True
            )
            return
        if lineup_manager.is_position_filled(position):
            await interaction.response.send_message(
                f"The {position} position is already filled.", ephemeral=True
            )
            return
        if countryball in lineup_manager.lineup.values():
            existing_position = next(pos for pos, ball in lineup_manager.lineup.items() 
                                    if ball and ball.id == countryball.id)
            await interaction.response.send_message(
                f"{countryball.ball.country} is already in your lineup at position {existing_position}.", 
                ephemeral=True
            )
            return
        await countryball.fetch_related('ball')
        is_suitable = lineup_manager.is_ball_suitable_for_position(countryball, position)
        # Restore original force behavior: skip prompt if force=True
        should_force = force
        if not is_suitable and not force:
            await interaction.response.defer(ephemeral=True)
            view = ForcePlacementView(interaction.user, interaction)
            economy_name = "Unknown"
            if countryball.ball.cached_economy:
                economy_name = countryball.ball.cached_economy.name
            await interaction.followup.send(
                f"{countryball.ball.country} is not ideal for the {position} position "
                f"based on its economy ({economy_name}).\n"
                f"Do you want to place it anyway?",
                view=view,
                ephemeral=True
            )
            await view.wait()
            should_force = view.value
            if not should_force:
                return
        if not is_suitable and not should_force:
            return
        lineup_manager.add_ball(countryball, position)
        
        # Automatically update the database if this is a saved lineup
        if lineup_manager.lineup_id:
            positions_data = {}
            for pos, ball in lineup_manager.lineup.items():
                if ball:
                    positions_data[pos] = {
                        "ball_id": ball.id,
                        "country": ball.countryball.country,
                        "economy": ball.countryball.cached_economy.name if ball.countryball.cached_economy else "Unknown"
                    }
                else:
                    positions_data[pos] = None
            chemistry = lineup_manager.calculate_team_chemistry()
            self.db.update_lineup(lineup_manager.lineup_id, chemistry, positions_data)

        if is_suitable:
            embed = discord.Embed(
                title="Ball added to lineup",
                description=f"{countryball.ball.country} added to {position}.",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Economy",
                value=f"{countryball.ball.cached_economy.name if countryball.ball.cached_economy else 'Unknown'} ✅",
                inline=True
            )
        else:
            embed = discord.Embed(
                title="Ball added to lineup",
                description=f"{countryball.ball.country} added to {position}.",
                color=discord.Color.yellow()
            )
            embed.add_field(
                name="Economy",
                value=f"{countryball.ball.cached_economy.name if countryball.ball.cached_economy else 'Unknown'} ⚠️",
                inline=True
            )
            embed.add_field(
                name="Warning",
                value="This position is not optimal for this economy type.",
                inline=False
            )
        chemistry = lineup_manager.calculate_team_chemistry()
        chemistry_emoji = "⭐⭐⭐" if chemistry >= 80 else "⭐⭐" if chemistry >= 50 else "⭐"
        embed.add_field(name="Team chemistry", value=f"{chemistry}% {chemistry_emoji}", inline=True)
        try:
            # Use response.send_message if not deferred, else followup
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send(
                f"{countryball.ball.country} added to {position}. "
                f"{'✅ Optimal position' if is_suitable else '⚠️ Non-optimal position'}", 
                ephemeral=True
            )

    @app_commands.command(description="Edit a position in the lineup by replacing or moving a countryball.")
    @app_commands.describe(
        countryball="The countryball you want to add to the position",
        position="The position in the lineup where you want to place the ball",
        special="Filter the results of autocompletion to a special event. Ignored afterwards.",
        force="Force placement even if not optimal for the position (optional)"
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        position: str,
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
        force: bool = False,
    ):
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            player_id = interaction.user.id
            saved_lineups = self.db.get_lineups(player_id)
            if not saved_lineups:
                await interaction.response.send_message(
                    "You do not have an ongoing or saved lineup creation. Use `/lineup begin` or select a saved lineup with `/lineup view`.", 
                    ephemeral=True
                )
                return
            view = LineupSelectionForCommandView(self, interaction, saved_lineups, "edit")
            await interaction.response.send_message(
                "Select a lineup to edit:", 
                view=view,
                ephemeral=True
            )
            return
        formation_positions = FormationTypes.FORMATIONS[lineup_manager.formation]["positions"]
        if position not in formation_positions:
            await interaction.response.send_message(
                f"Invalid position for formation {lineup_manager.formation}. Choose from: {', '.join(formation_positions)}", 
                ephemeral=True
            )
            return
        if countryball in lineup_manager.lineup.values():
            existing_position = next(pos for pos, ball in lineup_manager.lineup.items() 
                                    if ball and ball.id == countryball.id)
            if existing_position != position:
                await interaction.response.send_message(
                    f"{countryball.countryball.country} is already in your lineup at position {existing_position}. Moving to {position}.", 
                    ephemeral=True
                )
                lineup_manager.remove_ball(existing_position)
            else:
                await interaction.response.send_message(
                    f"{countryball.countryball.country} is already in position {position}.",
                    ephemeral=True
                )
                return
        old_ball = None
        if lineup_manager.is_position_filled(position):
            old_ball = lineup_manager.remove_ball(position)
        await countryball.fetch_related('countryball')
        is_suitable = lineup_manager.is_ball_suitable_for_position(countryball, position)
        # Restore original force behavior: skip prompt if force=True
        should_force = force
        if not is_suitable and not force:
            await interaction.response.defer(ephemeral=True)
            view = ForcePlacementView(interaction.user, interaction)
            economy_name = "Unknown"
            if countryball.countryball.cached_economy:
                economy_name = countryball.countryball.cached_economy.name
            await interaction.followup.send(
                f"{countryball.countryball.country} is not ideal for the {position} position "
                f"based on its economy ({economy_name}).\n"
                f"Do you want to place it anyway?",
                view=view,
                ephemeral=True
            )
            await view.wait()
            should_force = view.value
            if not should_force:
                if old_ball:
                    lineup_manager.add_ball(old_ball, position)
                return
        if not is_suitable and not should_force:
            if old_ball:
                lineup_manager.add_ball(old_ball, position)
            return
        lineup_manager.add_ball(countryball, position)
        
        # Automatically update the database if this is a saved lineup
        if lineup_manager.lineup_id:
            positions_data = {}
            for pos, ball in lineup_manager.lineup.items():
                if ball:
                    positions_data[pos] = {
                        "ball_id": ball.id,
                        "country": ball.countryball.country,
                        "economy": ball.countryball.cached_economy.name if ball.countryball.cached_economy else "Unknown"
                    }
                else:
                    positions_data[pos] = None
            chemistry = lineup_manager.calculate_team_chemistry()
            self.db.update_lineup(lineup_manager.lineup_id, chemistry, positions_data)

        if is_suitable:
            embed = discord.Embed(
                title="Ball position updated",
                description=f"{countryball.countryball.country} placed in {position}.",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Economy",
                value=f"{countryball.countryball.cached_economy.name if countryball.countryball.cached_economy else 'Unknown'} ✅",
                inline=True
            )
        else:
            embed = discord.Embed(
                title="Ball position updated",
                description=f"{countryball.countryball.country} placed in {position}.",
                color=discord.Color.yellow()
            )
            embed.add_field(
                name="Economy",
                value=f"{countryball.countryball.cached_economy.name if countryball.countryball.cached_economy else 'Unknown'} ⚠️",
                inline=True
            )
            embed.add_field(
                name="Warning",
                value="This position is not optimal for this economy type.",
                inline=False
            )
        if old_ball:
            embed.add_field(
                name="Replaced",
                value=f"Removed {old_ball.countryball.country} from {position}",
                inline=False
            )
        chemistry = lineup_manager.calculate_team_chemistry()
        chemistry_emoji = "⭐⭐⭐" if chemistry >= 80 else "⭐⭐" if chemistry >= 50 else "⭐"
        embed.add_field(name="Team chemistry", value=f"{chemistry}% {chemistry_emoji}", inline=True)
        try:
            # Use response.send_message if not deferred, else followup
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send(
                f"{countryball.countryball.country} added to {position}. "
                f"{'✅ Optimal position' if is_suitable else '⚠️ Non-optimal position'}", 
                ephemeral=True
            )

    @app_commands.command(description="Remove a countryball from a specific position in the lineup.")
    @app_commands.describe(
        position="The position in the lineup from which to remove a countryball"
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        position: str,
    ):
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            player_id = interaction.user.id
            saved_lineups = self.db.get_lineups(player_id)
            if not saved_lineups:
                await interaction.response.send_message(
                    "You do not have an ongoing lineup creation. Start one with /lineup begin or select a saved lineup with `/lineup view`.", 
                    ephemeral=True
                )
                return
            view = LineupSelectionForCommandView(self, interaction, saved_lineups, "remove")
            await interaction.response.send_message(
                "Select a lineup to remove a ball from:", 
                view=view,
                ephemeral=True
            )
            return
        formation_positions = FormationTypes.FORMATIONS[lineup_manager.formation]["positions"]
        if position not in formation_positions:
            await interaction.response.send_message(
                f"Invalid position for formation {lineup_manager.formation}. Choose from: {', '.join(formation_positions)}", 
                ephemeral=True
            )
            return
        if not lineup_manager.is_position_filled(position):
            await interaction.response.send_message(
                f"No {settings.collectible_name} in the {position} position.", ephemeral=True
            )
            return
        removed_ball = lineup_manager.remove_ball(position)
        
        # Automatically update the database if this is a saved lineup
        if lineup_manager.lineup_id:
            positions_data = {}
            for pos, ball in lineup_manager.lineup.items():
                if ball:
                    positions_data[pos] = {
                        "ball_id": ball.id,
                        "country": ball.countryball.country,
                        "economy": ball.countryball.cached_economy.name if ball.countryball.cached_economy else "Unknown"
                    }
                else:
                    positions_data[pos] = None
            chemistry = lineup_manager.calculate_team_chemistry()
            self.db.update_lineup(lineup_manager.lineup_id, chemistry, positions_data)

        chemistry = lineup_manager.calculate_team_chemistry()
        chemistry_emoji = "⭐⭐⭐" if chemistry >= 80 else "⭐⭐" if chemistry >= 50 else "⭐"
        embed = discord.Embed(
            title="Ball removed from lineup",
            description=f"{removed_ball.countryball.country} removed from {position}.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Team chemistry", value=f"{chemistry}% {chemistry_emoji}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="View your current or saved lineup with details and an image.")
    async def view(self, interaction: discord.Interaction):
        player_id = interaction.user.id
        saved_lineups = self.db.get_lineups(player_id)
        
        # Check if there's no saved lineup and no ongoing lineup
        current_lineup = self.get_lineup(interaction)
        if not saved_lineups and current_lineup[0] is None:
            await interaction.response.send_message(
                "You do not have an ongoing or saved lineup creation.", ephemeral=True
            )
            return
        
        # Handle saved lineups case
        if saved_lineups:
            view = LineupSelectionView(self, interaction, saved_lineups)
            initial_message = await interaction.response.send_message(
                "Select a lineup to view:", 
                view=view,
                ephemeral=True
            )
            # Keep the view active for further selections
        # Handle ongoing lineup case
        else:
            lineup, lineup_manager = self.get_lineup(interaction)
            if not lineup or not lineup_manager:
                await interaction.response.send_message(
                    "You do not have an ongoing lineup creation.", ephemeral=True
                )
                return
            
            visual_matrix = lineup_manager.get_formation_display()
            lineup_display = "\n".join(visual_matrix)
            balls_info = []
            
            # Loop through positions and handle missing relationship errors
            for position, ball in lineup_manager.lineup.items():
                if ball:
                    try:
                        if not hasattr(ball, 'countryball') or ball.countryball is None:
                            # Handle missing countryball relationship
                            balls_info.append(f"{position}: Unknown ball (ID: {ball.id if hasattr(ball, 'id') else 'Unknown'})")
                            continue
                        
                        economy_info = ""
                        if hasattr(ball.countryball, 'cached_economy') and ball.countryball.cached_economy:
                            economy_name = ball.countryball.cached_economy.name
                            is_suitable = lineup_manager.is_ball_suitable_for_position(ball, position)
                            economy_info = f" - Economy: {economy_name}"
                            if is_suitable:
                                economy_info += " ✅"
                            else:
                                economy_info += " ⚠️"
                        balls_info.append(f"{position}: {ball.countryball.country}{economy_info}")
                    except Exception as e:
                        # Log the error and add a placeholder for the problematic ball
                        logger.error(f"Error processing ball for position {position}: {e}")
                        balls_info.append(f"{position}: Ball data error")
                else:
                    balls_info.append(f"{position}: Empty")
            
            balls_display = "\n".join(balls_info)
            
            # Only calculate chemistry if we have valid balls with relationships
            try:
                chemistry = lineup_manager.calculate_team_chemistry()
                chemistry_emoji = "⭐⭐⭐" if chemistry >= 80 else "⭐⭐" if chemistry >= 50 else "⭐"
            except Exception as e:
                logger.error(f"Error calculating team chemistry: {e}")
                chemistry = 0
                chemistry_emoji = "⭐"
            
            embed = discord.Embed(
                title=f"Lineup {lineup_manager.formation}",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Positions", 
                value=balls_display, 
                inline=False
            )
            embed.add_field(
                name="Team chemistry", 
                value=f"{chemistry}% {chemistry_emoji}", 
                inline=True
            )
            
            # Count valid positions
            filled_positions = sum(1 for ball in lineup_manager.lineup.values() 
                                if ball is not None and hasattr(ball, 'countryball') and ball.countryball is not None)
            total_positions = len(lineup_manager.lineup)
            embed.add_field(
                name="Progress", 
                value=f"{filled_positions}/{total_positions} positions", 
                inline=True
            )
            
            try:
                image_io = await asyncio.wait_for(self._generate_lineup_image(lineup_manager, bot=self.bot), timeout=30)
                image_file = discord.File(image_io, filename="lineup.png")
                embed.set_image(url="attachment://lineup.png")
                await interaction.response.send_message(embed=embed, file=image_file, ephemeral=True)
            except asyncio.TimeoutError:
                await interaction.response.send_message(
                    "Image generation timed out. Please try again.", ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error generating lineup image in view: {e}")
                await interaction.response.send_message(
                    f"Error generating image: {str(e)}. Showing text only.", 
                    embed=embed, 
                    ephemeral=True
                )

    @app_commands.command(description="Suggest countryballs for empty positions based on economy suitability.")
    async def suggest(self, interaction: discord.Interaction):
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return
        empty_positions = [pos for pos, ball in lineup_manager.lineup.items() if ball is None]
        if not empty_positions:
            await interaction.response.send_message(
                "All positions are already filled in your lineup.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        player_balls = await BallInstance.filter(player__discord_id=interaction.user.id)
        used_ball_ids = [ball.id for ball in lineup_manager.lineup.values() if ball]
        available_balls = [ball for ball in player_balls if ball.id not in used_ball_ids]
        for ball in available_balls:
            if not hasattr(ball, 'countryball'):
                await ball.fetch_related('countryball')
            if not hasattr(ball.countryball, 'cached_economy'):
                await ball.countryball.fetch_related('cached_economy')
        suggestions = {}
        for position in empty_positions:
            suitable_economies = lineup_manager.economy_position_mapping.get(position, [])
            suitable_balls = []
            for ball in available_balls:
                if not ball.countryball.cached_economy:
                    continue
                economy_name = ball.countryball.cached_economy.name.lower()
                if any(economy_name == eco.lower() for eco in suitable_economies):
                    suitable_balls.append(ball)
            # Only include balls with matching economies, no fallback to non-matching balls
            suggestions[position] = suitable_balls[:3] if suitable_balls else []
        embed = discord.Embed(
            title="Suggestions for your lineup",
            description=f"Based on your {lineup_manager.formation} formation",
            color=discord.Color.blue()
        )
        for position, balls in suggestions.items():
            if balls:
                ball_text = "\n".join([
                    f"• {ball.countryball.country} - {ball.countryball.cached_economy.name if ball.countryball.cached_economy else 'Unknown economy'}"
                    for ball in balls
                ])
            else:
                ball_text = "No suitable balls found."
            embed.add_field(
                name=f"Position: {position}",
                value=ball_text,
                inline=False
            )
        embed.set_footer(text="Use /lineup add to add a ball to a position, or press Autocomplete to fill all positions.")
        
        # Add the Autocomplete button via a view
        view = SuggestAutocompleteView(lineup_manager, suggestions, self.db)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(description="Save the current lineup with a given name.")
    @app_commands.describe(
        name="The name to save the lineup under (must be unique)"
    )
    async def save(self, interaction: discord.Interaction, name: str):
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return
        filled_positions = sum(1 for ball in lineup_manager.lineup.values() if ball is not None)
        if filled_positions == 0:
            await interaction.response.send_message(
                "Cannot save an empty lineup. Add some balls first.", ephemeral=True
            )
            return
        positions_data = {}
        for position, ball in lineup_manager.lineup.items():
            if ball:
                positions_data[position] = {
                    "ball_id": ball.id,
                    "country": ball.countryball.country,
                    "economy": ball.countryball.cached_economy.name if ball.countryball.cached_economy else "Unknown"
                }
            else:
                positions_data[position] = None
        player_id = interaction.user.id
        chemistry = lineup_manager.calculate_team_chemistry()
        lineup_id = self.db.save_lineup(player_id, name, lineup_manager.formation, chemistry, positions_data)
        if not lineup_id:
            await interaction.response.send_message(
                "You have reached the maximum of 3 saved lineups. Please delete one to save a new one.",
                ephemeral=True
            )
            return
        lineup_manager.lineup_id = lineup_id  # Set the lineup_id after saving
        await interaction.response.defer(ephemeral=True)
        image_io = await self._generate_lineup_image(lineup_manager, bot=self.bot)
        image_file = discord.File(image_io, filename="saved_lineup.png")
        embed = discord.Embed(
            title=f"Lineup '{name}' saved",
            description=f"Formation: {lineup_manager.formation}\nChemistry: {chemistry}%",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Filled positions", 
            value=f"{filled_positions}/{len(lineup_manager.lineup)}", 
            inline=True
        )
        embed.set_image(url="attachment://saved_lineup.png")
        guild_id = interaction.guild.id
        channel_id = interaction.channel.id
        if guild_id in self.lineups and channel_id in self.lineups[guild_id]:
            if lineup in self.lineups[guild_id][channel_id]:
                self.lineups[guild_id][channel_id].remove(lineup)
        await interaction.followup.send(
            content="Lineup saved successfully. The creation session has ended.",
            embed=embed,
            file=image_file,
            ephemeral=True
        )

    @app_commands.command(description="Delete a saved lineup by its name.")
    @app_commands.describe(
        name="The name of the lineup to delete"
    )
    async def delete(self, interaction: discord.Interaction, name: str):
        player_id = interaction.user.id
        deleted = self.db.delete_lineup(player_id, name)
        if not deleted:
            await interaction.response.send_message(
                f"No lineup found with the name '{name}'.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Lineup '{name}' has been deleted.", ephemeral=True
        )

    @app_commands.command(description="Cancel the current lineup creation session.")
    async def cancel(self, interaction: discord.Interaction):
        lineup, lineup_manager = self.get_lineup(interaction)
        if not lineup or not lineup_manager:
            await interaction.response.send_message(
                "You do not have an ongoing lineup creation.", ephemeral=True
            )
            return
        await lineup.user_cancel(lineup_manager)
        await interaction.response.send_message(
            "Lineup creation cancelled.", ephemeral=True
        )

async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Lineup(bot))