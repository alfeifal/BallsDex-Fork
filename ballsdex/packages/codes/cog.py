import logging
import random
import string
import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands
from tortoise import timezone
from random import randint

from ballsdex.core.bot import BallsDexBot
from ballsdex.core.models import Ball, BallInstance, Player, Special
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.logging import log_action
from ballsdex.core.utils.transformers import (
    BallTransform,
    EconomyTransform,
    RegimeTransform,
    SpecialTransform,
)
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.codes")

def generate_code(length: int = 12) -> str:
    """Generate a random alphanumeric code."""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

class RedemptionCodeJSON:
    """Class to represent redemption codes stored in JSON."""
    
    def __init__(
        self,
        code: str,
        created_by: int,
        created_at: datetime,
        name: str,
        expires_at: Optional[datetime] = None,
        max_uses: int = 0,
        uses: int = 0,
        active: bool = True,
        description: Optional[str] = None,
        ball_id: int = None,
        regime_id: Optional[int] = None,
        economy_id: Optional[int] = None,
        special_id: Optional[int] = None,
        quantity: int = 1,
        attack_bonus: int = 0,
        health_bonus: int = 0,
        min_attack_bonus: int = -20,
        max_attack_bonus: int = 20,
        min_health_bonus: int = -20,
        max_health_bonus: int = 20
    ):
        self.code = code
        self.created_by = created_by
        self.created_at = created_at
        self.name = name
        self.expires_at = expires_at
        self.max_uses = max_uses
        self.uses = uses
        self.active = active
        self.description = description
        self.ball_id = ball_id
        self.regime_id = regime_id
        self.economy_id = economy_id
        self.special_id = special_id
        self.quantity = quantity
        self.attack_bonus = attack_bonus
        self.health_bonus = health_bonus
        self.min_attack_bonus = min_attack_bonus
        self.max_attack_bonus = max_attack_bonus
        self.min_health_bonus = min_health_bonus
        self.max_health_bonus = max_health_bonus
        
        # These will be populated when needed
        self.ball = None
        self.special = None
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'RedemptionCodeJSON':
        """Create a RedemptionCodeJSON object from a dictionary."""
        return cls(
            code=data['code'],
            created_by=data['created_by'],
            created_at=datetime.fromisoformat(data['created_at']),
            name=data.get('name', 'Unnamed Code'),
            expires_at=datetime.fromisoformat(data['expires_at']) if data.get('expires_at') else None,
            max_uses=data.get('max_uses', 0),
            uses=data.get('uses', 0),
            active=data.get('active', True),
            description=data.get('description'),
            ball_id=data.get('ball_id'),
            regime_id=data.get('regime_id'),
            economy_id=data.get('economy_id'),
            special_id=data.get('special_id'),
            quantity=data.get('quantity', 1),
            attack_bonus=data.get('attack_bonus', 0),
            health_bonus=data.get('health_bonus', 0),
            min_attack_bonus=data.get('min_attack_bonus', -20),
            max_attack_bonus=data.get('max_attack_bonus', 20),
            min_health_bonus=data.get('min_health_bonus', -20),
            max_health_bonus=data.get('max_health_bonus', 20)
        )
    
    def to_dict(self) -> Dict:
        """Convert this object to a dictionary for JSON storage."""
        return {
            'code': self.code,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat(),
            'name': self.name,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'max_uses': self.max_uses,
            'uses': self.uses,
            'active': self.active,
            'description': self.description,
            'ball_id': self.ball_id,
            'regime_id': self.regime_id,
            'economy_id': self.economy_id,
            'special_id': self.special_id,
            'quantity': self.quantity,
            'attack_bonus': self.attack_bonus,
            'health_bonus': self.health_bonus,
            'min_attack_bonus': self.min_attack_bonus,
            'max_attack_bonus': self.max_attack_bonus,
            'min_health_bonus': self.min_health_bonus,
            'max_health_bonus': self.max_health_bonus
        }

class RedeemCodes(commands.Cog):
    """
    Manage and redeem codes for rewards using JSON files.
    """
    
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.codes_file = os.path.join(os.path.dirname(__file__), "redemption_codes.json")
        self.redemptions_file = os.path.join(os.path.dirname(__file__), "code_redemptions.json")
        self.announcement_channel_id = 1344760924828864632
        
        # Create files if they don't exist
        if not os.path.exists(self.codes_file):
            with open(self.codes_file, 'w') as f:
                json.dump([], f)
        
        if not os.path.exists(self.redemptions_file):
            with open(self.redemptions_file, 'w') as f:
                json.dump([], f)

    async def _load_codes(self) -> List[RedemptionCodeJSON]:
        """Load all codes from the JSON file."""
        try:
            with open(self.codes_file, 'r') as f:
                data = json.load(f)
            return [RedemptionCodeJSON.from_dict(item) for item in data]
        except Exception as e:
            log.error(f"Error loading redemption codes: {e}")
            return []

    async def _save_codes(self, codes: List[RedemptionCodeJSON]) -> None:
        """Save codes to the JSON file."""
        try:
            with open(self.codes_file, 'w') as f:
                json.dump([code.to_dict() for code in codes], f, indent=2)
        except Exception as e:
            log.error(f"Error saving redemption codes: {e}")

    async def _get_code_by_name(self, name: str) -> Optional[RedemptionCodeJSON]:
        """Get a specific code by its name."""
        codes = await self._load_codes()
        # Convert both to lowercase for case-insensitive comparison
        name_lower = name.lower()
        for code in codes:
            if code.name.lower() == name_lower:
                return code
        return None
    
    async def _get_code(self, code_str: str) -> Optional[RedemptionCodeJSON]:
        """Get a specific code by its string value. Kept for admin commands."""
        codes = await self._load_codes()
        for code in codes:
            if code.code == code_str:
                return code
        return None

    async def _save_redemption(self, user_id: int, code: str, ball_instance_id: int) -> None:
        """Save a record of code redemption."""
        try:
            with open(self.redemptions_file, 'r') as f:
                redemptions = json.load(f)
            
            redemptions.append({
                'user_id': user_id,
                'code': code,
                'ball_instance_id': ball_instance_id,
                'redeemed_at': datetime.now().isoformat()
            })
            
            with open(self.redemptions_file, 'w') as f:
                json.dump(redemptions, f, indent=2)
        except Exception as e:
            log.error(f"Error saving redemption: {e}")

    async def _has_redeemed(self, user_id: int, code: str) -> bool:
        """Check if a user has already redeemed a specific code."""
        try:
            with open(self.redemptions_file, 'r') as f:
                redemptions = json.load(f)
            
            for redemption in redemptions:
                if redemption['user_id'] == user_id and redemption['code'] == code:
                    return True
            return False
        except Exception as e:
            log.error(f"Error checking redemption: {e}")
            return False

    async def _is_valid_code(self, code: RedemptionCodeJSON) -> bool:
        """Check if a code is valid."""
        if not code.active:
            return False
        if code.expires_at and code.expires_at < datetime.now():
            return False
        if code.max_uses > 0 and code.uses >= code.max_uses:
            return False
        return True

    async def _load_ball_and_special(self, code: RedemptionCodeJSON) -> None:
        """Load the ball and special objects for a code."""
        if code.ball_id and not code.ball:
            code.ball = await Ball.get(id=code.ball_id)
        if code.special_id and not code.special:
            code.special = await Special.get(id=code.special_id)

    async def announce_code(self, interaction: discord.Interaction, code: RedemptionCodeJSON):
        """Announce a new redeem code in the designated channel."""
        channel = self.bot.get_channel(self.announcement_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(self.announcement_channel_id)
            except discord.NotFound:
                await interaction.followup.send(
                    "⚠️ Couldn't find the announcement channel. The code was created but not announced.",
                    ephemeral=True
                )
                return
        
        # Create an embed for the announcement
        embed = discord.Embed(
            title=f"🎁 New Redeem Code: {code.name}",
            description=f"A new redeem code has been created! Use `/redeem name:{code.name}` to claim your rewards!",
            color=0x3498db,
            timestamp=datetime.now().astimezone()
        )
        
        embed.add_field(name="Redeem With", value=f"`{code.name}`", inline=False)
        embed.add_field(name="Available Claims", value=f"{code.max_uses}", inline=True)
        
        # Add reward details
        reward_details = []
        if code.quantity > 1:
            reward_details.append(f"**{code.quantity}x** Countryballs")
        else:
            reward_details.append("**1x** Countryball")
            
        await self._load_ball_and_special(code)
        if code.ball:
            reward_details.append(f"Ball: **{code.ball.country}**")
        
        if code.special:
            reward_details.append(f"Special: **{code.special.name}**")
        
        if code.attack_bonus != 0 or code.health_bonus != 0:
            reward_details.append(f"Bonuses: **ATK: {code.attack_bonus:+d}%, HP: {code.health_bonus:+d}%**")
        
        embed.add_field(name="Rewards", value="\n".join(reward_details), inline=False)
        embed.set_footer(text=f"Created by {interaction.user.name}")
        
        await channel.send(embed=embed)

    @app_commands.command(name="redeem")
    @app_commands.describe(name="The name of the code to redeem")
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def redeem_code(self, interaction: discord.Interaction, name: str):
        """
        Redeem a code for a countryball reward.

        Parameters
        ----------
        name: str
            The name of the code to redeem
        """
        await interaction.response.defer(ephemeral=True)
        
        # Get code by name instead of code value
        redemption_code = await self._get_code_by_name(name)
        if not redemption_code:
            await interaction.followup.send("Invalid code name.", ephemeral=True)
            return

        if not await self._is_valid_code(redemption_code):
            await interaction.followup.send(
                "This code is either expired, inactive, or has reached its usage limit.",
                ephemeral=True
            )
            return

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        if await self._has_redeemed(interaction.user.id, redemption_code.code):
            await interaction.followup.send(
                "You have already redeemed this code.",
                ephemeral=True
            )
            return

        # Load related objects
        await self._load_ball_and_special(redemption_code)

        # Create a BallInstance as the reward
        created_instances = []
        for _ in range(redemption_code.quantity):
            # Create a BallInstance as the reward
            ball_instance = BallInstance(
                ball=redemption_code.ball,
                player=player,
                special=redemption_code.special,
                attack_bonus=redemption_code.attack_bonus,
                health_bonus=redemption_code.health_bonus,
                catch_date=timezone.now()
            )
            await ball_instance.save()
            created_instances.append(ball_instance)

        # Save the redemption record - still use the internal code for tracking
        await self._save_redemption(interaction.user.id, redemption_code.code, ball_instance.id)

        # Update code usage
        codes = await self._load_codes()
        for c in codes:
            if c.code == redemption_code.code:
                c.uses += 1
                break
        await self._save_codes(codes)

        # Prepare response
        embed = discord.Embed(
            title=f"🎁 Redeem Code: {redemption_code.name}",
            description=f"You've successfully redeemed the code `{redemption_code.name}`!",
            color=0x2ecc71
        )

        special_text = f" [{redemption_code.special.name}]" if redemption_code.special else ""
        quantity_text = f"{redemption_code.quantity}x " if redemption_code.quantity > 1 else ""

        embed.add_field(
            name=f"{quantity_text}{redemption_code.ball.country}{special_text}",
            value=(
                f"ATK: {redemption_code.attack_bonus:+d}%\n"
                f"HP: {redemption_code.health_bonus:+d}%"
            ),
            inline=False
        )
        
        await interaction.followup.send(embed=embed)
        
        # Log to admin logs
        await log_action(
            f"{interaction.user} redeemed code '{redemption_code.name}' and received a {redemption_code.ball.country} ball",
            self.bot
        )

    @app_commands.command(name="code_managing")
    @app_commands.describe(
        action="The admin action to perform",
        name="Name of the redeem code",
        max_uses="Maximum number of times this code can be used",
        countryball="Specific ball to reward (optional if regime or economy specified)",
        regime="Filter by regime",
        economy="Filter by economy", 
        special="Special to apply to the ball",
        quantity="Number of balls to give (1-10)",
        attack_bonus="Attack bonus percentage",
        health_bonus="Health bonus percentage",
        min_attack="Minimum attack bonus (-20 to 20)",
        max_attack="Maximum attack bonus (-20 to 20)",
        min_health="Minimum health bonus (-20 to 20)",
        max_health="Maximum health bonus (-20 to 20)",
        days_valid="Number of days the code remains valid",
        description="Description of the code",
        code="The internal code ID (for actions other than create)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Create Redeem Code", value="create"),
        app_commands.Choice(name="List Redeem Codes", value="list"),
        app_commands.Choice(name="Disable Redeem Code", value="disable"),
        app_commands.Choice(name="Delete Redeem Code", value="delete"),
        app_commands.Choice(name="Get Code Info", value="info")
    ])
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def admin_redeem(
        self,
        interaction: discord.Interaction,
        action: str,
        name: Optional[str] = None,
        max_uses: Optional[int] = None,
        countryball: Optional[BallTransform] = None,
        regime: Optional[RegimeTransform] = None,
        economy: Optional[EconomyTransform] = None,
        special: Optional[SpecialTransform] = None,
        quantity: Optional[int] = 1,
        attack_bonus: Optional[int] = 0,
        health_bonus: Optional[int] = 0,
        min_attack: Optional[int] = -20,
        max_attack: Optional[int] = 20,
        min_health: Optional[int] = -20,
        max_health: Optional[int] = 20,
        days_valid: Optional[int] = None,
        description: Optional[str] = None,
        code: Optional[str] = None
    ):
        """Admin commands for managing redeem codes"""
        await interaction.response.defer(ephemeral=True)
        
        if action == "create":
            if not name or not max_uses:
                await interaction.followup.send("❌ Name and max_uses are required for creating a redeem code.", ephemeral=True)
                return
            
            # Validate inputs
            if max_uses < 1:
                await interaction.followup.send("❌ max_uses must be at least 1.", ephemeral=True)
                return
                
            if quantity < 1 or quantity > 10:
                await interaction.followup.send("quantity must be between 1 and 10.", ephemeral=True)
                return
                
            if min_attack < -20 or max_attack > 20 or min_health < -20 or max_health > 20:
                await interaction.followup.send("Bonus values must be between -20 and 20.", ephemeral=True)
                return
                
            if min_attack > max_attack or min_health > max_health:
                await interaction.followup.send("Minimum values cannot be greater than maximum values.", ephemeral=True)
                return
            
            if not countryball:
                if not regime and not economy:
                    await interaction.followup.send("You must specify a countryball, regime, or economy.", ephemeral=True)
                    return
                
                # Build the query for balls based on regime and/or economy
                query = {}
                if regime:
                    query["regime"] = regime
                if economy:
                    query["economy"] = economy
                
                # Get all matching balls and select a random one
                matching_balls = await Ball.filter(**query)
                if not matching_balls:
                    await interaction.followup.send("No countryballs found matching the specified regime and/or economy.", ephemeral=True)
                    return
                
                # Select a random ball from the matches
                countryball = random.choice(matching_balls)
            
            # Check if name already exists
            existing_code = await self._get_code_by_name(name)
            if existing_code:
                await interaction.followup.send("A code with this name already exists.", ephemeral=True)
                return
            
            # Generate a new internal code (still needed for tracking)
            new_code = generate_code()
            expires_at = datetime.now() + timedelta(days=days_valid) if days_valid else None
            # Create the redemption code
            redemption_code = RedemptionCodeJSON(
                code=new_code,
                created_by=interaction.user.id,
                created_at=datetime.now(),
                name=name,
                expires_at=expires_at,
                max_uses=max_uses,
                uses=0,
                active=True,
                description=description,
                ball_id=countryball.id,
                regime_id=regime.id if regime else None,
                economy_id=economy.id if economy else None,
                special_id=special.id if special else None,
                quantity=quantity,
                attack_bonus=attack_bonus,
                health_bonus=health_bonus,
                min_attack_bonus=min_attack,
                max_attack_bonus=max_attack,
                min_health_bonus=min_health,
                max_health_bonus=max_health
            )
            
            # Load existing codes and add the new one
            codes = await self._load_codes()
            codes.append(redemption_code)
            await self._save_codes(codes)
            
            # Announce the code
            await self.announce_code(interaction, redemption_code)
            
            embed = discord.Embed(title="Code Created", color=discord.Color.green())
            embed.add_field(name="Name", value=name, inline=False)
            embed.add_field(name="Internal Code ID", value=f"`{new_code}`", inline=False)
            embed.add_field(name="Ball", value=countryball.country, inline=True)
            if special:
                embed.add_field(name="Special", value=special.name, inline=True)
            embed.add_field(name="Attack Bonus", value=f"{attack_bonus:+d}%", inline=True)
            embed.add_field(name="Health Bonus", value=f"{health_bonus:+d}%", inline=True)
            embed.add_field(name="Max Uses", value=str(max_uses) if max_uses > 0 else "Unlimited", inline=True)
            embed.add_field(name="Expires", value=discord.utils.format_dt(expires_at, "R") if expires_at else "Never", inline=True)
            if description:
                embed.add_field(name="Description", value=description, inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Log to admin logs
            await log_action(
                f"{interaction.user} created redeem code '{name}' with {max_uses} max uses",
                self.bot
            )
            
        elif action == "list":
            # List all active codes
            codes = await self._load_codes()
            active_codes = [code for code in codes if code.active]
            
            if not active_codes:
                await interaction.followup.send("No active codes found.", ephemeral=True)
                return
            
            entries = []
            for code in active_codes:
                # Load related objects if needed
                await self._load_ball_and_special(code)
                
                entry = [
                    f"Name: {code.name}",
                    f"Internal Code ID: `{code.code}`",
                    f"Uses: {code.uses}/{code.max_uses if code.max_uses > 0 else '∞'}",
                    f"Expires: {discord.utils.format_dt(code.expires_at, 'R') if code.expires_at else 'Never'}",
                    f"Ball: {code.ball.country if code.ball else 'Unknown'}"
                ]
                if code.special:
                    entry.append(f"Special: {code.special.name}")
                if code.description:
                    entry.append(f"Description: {code.description}")
                entries.append(("\n".join(entry), ""))
            
            source = FieldPageSource(entries, per_page=5, inline=False)
            source.embed.title = "Active Redemption Codes"
            source.embed.color = discord.Color.blue()
            paginator = Pages(source, interaction=interaction)
            await paginator.start(ephemeral=True)
            
        elif action == "disable":
            if not code:
                await interaction.followup.send("You must provide a code to disable.", ephemeral=True)
                return
            
            # Find and disable the code
            code = code.upper()
            codes = await self._load_codes()
            found = False
            
            for c in codes:
                if c.code == code:
                    if not c.active:
                        await interaction.followup.send("This code is already inactive.", ephemeral=True)
                        return
                    
                    c.active = False
                    found = True
                    break
            
            if not found:
                await interaction.followup.send("Code not found.", ephemeral=True)
                return
            
            await self._save_codes(codes)
            await interaction.followup.send(f"✅ Code `{code}` has been deactivated.", ephemeral=True)
            
            # Log to admin logs
            await log_action(
                f"{interaction.user} disabled redeem code '{code}'",
                self.bot
            )
            
        elif action == "delete":
            if not code:
                await interaction.followup.send("You must provide a code to delete.", ephemeral=True)
                return
            
            # Find and delete the code
            code = code.upper()
            codes = await self._load_codes()
            code_obj = None
            
            for i, c in enumerate(codes):
                if c.code == code:
                    code_obj = c
                    codes.pop(i)
                    break
            
            if not code_obj:
                await interaction.followup.send("Code not found.", ephemeral=True)
                return
            
            await self._save_codes(codes)
            await interaction.followup.send(f"✅ Code `{code}` has been deleted.", ephemeral=True)
            
            # Log to admin logs
            await log_action(
                f"{interaction.user} deleted redeem code '{code}' ({code_obj.name})",
                self.bot
            )
            
        elif action == "info":
            if not code:
                await interaction.followup.send("You must provide a code to get info.", ephemeral=True)
                return
            
            code = code.upper()
            redemption_code = await self._get_code(code)
            
            if not redemption_code:
                await interaction.followup.send("Code not found.", ephemeral=True)
                return
            
            # Load related objects
            await self._load_ball_and_special(redemption_code)
            
            embed = discord.Embed(title=f"Code: {code}", color=discord.Color.blurple())
            embed.add_field(name="Name", value=redemption_code.name, inline=True)
            embed.add_field(name="Status", value="Active" if await self._is_valid_code(redemption_code) else "Inactive", inline=True)
            embed.add_field(name="Uses", value=f"{redemption_code.uses}/{redemption_code.max_uses if redemption_code.max_uses > 0 else '∞'}", inline=True)
            embed.add_field(name="Created", value=discord.utils.format_dt(redemption_code.created_at, "R"), inline=True)
            if redemption_code.expires_at:
                embed.add_field(name="Expires", value=discord.utils.format_dt(redemption_code.expires_at, "R"), inline=True)
            if redemption_code.ball:
                embed.add_field(name="Ball", value=redemption_code.ball.country, inline=True)
            if redemption_code.special:
                embed.add_field(name="Special", value=redemption_code.special.name, inline=True)
            embed.add_field(name="Attack Bonus", value=f"{redemption_code.attack_bonus:+d}%", inline=True)
            embed.add_field(name="Health Bonus", value=f"{redemption_code.health_bonus:+d}%", inline=True)
            if redemption_code.description:
                embed.add_field(name="Description", value=redemption_code.description, inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send("Unknown action. Valid actions are: create, list, disable, delete, info", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(RedeemCodes(bot))