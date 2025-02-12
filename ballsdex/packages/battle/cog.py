import logging
import random
import sys
from typing import TYPE_CHECKING, Dict
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

import asyncio
import io

from ballsdex.core.models import (
    Ball,
    BallInstance,
    Player
)
from ballsdex.core.models import balls as countryballs
from ballsdex.settings import settings

from ballsdex.core.utils.transformers import (
    BallInstanceTransform,
    BallTransform,
    SpecialEnabledTransform
)

from ballsdex.packages.battle.xe_battle_lib import (
    BattleBall,
    BattleInstance,
    gen_battle,
)

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.battle")

battles = []
highevent = ("Testers","Birthday Ball","Eid al-Adha","Realm")
lowevent = ("Lunar New Year 2025","Christmas 2024","Summer")

@dataclass
class GuildBattle:
    interaction: discord.Interaction

    author: discord.Member
    opponent: discord.Member

    author_ready: bool = False
    opponent_ready: bool = False

    battle: BattleInstance = field(default_factory=BattleInstance)


def gen_deck(balls) -> str:
    """Generates a text representation of the player's deck."""
    if not balls:
        return "Empty"
    deck = "\n".join(
        [
            f"- {ball.emoji} {ball.name} (HP: {ball.health} | ATK: {ball.attack})"
            for ball in balls
        ]
    )
    if len(deck) > 6000:
        return deck[0:941] + f'\nTotal: {len(balls)}'
    return deck

def update_embed(
    author_balls, opponent_balls, author, opponent, author_ready, opponent_ready
) -> discord.Embed:
    """Creates an embed for the battle setup phase."""
    embed = discord.Embed(
        title=f"{settings.plural_collectible_name.title()} Battle Plan",
        description=(
            f"Add or remove {settings.plural_collectible_name} you want to propose to the other player using the "
            "'/battle add' and '/battle remove' commands. Once you've finished, "
            "click the tick button to start the battle."
        ),
        color=discord.Colour.blurple(),
    )

    author_emoji = ":white_check_mark:" if author_ready else ""
    opponent_emoji = ":white_check_mark:" if opponent_ready else ""

    embed.add_field(
        name=f"{author_emoji} {author}'s deck:",
        value=gen_deck(author_balls),
        inline=True,
    )
    embed.add_field(
        name=f"{opponent_emoji} {opponent}'s deck:",
        value=gen_deck(opponent_balls),
        inline=True,
    )
    return embed


def create_disabled_buttons() -> discord.ui.View:
    """Creates a view with disabled start and cancel buttons."""
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.success, emoji="✔", label="Ready", disabled=True
        )
    )
    view.add_item(
        discord.ui.Button(
            style=discord.ButtonStyle.danger, emoji="✖", label="Cancel", disabled=True
        )
    )


def fetch_battle(user: discord.User | discord.Member):
    """
    Fetches a battle based on the user provided.

    Parameters
    ----------
    user: discord.User | discord.Member
        The user you want to fetch the battle from.
    """
    found_battle = None

    for battle in battles:
        if user not in (battle.author, battle.opponent):
            continue

        found_battle = battle
        break

    return found_battle


class Battle(commands.GroupCog):
    """
    Battle your countryballs!
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    bulk = app_commands.Group(
        name='bulk', description='Bulk commands for battle'
    )

    async def start_battle(self, interaction: discord.Interaction):
        guild_battle = fetch_battle(interaction.user)

        if guild_battle is None:
            await interaction.response.send_message(
                "You aren't a part of this battle.", ephemeral=True
            )
            return
        
        # Set the player's readiness status

        if interaction.user == guild_battle.author:
            guild_battle.author_ready = True
        elif interaction.user == guild_battle.opponent:
            guild_battle.opponent_ready = True
        # If both players are ready, start the battle

        if guild_battle.author_ready and guild_battle.opponent_ready:
            if not (guild_battle.battle.p1_balls and guild_battle.battle.p2_balls):
                await interaction.response.send_message(
                    f"Both players must add {settings.plural_collectible_name}!"
                )
                return
            new_view = create_disabled_buttons()
            battle_log = "\n".join(gen_battle(guild_battle.battle))

            embed = discord.Embed(
                title=f"{settings.plural_collectible_name.title()} Battle Plan",
                description=f"Battle between {guild_battle.author.mention} and {guild_battle.opponent.mention}",
                color=discord.Color.green(),
            )
            embed.add_field(
                name=f"{guild_battle.author}'s deck:",
                value=gen_deck(guild_battle.battle.p1_balls),
                inline=True,
            )
            embed.add_field(
                name=f"{guild_battle.opponent}'s deck:",
                value=gen_deck(guild_battle.battle.p2_balls),
                inline=True,
            )
            embed.add_field(
                name="Winner:",
                value=f"{guild_battle.battle.winner} - Turn: {guild_battle.battle.turns}",
                inline=False,
            )
            embed.set_footer(text="Battle log is attached.")

            await interaction.response.defer()
            await interaction.message.edit(
                content=f"{guild_battle.author.mention} vs {guild_battle.opponent.mention}",
                embed=embed,
                view=new_view,
                attachments=[
                    discord.File(io.StringIO(battle_log), filename="battle-log.txt")
                ],
            )
            battles.pop(battles.index(guild_battle))
        else:
            # One player is ready, waiting for the other player

            await interaction.response.send_message(
                f"Done! Waiting for the other player to press 'Ready'.", ephemeral=True
            )

            author_emoji = (
                ":white_check_mark:" if interaction.user == guild_battle.author else ""
            )
            opponent_emoji = (
                ":white_check_mark:"
                if interaction.user == guild_battle.opponent
                else ""
            )

            embed = discord.Embed(
                title=f"{settings.plural_collectible_name.title()} Battle Plan",
                description=(
                    f"Add or remove {settings.plural_collectible_name} you want to propose to the other player using the "
                    "'/battle add' and '/battle remove' commands. Once you've finished, "
                    "click the tick button to start the battle."
                ),
                color=discord.Colour.blurple(),
            )

            embed.add_field(
                name=f"{author_emoji} {guild_battle.author.name}'s deck:",
                value=gen_deck(guild_battle.battle.p1_balls),
                inline=True,
            )
            embed.add_field(
                name=f"{opponent_emoji} {guild_battle.opponent.name}'s deck:",
                value=gen_deck(guild_battle.battle.p2_balls),
                inline=True,
            )

            await guild_battle.interaction.edit_original_response(embed=embed)

    async def cancel_battle(self, interaction: discord.Interaction):
        guild_battle = fetch_battle(interaction.user)

        if guild_battle is None:
            await interaction.response.send_message(
                "You aren't a part of this battle!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Battle Plan",
            description="The battle has been cancelled.",
            color=discord.Color.red(),
        )
        embed.add_field(
            name=f":no_entry_sign: {guild_battle.author}'s deck:",
            value=gen_deck(guild_battle.battle.p1_balls),
            inline=True,
        )
        embed.add_field(
            name=f":no_entry_sign: {guild_battle.opponent}'s deck:",
            value=gen_deck(guild_battle.battle.p2_balls),
            inline=True,
        )

        try:
            await interaction.response.defer()
        except discord.errors.InteractionResponded:
            pass

        await interaction.message.edit(embed=embed, view=create_disabled_buttons())
        battles.pop(battles.index(guild_battle))

    @app_commands.command()
    async def start(self, interaction: discord.Interaction, opponent: discord.Member):
        """
        Starts a battle with a chosen user.

        Parameters
        ----------
        opponent: discord.Member
            The user you want to battle.
        """
        if opponent.bot:
            await interaction.response.send_message(
                "You can't battle against bots.", ephemeral=True,
            )
            return
        
        if opponent.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't battle against yourself.", ephemeral=True,
            )
            return

        if fetch_battle(opponent) is not None:
            await interaction.response.send_message(
                "That user is already in a battle.", ephemeral=True,
            )
            return

        if fetch_battle(interaction.user) is not None:
            await interaction.response.send_message(
                "You are already in a battle.", ephemeral=True,
            )
            return
        
        battles.append(GuildBattle(interaction, interaction.user, opponent))

        embed = update_embed([], [], interaction.user.name, opponent.name, False, False)

        start_button = discord.ui.Button(
            style=discord.ButtonStyle.success, emoji="✔", label="Ready"
        )
        cancel_button = discord.ui.Button(
            style=discord.ButtonStyle.danger, emoji="✖", label="Cancel"
        )

        # Set callbacks

        start_button.callback = self.start_battle
        cancel_button.callback = self.cancel_battle

        view = discord.ui.View(timeout=None)

        view.add_item(start_button)
        view.add_item(cancel_button)

        await interaction.response.send_message(
            f"Hey, {opponent.mention}, {interaction.user.name} is proposing a battle with you!",
            embed=embed,
            view=view,
        )

    async def add_balls(self, interaction: discord.Interaction, countryballs):
        guild_battle = fetch_battle(interaction.user)

        if guild_battle is None:
            await interaction.response.send_message(
                "You aren't a part of a battle!", ephemeral=True
            )
            return
        
        if interaction.guild_id != guild_battle.interaction.guild_id:
            await interaction.response.send_message(
                "You must be in the same server as your battle to use commands.", ephemeral=True
            )
            return

        # Check if the user is already ready

        if (interaction.user == guild_battle.author and guild_battle.author_ready) or (
            interaction.user == guild_battle.opponent and guild_battle.opponent_ready
        ):
            await interaction.response.send_message(
                f"You cannot change your {settings.plural_collectible_name} as you are already ready.", ephemeral=True
            )
            return
        # Determine if the user is the author or opponent and get the appropriate ball list

        user_balls = (
            guild_battle.battle.p1_balls
            if interaction.user == guild_battle.author
            else guild_battle.battle.p2_balls
        )
        # Create the BattleBall instance
        maxvalue = 200000 if settings.bot_name == "dragonballdex" else 14000
        for countryball in countryballs:
            battlespecial = await countryball.special
            battlespecial = (f"{battlespecial}")
            if battlespecial == "Shiny":
                buff = 5000 if settings.bot_name == "dragonballdex" else 5000
            elif battlespecial == "Mythical":
                buff = 10000 if settings.bot_name == "dragonballdex" else 12000
            elif battlespecial == "Boss" or battlespecial == "Collector":
                buff = 6000 if settings.bot_name == "dragonballdex" else 6000
            elif battlespecial == "Diamond":
                buff = 8000 if settings.bot_name == "dragonballdex" else 8000
            elif battlespecial == "Emerald":
                buff = 120000 if settings.bot_name == "dragonballdex" else 14000
            elif battlespecial == "Christmas":
                buff = 1250
            elif battlespecial == None or battlespecial == "None":
                buff = 0
            else:
                buff = 1000
            if countryball.health < 0:
                countryballhealth = 0
            elif countryball.health > maxvalue:
                countryballhealth = maxvalue
            else:
                countryballhealth = countryball.health
            if countryball.attack < 0:
                countryballattack = 0
            elif countryball.attack > maxvalue:
                countryballattack = maxvalue
            else:
                countryballattack = countryball.attack
            ball = BattleBall(
                countryball.description(short=True, include_emoji=False, bot=self.bot),
                interaction.user.name,
                (countryballhealth + buff),
                (countryballattack + buff),
                self.bot.get_emoji(countryball.countryball.emoji_id),
            )

            # Check if ball has already been added

            if ball in user_balls:
                yield True
                continue
            
            user_balls.append(ball)
            yield False

        # Update the battle embed for both players

        await guild_battle.interaction.edit_original_response(
            embed=update_embed(
                guild_battle.battle.p1_balls,
                guild_battle.battle.p2_balls,
                guild_battle.author.name,
                guild_battle.opponent.name,
                guild_battle.author_ready,
                guild_battle.opponent_ready,
            )
        )

    async def remove_balls(self, interaction: discord.Interaction, countryballs):
        guild_battle = fetch_battle(interaction.user)

        if guild_battle is None:
            await interaction.response.send_message(
                "You aren't a part of a battle!", ephemeral=True
            )
            return
        
        if interaction.guild_id != guild_battle.interaction.guild_id:
            await interaction.response.send_message(
                "You must be in the same server as your battle to use commands.", ephemeral=True
            )
            return

        # Check if the user is already ready

        if (interaction.user == guild_battle.author and guild_battle.author_ready) or (
            interaction.user == guild_battle.opponent and guild_battle.opponent_ready
        ):
            await interaction.response.send_message(
                "You cannot change your balls as you are already ready.", ephemeral=True
            )
            return
        # Determine if the user is the author or opponent and get the appropriate ball list

        user_balls = (
            guild_battle.battle.p1_balls
            if interaction.user == guild_battle.author
            else guild_battle.battle.p2_balls
        )
        # Create the BattleBall instance

        maxvalue = 200000 if settings.bot_name == "dragonballdex" else 14000
        for countryball in countryballs:
            battlespecial = await countryball.special
            battlespecial = (f"{battlespecial}")
            if battlespecial == "Shiny":
                buff = 50000 if settings.bot_name == "dragonballdex" else 5000
            elif battlespecial == "Mythical":
                buff = 100000 if settings.bot_name == "dragonballdex" else 12000
            elif battlespecial == "Boss" or battlespecial == "Collector":
                buff = 60000 if settings.bot_name == "dragonballdex" else 6000
            elif battlespecial == "Diamond":
                buff = 80000 if settings.bot_name == "dragonballdex" else 8000
            elif battlespecial == "Emerald":
                buff = 120000 if settings.bot_name == "dragonballdex" else 14000
            elif battlespecial in highevent:
                buff = 25000 if settings.bot_name == "dragonballdex" else 3000
            elif battlespecial in lowevent:
                buff = 15000 if settings.bot_name == "dragonballdex" else 2000
            elif battlespecial == "Gold" or battlespecial == "Titanium White":
                buff = 1500
            elif battlespecial == "Black":
                buff = 1250
            elif battlespecial == None or battlespecial == "None":
                buff = 0
            else:
                buff = 1000
            if countryball.health < 0:
                countryballhealth = 0
            elif countryball.health > maxvalue:
                countryballhealth = maxvalue
            else:
                countryballhealth = countryball.health
            if countryball.attack < 0:
                countryballattack = 0
            elif countryball.attack > maxvalue:
                countryballattack = maxvalue
            else:
                countryballattack = countryball.attack
            ball = BattleBall(
                countryball.description(short=True, include_emoji=False, bot=self.bot),
                interaction.user.name,
                (countryballhealth + buff),
                (countryballattack + buff),
                self.bot.get_emoji(countryball.countryball.emoji_id),
            )

            # Check if ball has already been added

            if ball not in user_balls:
                yield True
                continue
            
            user_balls.remove(ball)
            yield False

        # Update the battle embed for both players

        await guild_battle.interaction.edit_original_response(
            embed=update_embed(
                guild_battle.battle.p1_balls,
                guild_battle.battle.p2_balls,
                guild_battle.author.name,
                guild_battle.opponent.name,
                guild_battle.author_ready,
                guild_battle.opponent_ready,
            )
        )

    @app_commands.command()
    async def add(
        self, interaction: discord.Interaction, countryball: BallInstanceTransform, special: SpecialEnabledTransform | None = None,
    ):
        """
        Adds a countryball to a battle.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to add.
        """
        async for dupe in self.add_balls(interaction, [countryball]):
            if dupe:
                await interaction.response.send_message(
                    "You cannot add the same ball twice!", ephemeral=True
                )
                return

        # Construct the message
        attack = "{:+}".format(countryball.attack_bonus)
        health = "{:+}".format(countryball.health_bonus)

        try:
            await interaction.response.send_message(
                f"Added `{countryball.description(short=True, include_emoji=False, bot=self.bot)} ({attack}%/{health}%)`!",
                ephemeral=True,
            )
        except:
            return

    @app_commands.command()
    async def remove(
        self, interaction: discord.Interaction, countryball: BallInstanceTransform, special: SpecialEnabledTransform | None = None,
    ):
        """
        Removes a countryball from battle.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to remove.
        """
        async for not_in_battle in self.remove_balls(interaction, [countryball]):
            if not_in_battle:
                await interaction.response.send_message(
                    f"You cannot remove a {settings.collectible_name} that is not in your deck!", ephemeral=True
                )
                return

        attack = "{:+}".format(countryball.attack_bonus)
        health = "{:+}".format(countryball.health_bonus)

        try:
            await interaction.response.send_message(
                f"Removed `{countryball.description(short=True, include_emoji=False, bot=self.bot)} ({attack}%/{health}%)`!",
                ephemeral=True,
            )
        except:
            return
    
    @bulk.command(name="add")
    async def bulk_add(
        self, interaction: discord.Interaction, countryball: BallTransform
    ):
        """
        Adds countryballs to a battle in bulk.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to add.
        """
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            balls = await countryball.ballinstances.filter(player=player)

            count = 0
            async for dupe in self.add_balls(interaction, balls):
                if not dupe:
                    count += 1

            await interaction.followup.send(
                f'Added {count} {countryball.country}{"s" if count != 1 else ""}!',
                ephemeral=True,
            )
        except:
            await interaction.followup.send(f"You aren't a part of a battle!",ephemeral=True)

    @bulk.command(name="all")
    async def bulk_all(
        self, interaction: discord.Interaction
    ):
        """
        Adds all your countryballs to a battle.
        """
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            balls = await BallInstance.filter(player=player)

            count = 0
            async for dupe in self.add_balls(interaction, balls):
                if not dupe:
                    count += 1

            name = settings.plural_collectible_name if count != 1 else settings.collectible_name

            await interaction.followup.send(f"Added {count} {name}!", ephemeral=True)
        except:
            await interaction.followup.send(f"You aren't a part of a battle!",ephemeral=True)
        
    @bulk.command(name="clear")
    async def bulk_remove(
        self, interaction: discord.Interaction
    ):
        """
        Removes all your countryballs from a battle.
        """
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            balls = await BallInstance.filter(player=player)

            count = 0
            async for not_in_battle in self.remove_balls(interaction, balls):
                if not not_in_battle:
                    count += 1

            name = settings.plural_collectible_name if count != 1 else settings.collectible_name
            await interaction.followup.send(f"Removed {count} {name}!", ephemeral=True)
        except:
            await interaction.followup.send(f"You aren't a part of a battle!",ephemeral=True)

    @bulk.command(name="remove")
    async def bulk_remove(
        self, interaction: discord.Interaction, countryball: BallTransform
    ):
        """
        Removes countryballs from a battle in bulk.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to remove.
        """
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            player, _ = await Player.get_or_create(discord_id=interaction.user.id)
            balls = await countryball.ballinstances.filter(player=player)

            count = 0
            async for not_in_battle in self.remove_balls(interaction, balls):
                if not not_in_battle:
                    count += 1
            await interaction.followup.send(
                f'Removed {count} {countryball.country}{"s" if count != 1 else ""}!',
                ephemeral=True,
            )
        except:
            await interaction.followup.send(f"You aren't a part of a battle!",ephemeral=True)
