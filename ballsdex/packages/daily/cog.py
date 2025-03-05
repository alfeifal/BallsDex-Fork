import datetime
import logging
import random
import asyncio
from typing import List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from tortoise.expressions import Q

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
from ballsdex.packages.countryballs.countryball import CountryBall
from ballsdex.core.utils.logging import log_action

log = logging.getLogger("ballsdex.packages.daily.cog")

class RevealStage:
    STATS = 1
    ECONOMY = 2
    FULL = 3

class AutomatedPackOpening:
    def __init__(self, bot: commands.Bot, instances: List[Tuple[BallInstance, CountryBall, Optional[Special]]]):
        self.bot = bot
        self.instances = instances
        self.current_index = 0
        self.current_stage = RevealStage.STATS
        self.revealed = [False] * len(instances)
        self.message: Optional[discord.Message] = None

    async def start_reveal(self, interaction: discord.Interaction):
        """Initiate the automated pack opening sequence with staged reveals"""
        embed = await self.create_pack_embed()
        self.message = await interaction.followup.send(embed=embed)
        
        # Reveal balls one by one with stages
        for i in range(len(self.instances)):
            self.current_index = i
            
            # Stage 1: Show stats
            self.current_stage = RevealStage.STATS
            await asyncio.sleep(1.5)
            embed = await self.create_pack_embed()
            await self.message.edit(embed=embed)
            
            # Stage 2: Show economy
            self.current_stage = RevealStage.ECONOMY
            await asyncio.sleep(1.5)
            embed = await self.create_pack_embed()
            await self.message.edit(embed=embed)
            
            # Stage 3: Full reveal
            self.current_stage = RevealStage.FULL
            self.revealed[i] = True
            await asyncio.sleep(1.5)
            embed = await self.create_pack_embed()
            
            if i == len(self.instances) - 1:
                embed.set_footer(text="Pack opening complete! 🎉")
            
            await self.message.edit(embed=embed)

    async def create_pack_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎁 Daily Pack Opening!",
            description="Revealing your new balls...",
            color=0x3498db
        )

        total_attack = 0
        total_health = 0

        for i, (revealed, (instance, countryball, special)) in enumerate(zip(self.revealed, self.instances)):
            if i < self.current_index or revealed:
                # Fully revealed ball
                ball_model = balls.get(instance.ball_id, instance.ball)
                emoji = f"{self.bot.get_emoji(ball_model.emoji_id)}"
                special_text = f" [**{special.name}**]" if special else ""
        
                field_name = f"Ball #{i+1}: {emoji} {ball_model.country}{special_text}"
                field_value = (
                    f"`ATK: {instance.attack_bonus:+d}% ({instance.attack})`\n"
                    f"`HP: {instance.health_bonus:+d}% ({instance.health})`\n"
                    f"Regime: {ball_model.cached_regime.name}\n"
                    f"Economy: {ball_model.cached_economy.name if ball_model.cached_economy else 'None'}"
                )
                
                if special and special.catch_phrase:
                    field_value += f"\n*{special.catch_phrase}*"

                total_attack += instance.attack
                total_health += instance.health
                
                embed.add_field(name=field_name, value=field_value, inline=False)
            
            elif i == self.current_index:
                # Current ball being revealed
                ball_model = balls.get(instance.ball_id, instance.ball)
                field_name = f"Ball #{i+1}"
                
                if self.current_stage >= RevealStage.STATS:
                    field_name = f"Ball #{i+1}: ❓"
                    field_value = (
                        f"`ATK: {instance.attack_bonus:+d}% ({instance.attack})`\n"
                        f"`HP: {instance.health_bonus:+d}% ({instance.health})`"
                    )
                    
                if self.current_stage >= RevealStage.ECONOMY:
                    field_value += f"\nRegime: {ball_model.cached_regime.name}\n"
                    field_value += f"Economy: {ball_model.cached_economy.name if ball_model.cached_economy else 'None'}"
                    
                embed.add_field(name=field_name, value=field_value, inline=False)
            else:
                # Not yet revealed
                embed.add_field(
                    name=f"Ball #{i+1}", 
                    value="❓ *Revealing soon...*",
                    inline=False
                )

        if all(self.revealed):
            embed.add_field(
                name="📊 Pack Statistics",
                value=f"Total ATK: {total_attack}\nTotal HP: {total_health}",
                inline=False
            )

        # Add progress indicator
        stages = {
            RevealStage.STATS: "Stats",
            RevealStage.ECONOMY: "Economy",
            RevealStage.FULL: "Full Reveal"
        }
        current_ball = self.current_index + 1
        current_stage = stages.get(self.current_stage, "")
        progress_bar = "▰" * self.current_index + "▱" * (len(self.instances) - self.current_index)
        embed.set_footer(text=f"Ball {current_ball}/{len(self.instances)} | {current_stage}\n{progress_bar}")

        return embed

class daily(commands.Cog):
    def __init__(self, bot: commands.Bot):
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

        instances_data = []
        log_message = f"{interaction.user} claimed their daily pack and received: "

        for _ in range(3):
            countryball = await CountryBall.get_random()
            special = await self.get_special()
            
            instance = await BallInstance.create(
                ball=countryball.model,
                player=player,
                attack_bonus=random.randint(-20, 20),
                health_bonus=random.randint(-20, 20),
                special=special,
            )
            
            instances_data.append((instance, countryball, special))
            
            # Add to log message
            special_log = f" ({special.name})" if special else ""
            log_message += f"{countryball.name}{special_log} ({instance.attack_bonus:+d} ATK, {instance.health_bonus:+d} HP), "

        pack_opening = AutomatedPackOpening(self.bot, instances_data)
        await pack_opening.start_reveal(interaction)
        
        # Log the pack opening
        log_message = log_message.rstrip(", ")
        await log_action(log_message, self.bot)