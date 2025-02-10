import datetime
import logging
import random
from typing import List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View
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

class PackOpeningView(View):
    def __init__(self, bot: commands.Bot, instances: List[Tuple[BallInstance, CountryBall, Optional[Special]]]):
        super().__init__(timeout=180)
        self.bot = bot
        self.instances = instances
        self.current_index = 0
        self.revealed = [False] * len(instances)
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        if not all(self.revealed):
            reveal_button = Button(
                label="Reveal Next Ball", 
                style=discord.ButtonStyle.primary,
                custom_id="reveal"
            )
            reveal_button.callback = self.reveal_ball
            self.add_item(reveal_button)

            reveal_all_button = Button(
                label="Reveal All", 
                style=discord.ButtonStyle.success,
                custom_id="reveal_all"
            )
            reveal_all_button.callback = self.reveal_all
            self.add_item(reveal_all_button)

    async def reveal_ball(self, interaction: discord.Interaction):
        if self.current_index >= len(self.instances):
            return

        self.revealed[self.current_index] = True
        embed = await self.create_pack_embed()
        self.current_index += 1

        if self.current_index >= len(self.instances):
            self.clear_items()
            embed.set_footer(text="Pack opening complete! 🎉")
        else:
            self.update_buttons()

        await interaction.response.edit_message(embed=embed, view=self)

    async def reveal_all(self, interaction: discord.Interaction):
        self.revealed = [True] * len(self.instances)
        self.current_index = len(self.instances)
        
        embed = await self.create_pack_embed()
        embed.set_footer(text="Pack opening complete! 🎉")
        
        self.clear_items()
        await interaction.response.edit_message(embed=embed, view=self)

    async def create_pack_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎁 Daily Pack Opening!",
            description="Revealing your new balls...",
            color=0x3498db
        )

        total_attack = 0
        total_health = 0

        for i, (revealed, (instance, countryball, special)) in enumerate(zip(self.revealed, self.instances)):
            if revealed:
                ball_model = balls.get(instance.ball_id, instance.ball)
                emoji = f"{self.bot.get_emoji(ball_model.emoji_id)}"  # Formateo directo del emoji
                special_text = f" [**{special.name}**]" if special else ""
        
                field_name = f"Ball #{i+1}: {emoji} {ball_model.country}{special_text}"
                field_value = (
                    f"`ATK: {instance.attack_bonus:+d}% ({instance.attack})`\n"
                    f"`HP: {instance.health_bonus:+d}% ({instance.health})`"
            )
                
                if special and special.catch_phrase:
                    field_value += f"\n*{special.catch_phrase}*"

                total_attack += instance.attack
                total_health += instance.health
                
                embed.add_field(name=field_name, value=field_value, inline=False)
            else:
                embed.add_field(
                    name=f"Ball #{i+1}", 
                    value="❓ *Click 'Reveal Next Ball' to see what you got!*",
                    inline=False
                )

        if all(self.revealed):
            embed.add_field(
                name="📊 Pack Statistics",
                value=f"Total ATK: {total_attack}\nTotal HP: {total_health}",
                inline=False
            )

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

        for _ in range(5):
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

        view = PackOpeningView(self.bot, instances_data)
        initial_embed = await view.create_pack_embed()
        
        await interaction.followup.send(embed=initial_embed, view=view)
        
        # Log the pack opening
        log_message = log_message.rstrip(", ")
        await log_action(log_message, self.bot)
