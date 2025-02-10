import discord
import time
import random
import logging
import asyncio

from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING, Optional, List
from discord.ui import Button, View
from datetime import datetime

from ballsdex.settings import settings
from ballsdex.core.utils.transformers import BallInstanceTransform, SpecialTransform
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

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.battle.cog")

class BattleView(View):
    def __init__(self, initiator: discord.Member, opponent: discord.Member):
        super().__init__(timeout=900)  # 15 minutes
        self.initiator = initiator
        self.opponent = opponent
        self.initiator_locked = False
        self.opponent_locked = False
        self.initiator_balls = []
        self.opponent_balls = []
        self.cancelled = False
        
    @discord.ui.button(label="Lock Selection", style=discord.ButtonStyle.primary, custom_id="lock")
    async def lock_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id not in [self.initiator.id, self.opponent.id]:
            await interaction.response.send_message("You're not part of this battle!", ephemeral=True)
            return
            
        if interaction.user.id == self.initiator.id:
            self.initiator_locked = True
        else:
            self.opponent_locked = True
            
        if self.initiator_locked and self.opponent_locked:
            button.style = discord.ButtonStyle.success
            button.label = "Conclude Battle"
            
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary, custom_id="reset")
    async def reset_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id not in [self.initiator.id, self.opponent.id]:
            await interaction.response.send_message("You're not part of this battle!", ephemeral=True)
            return
            
        if interaction.user.id == self.initiator.id:
            self.initiator_balls = []
            self.initiator_locked = False
        else:
            self.opponent_balls = []
            self.opponent_locked = False
            
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Cancel battle", style=discord.ButtonStyle.danger, custom_id="cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id not in [self.initiator.id, self.opponent.id]:
            await interaction.response.send_message("You're not part of this battle!", ephemeral=True)
            return
        
        self.cancelled = True
        self.stop()
        
        # Update embed to show cancelled state
        embed = interaction.message.embeds[0]
        embed.title = "Countryballs Battle Plan"
        embed.description = "The battle has been cancelled."
        
        # Strike through all ball entries
        for field in embed.fields[1:]:
            if field.value != "Empty" and field.value != "No balls selected":
                lines = field.value.split('\n')
                struck_lines = [f"~~{line}~~" for line in lines]
                field.value = '\n'.join(struck_lines)
                
        await interaction.message.edit(embed=embed, view=None)

class Battle(commands.GroupCog):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.active_battles = {}

    @app_commands.command()
    async def start(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        balls_count: int,
        allow_dupes: bool = False,
        exclude_special: bool = False,
        exclude_shiny: bool = False
    ):
        """Start a battle with another player"""
        if opponent.id == interaction.user.id:
            return await interaction.response.send_message("You can't battle yourself!", ephemeral=True)
            
        if interaction.channel_id in self.active_battles:
            return await interaction.response.send_message("There's already an active battle in this channel!", ephemeral=True)

        embed = discord.Embed(
            title="Countryballs Battle Plan",
            description=f"Add or remove countryballs you want to propose to the other player using the\n"
                       f"/battle add and /battle remove commands.\n"
                       f"Once you're finished, click the lock button below to confirm your proposal.\n"
                       f"You have 15 minutes before this interaction ends.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Settings:",
            value=f"• Duplicates: {'Not Allowed' if allow_dupes else 'Allowed'}\n"
                  f"• Buffs: Allowed\n"
                  f"• Amount: {balls_count}",
            inline=False
        )

        embed.add_field(
            name=interaction.user.name,
            value="Empty",
            inline=True
        )

        embed.add_field(
            name=opponent.name,
            value="Empty",
            inline=True
        )

        embed.set_footer(text="This message is updated every 15 seconds, but you can keep on editing your battle proposal.")
            
        view = BattleView(interaction.user, opponent)
        await interaction.response.send_message(f"Hey {opponent.mention}, {interaction.user.name} is proposing a battle with you!", embed=embed, view=view)
        
        battle_message = await interaction.original_response()
        self.active_battles[interaction.channel_id] = {
            "message": battle_message,
            "initiator": interaction.user,
            "opponent": opponent,
            "balls_count": balls_count,
            "duplicates": allow_dupes,
            "exclude_special": exclude_special,
            "exclude_shiny": exclude_shiny,
            "view": view,
            "last_update": time.time()
        }
        
        asyncio.create_task(self.update_battle_embed(interaction.channel_id))

    async def update_battle_embed(self, channel_id: int):
        battle = self.active_battles[channel_id]
        message = battle["message"]
        view = battle["view"]
        
        while True:
            if channel_id not in self.active_battles or view.cancelled:
                break
                
            if time.time() - battle["last_update"] >= 15:
                embed = message.embeds[0]
                
                # Update initiator's team
                initiator_balls = view.initiator_balls
                initiator_text = "\n".join([
                    f"{self.bot.get_emoji(ball.emoji_id)} #{ball.pk:X} {ball.ball.country} ATK:+{ball.attack_bonus}% HP:+{ball.health_bonus}%"
                    for ball in initiator_balls
                ]) if initiator_balls else "Empty"
                
                # Update opponent's team
                opponent_balls = view.opponent_balls
                opponent_text = "\n".join([
                    f"{self.bot.get_emoji(ball.emoji_id)} #{ball.pk:X} {ball.ball.country} ATK:+{ball.attack_bonus}% HP:+{ball.health_bonus}%"
                    for ball in opponent_balls
                ]) if opponent_balls else "Empty"
                
                embed.set_field_at(1, name=battle["initiator"].name, value=initiator_text, inline=True)
                embed.set_field_at(2, name=battle["opponent"].name, value=opponent_text, inline=True)
                
                try:
                    await message.edit(embed=embed)
                except discord.NotFound:
                    break
                    
                battle["last_update"] = time.time()
            
            if view.initiator_locked and view.opponent_locked:
                await self.start_battle(channel_id)
                break
                
            await asyncio.sleep(1)

    async def start_battle(self, channel_id: int):
        battle = self.active_battles[channel_id]
        initiator_balls = battle["view"].initiator_balls
        opponent_balls = battle["view"].opponent_balls
        
        # Create battle result embed
        embed = discord.Embed(
            title=f"Battle between {battle['initiator'].name} and {battle['opponent'].name}",
            color=discord.Color.blue()
        )
        
        # Add battle settings
        embed.add_field(
            name="Battle settings:",
            value=f"• Duplicates: {'Allowed' if battle['exclude_special'] else 'Not Allowed'}\n"
                  f"• Buffs: Allowed\n"
                  f"• Amount: {battle['balls_count']}",
            inline=False
        )
        
        # Add decks
        initiator_deck = "\n".join([
            f"{self.bot.get_emoji(ball.emoji_id)} {ball.description(short=True)} (HP: {ball.health} | "
            f"DMG:{ball.attack})"
            for ball in initiator_balls
        ])
        opponent_deck = "\n".join([
            f"{self.bot.get_emoji(ball.emoji_id)} {ball.description(short=True)} (HP: {ball.health} | "
            f"DMG:{ball.attack})"
            for ball in opponent_balls
        ])
        
        embed.add_field(name=f"{battle['initiator'].name}'s deck:", value=initiator_deck, inline=True)
        embed.add_field(name=f"{battle['opponent'].name}'s deck:", value=opponent_deck, inline=True)
        
        # Simulate battle
        initiator_health = sum(ball.health for ball in initiator_balls)
        opponent_health = sum(ball.health for ball in opponent_balls)
        initiator_damage = sum(ball.attack for ball in initiator_balls)
        opponent_damage = sum(ball.attack for ball in opponent_balls)
        
        turn = 1
        while initiator_health > 0 and opponent_health > 0:
            opponent_health -= initiator_damage
            if opponent_health <= 0:
                break
            initiator_health -= opponent_damage
            turn += 1
            
        # Determine winner
        winner = battle['initiator'] if opponent_health <= 0 else battle['opponent']
        embed.add_field(name="Winner:", value=f"{winner.name} - Turn: {turn}", inline=False)
        
        await battle["message"].edit(embed=embed, view=None)
        del self.active_battles[channel_id]
        
    @app_commands.command()
    async def cancel(self, interaction: discord.Interaction):
        """Cancel an ongoing battle in this channel"""
        if interaction.channel_id not in self.active_battles:
            return await interaction.response.send_message("There's no active battle in this channel!", ephemeral=True)
            
        battle = self.active_battles[interaction.channel_id]
        if interaction.user.id not in [battle["initiator"].id, battle["opponent"].id]:
            return await interaction.response.send_message("You're not part of this battle!", ephemeral=True)
        
        embed = battle["message"].embeds[0]
        embed.title = "Countryballs Battle Plan"
        embed.description = "The battle has been cancelled."
        
        for field in embed.fields[1:]:
            if field.value != "Empty":
                lines = field.value.split('\n')
                struck_lines = [f"~~{line}~~" for line in lines]
                field.value = '\n'.join(struck_lines)
        
        await battle["message"].edit(embed=embed, view=None)
        del self.active_battles[interaction.channel_id]
        await interaction.response.send_message("Battle cancelled!", ephemeral=True)

    async def update_battle_embed(self, channel_id: int):
        battle = self.active_battles[channel_id]
        message = battle["message"]
        view = battle["view"]
    
        while True:
            if channel_id not in self.active_battles or view.cancelled:
                break
                
            if time.time() - battle["last_update"] >= 15:
                embed = message.embeds[0]

                # Update initiator's team with new emojis
                initiator_balls = view.initiator_balls
                initiator_text = "\n".join([
                    f"{self.bot.get_emoji(ball.emoji_id)} {ball.description(short=True)}\n"
                    f"ATK:{ball.attack:+d}% HP:{ball.health:+d}%"
                    for ball in initiator_balls
                ]) if initiator_balls else "Empty"

                # Update opponent's team
                opponent_balls = view.opponent_balls
                opponent_text = "\n".join([
                    f"{self.bot.get_emoji(ball.emoji_id)} {ball.description(short=True)}\n"
                    f"ATK:{ball.attack:+d}% HP:{ball.health:+d}%"
                    for ball in opponent_balls
                ]) if opponent_balls else "Empty"

                embed.set_field_at(1, name=battle["initiator"].name, value=initiator_text, inline=True)
                embed.set_field_at(2, name=battle["opponent"].name, value=opponent_text, inline=True)

                try:
                    await message.edit(embed=embed)
                except discord.NotFound:
                    break
                    
                battle["last_update"] = time.time()

            if view.initiator_locked and view.opponent_locked:
                await self.start_battle(channel_id)
                break
                
            await asyncio.sleep(1)

    @app_commands.command()
    async def add(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
        special: SpecialTransform | None = None
    ):
        """Add a ball to your battle team"""
        if interaction.channel_id not in self.active_battles:
            return await interaction.response.send_message("There's no active battle in this channel!", ephemeral=True)
            
        battle = self.active_battles[interaction.channel_id]
        if interaction.user.id not in [battle["initiator"].id, battle["opponent"].id]:
            return await interaction.response.send_message("You're not part of this battle!", ephemeral=True)
            
        view = battle["view"]
        
        # Check if ball has special and if specials are excluded
        if battle["exclude_special"] and countryball.special:
            return await interaction.response.send_message("Special balls are not allowed in this battle!", ephemeral=True)
        
        # Add ball to appropriate team
        if interaction.user.id == battle["initiator"].id:
            if view.initiator_locked:
                return await interaction.response.send_message("Your team is locked!", ephemeral=True)
            if len(view.initiator_balls) >= battle["balls_count"]:
                return await interaction.response.send_message("Your team is full!", ephemeral=True)
            if not battle["duplicates"] and countryball in view.initiator_balls:
                return await interaction.response.send_message("Duplicates are not allowed in this battle!", ephemeral=True)
            view.initiator_balls.append(countryball)
        else:
            if view.opponent_locked:
                return await interaction.response.send_message("Your team is locked!", ephemeral=True)
            if len(view.opponent_balls) >= battle["balls_count"]:
                return await interaction.response.send_message("Your team is full!", ephemeral=True)
            if not battle["duplicates"] and countryball in view.opponent_balls:
                return await interaction.response.send_message("Duplicates are not allowed in this battle!", ephemeral=True)
            view.opponent_balls.append(countryball)
            
        await interaction.response.send_message(f"Added `{countryball.to_string()}` to your team!", ephemeral=True)

    @app_commands.command()
    async def remove(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform
    ):
        """Remove a ball from your battle team"""
        if interaction.channel_id not in self.active_battles:
            return await interaction.response.send_message("There's no active battle in this channel!", ephemeral=True)
            
        battle = self.active_battles[interaction.channel_id]
        if interaction.user.id not in [battle["initiator"].id, battle["opponent"].id]:
            return await interaction.response.send_message("You're not part of this battle!", ephemeral=True)
            
        view = battle["view"]
        if interaction.user.id == battle["initiator"].id:
            if view.initiator_locked:
                return await interaction.response.send_message("Your team is locked!", ephemeral=True)
            if countryball not in view.initiator_balls:
                return await interaction.response.send_message("This ball is not in your team!", ephemeral=True)
            view.initiator_balls.remove(countryball)
        else:
            if view.opponent_locked:
                return await interaction.response.send_message("Your team is locked!", ephemeral=True)
            if countryball not in view.opponent_balls:
                return await interaction.response.send_message("This ball is not in your team!", ephemeral=True)
            view.opponent_balls.remove(countryball)
            
        await interaction.response.send_message(f"Removed {countryball.description(short=True)} from your team!", ephemeral=True)
