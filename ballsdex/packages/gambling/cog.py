import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.utils import format_dt

from ballsdex.settings import settings
from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.transformers import BallInstanceTransform
from ballsdex.packages.gambling.blackjack import BlackjackGame, BlackjackGameView

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

class Gambling(commands.Cog):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.games = {}
        
    roulette = app_commands.Group(name="roulette", description="Roulette commands")

    @app_commands.command()
    async def blackjack(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
    ):
        """
        Start an interactive blackjack game, where you bet a countryball.

        Parameters
        ----------
        countryball: BallInstanceTransform
            The countryball to bet.
        """
        player = await Player.get(discord_id=interaction.user.id)
        if countryball.special:
            await interaction.response.send_message(
                f"You cannot gamble with a special {settings.collectible_name}.", ephemeral=True
            )
            return
        if not countryball.countryball.enabled or not countryball.is_tradeable:
            await interaction.response.send_message(
                f"You cannot bet this {settings.collectible_name}.", ephemeral=True
            )
            return

        bj_game = BlackjackGame()
        view = BlackjackGameView(self.bot, player, bj_game, countryball)
        await view.send_initial_message(interaction)

    @app_commands.command()
    async def slots(self, interaction: discord.Interaction, countryball: BallInstanceTransform):
        player = await Player.get(discord_id=interaction.user.id)
        """
        Start an interactive slots game, where you bet a countryball.

        Parameters
        ----------
        countryball: BallInstanceTransform
            The countryball to bet.
        """
        
        if not countryball.countryball.enabled or not countryball.is_tradeable:
            await interaction.response.send_message(
                f"You cannot bet this {settings.collectible_name}.", ephemeral=True
            )
            return
        
        def get_emoji():
            ran = random.randint(1, 9)
            emojis = {
                1: "🍒",
                2: "🍌",
                3: "🍑",
                4: "🍅",
                5: "🍉",
                6: "🍇",
                7: "🍓",
                8: "🍐",
                9: "🍈"
            }
            return emojis.get(ran, "🍒")
        
        slot1 = get_emoji()
        slot2 = get_emoji()
        slot3 = get_emoji()
        
        def calculate_reward(ball, slot1, slot2, slot3):
            if slot1 == slot2 and slot2 == slot3:
                return 3
            elif slot1 == slot2 or slot2 == slot3 or slot1 == slot3:
                return 1
            return 0
        
        reward = calculate_reward(countryball, slot1, slot2, slot3)
        
        slot_display = f"""
        **Bet:** {countryball.description(include_emoji=True, bot=self.bot, is_trade=False)}
        **Multiplier:** 2x
        ╔══════════╗
        ║ {get_emoji()} ║ {get_emoji()} ║ {get_emoji()} ‎‎‎‎║
        ╠══════════╣
        ║ {slot1} ║ {slot2} ║ {slot3} ⟸
        ╠══════════╣
        ║ {get_emoji()} ║ {get_emoji()} ║ {get_emoji()} ║
        ╚══════════╝
        """
        
        embed = discord.Embed(
            color=discord.Color.blue() if reward > 0 else discord.Color.red()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_thumbnail(url="https://i.pinimg.com/originals/9a/f1/4e/9af14e0ae92487516894faa9ea2c35dd.gif")
        embed.description = "Spinning... please wait..."
        
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        
        for _ in range(3):
            temp_slot1 = get_emoji()
            temp_slot2 = get_emoji()
            temp_slot3 = get_emoji()
            
            temp_display = f"""
            **Bet:** {countryball.description(include_emoji=True, bot=self.bot, is_trade=False)}
            **Multiplier:** 2x
            ╔══════════╗
            ║ {get_emoji()} ║ {get_emoji()} ║ {get_emoji()} ‎‎‎‎║
            ╠══════════╣
            ║ {temp_slot1} ║ {temp_slot2} ║ {temp_slot3} ⟸
            ╠══════════╣
            ║ {get_emoji()} ║ {get_emoji()} ║ {get_emoji()} ║
            ╚══════════╝
            """
            
            embed.description = temp_display
            await message.edit(embed=embed)
            await asyncio.sleep(0.7)
        
        ball = await countryball.ball.first()
        
        if reward == 3:
            result_text = f"You won: 3 new {settings.plural_collectible_name}!"
            footer_text = f"You kept your bet and won 3 new {settings.plural_collectible_name}!"
            for _ in range(3):
                await BallInstance.create(
                    player=player,
                    ball=ball,
                    attack_bonus=random.randint(-20, 20),
                    health_bonus=random.randint(-20, 20),
                )
        elif reward == 1:
            result_text = f"You won: 1 new {settings.collectible_name}!"
            footer_text = f"You kept your bet and won a new {settings.collectible_name}!"
            await BallInstance.create(
                player=player,
                ball=ball,
                attack_bonus=random.randint(-20, 20),
                health_bonus=random.randint(-20, 20),
            )
        else:
            result_text = f"You lost: {countryball.description(include_emoji=True, bot=self.bot, is_trade=False)}"
            footer_text = "You lost your bet."
            await countryball.delete()
        
        embed.description = slot_display
        embed.color = discord.Color.blue() if reward > 0 else discord.Color.red()
        embed.set_footer(text=f"{result_text}\n{footer_text}")
        await message.edit(embed=embed)

    @roulette.command(name="start")
    @app_commands.choices(
        mode=[
            Choice(name="Alone", value=1),
            Choice(name="With other players", value=2),
        ])
    async def roulette_start(
        self,
        interaction: discord.Interaction,
        mode: Choice[int],
        countryball: BallInstanceTransform | None = None,
        bet_number: int | None = None,
        bet_color: str | None = None,
        time_before_start: int | None = 30,
    ):
        """
        Start an interactive roulette game, where you bet a countryball.

        Parameters
        ----------
        mode: Choice[int]
            The mode of the game. 1 for alone, 2 for with other players.
        countryball: BallInstanceTransform
            The countryball to bet.
        bet_number: int
            The number to bet on.
        bet_color: str
            The color to bet on.
        time_before_start: int
            The time in seconds before the game starts.
        """
        if mode.value == 1:
            errors = []
            if (bet_color and bet_number) or (not bet_color and not bet_number):
                errors.append("You need to select either `bet_color` or `bet_number`.")
            if bet_color and bet_color.lower() not in ["red", "black", "green"]:
                errors.append("Invalid color! Choose from `red`, `black`, or `green`.")
            if not countryball:
                errors.append("You must not leave `countryball` empty when starting a roulette game with `mode` set to `Alone`.")
            if countryball and (not countryball.countryball.enabled or not countryball.is_tradeable):
                errors.append(f"You cannot bet this {settings.collectible_name}.")
            if countryball and countryball.special:
                errors.append(f"You cannot gamble with a special {settings.collectible_name}.")
            if time_before_start != None and time_before_start != 30:
                errors.append("You must not add a value for `time_before_start` when starting a roulette game with `mode` set to `Alone`.")
            if bet_number and (bet_number < 0 or bet_number > 19):
                errors.append("`bet_number` must be 0 or bigger, and 19 or smaller.")
            
            if errors:
                await interaction.response.send_message(errors[0], ephemeral=True)
                return

            player = await Player.get(discord_id=interaction.user.id)
            embed = discord.Embed(
                title="Roulette",
                description="Spinning the roulette...",
                color=discord.Color.light_grey(),
            )
            embed.add_field(
                name="Bet",
                value=f"{countryball.description(include_emoji=True, bot=self.bot, is_trade=False)}",
                inline=False,
            )
            if bet_number is not None:
                embed.add_field(name="Bet Number", value=str(bet_number), inline=False)
            if bet_color:
                embed.add_field(name="Bet Color", value=bet_color.capitalize(), inline=False)

            await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()

            spin_animation = ['🟢', '🔴', '⚫', '🟢', '🔴', '⚫', '🟢', '🔴', '⚫']
            for spin_emoji in spin_animation:
                await asyncio.sleep(0.3)
                embed.description = f"Spinning the roulette... {spin_emoji}"
                await message.edit(embed=embed)

            embed.description = "Selecting the winning number..."
            await message.edit(embed=embed)
            
            number_selection = []
            for _ in range(5):
                random_number = random.randint(0, 19)
                number_selection.append(str(random_number))
                await asyncio.sleep(0.5)
                embed.description = f"Selecting the number... {', '.join(number_selection)}"
                await message.edit(embed=embed)

            await asyncio.sleep(1)
            
            pockets = [f"{color}{number}" for color, number in zip(
                ["🔴"] * 18 + ["⚫"] * 18 + ["🟢"], list(range(1, 19)) * 2 + [0]
            )]
            result = random.choice(pockets)
            result_color = "red" if "🔴" in result else "black" if "⚫" in result else "green"
            result_number = int(result[1:]) if result[1:].isdigit() else 0
            
            color_handler = (
                discord.Color.green()
                if (bet_color is not None and result_color == bet_color.lower()) or
                   (bet_number is not None and result_number == bet_number)
                else discord.Color.red()
            )
            
            display_color = (
                discord.Color.green() if result_color == "green" 
                else discord.Color.red() if result_color == "red" 
                else discord.Color.dark_theme()
            )

            embed = discord.Embed(
                title="Roulette Results",
                description=f"The winning number is **{result_number}** ({result_color})!",
                color=display_color,
            )
            await message.edit(embed=embed)
            
            await asyncio.sleep(2)
            
            embed = discord.Embed(
                title="Roulette Results",
                description=f"The wheel lands on {result}!",
                color=color_handler,
            )
            embed.add_field(
                name="Bet",
                value=f"{countryball.description(include_emoji=True, bot=self.bot, is_trade=False)}",
                inline=False,
            )

            reward = 0
            ball = await countryball.ball.first()

            if bet_number == result_number:
                reward = 18
                embed.add_field(name="You guessed the number!", value="18:1 payout!", inline=False)
            elif bet_color and bet_color.lower() == result_color:
                reward = 18 if result_color == "green" else 2
                embed.add_field(
                    name="You guessed the color!",
                    value=f"{reward}:1 payout!",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Better luck next time.", value="You lost your bet.", inline=False
                )
                await countryball.delete()

            if reward > 0:
                for _ in range(reward - 1):
                    await BallInstance.create(
                        player=player,
                        ball=ball,
                        attack_bonus=random.randint(-20, 20),
                        health_bonus=random.randint(-20, 20),
                    )

            await message.edit(embed=embed)
        else:
            if countryball:
                await interaction.response.send_message(
                    "You must not use `countryball` when starting a roulette game with `mode` set to `With other players`.",
                    ephemeral=True,
                )
                return

            if bet_number or bet_color:
                await interaction.response.send_message(
                    "You must use neither `bet_number`, nor `bet_color` when starting a roulette game with `mode` set to `With other players`.",
                    ephemeral=True,
                )
                return

            game_id = random.randint(100, 99999)
            remaining_time = datetime.now(timezone.utc) + timedelta(seconds=time_before_start)
            time = format_dt(remaining_time, style="R")
            self.games[game_id] = {"players": [], "bets": []}
            embed = discord.Embed(
                title="Roulette Game",
                description=(
                    f"A new roulette game has started! Use `/roulette add` to join with your bet.\nThis roulette game will start {time}."
                ),
                color=discord.Color.blue(),
            )
            embed.add_field(name="Game ID", value=f"#{game_id}", inline=False)
            embed.add_field(name="Bets", value="No bets yet.", inline=False)
            await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()

            for _ in range(time_before_start):
                await asyncio.sleep(1)
                if not self.games[game_id]["bets"]:
                    continue

                bets_summary = ""
                for bet in self.games[game_id]["bets"]:
                    c_b = bet['countryball'].description(
                        include_emoji=True, bot=self.bot, is_trade=False
                    )
                    bets_summary += f"{bet['player'].mention}: {c_b}\n"

                if bets_summary:
                    embed.set_field_at(1, name="Bets", value=bets_summary)
                    await message.edit(embed=embed)

            if not self.games[game_id]["bets"]:
                embed.description = "No bets were placed. The game has been canceled."
                embed.color = discord.Color.red()
                await message.edit(embed=embed)
                if game_id in self.games:
                    del self.games[game_id]
                return
                
            embed.description = "Spinning the roulette..."
            embed.color = discord.Color.light_grey()
            await message.edit(embed=embed)
            
            spin_animation = ['🟢', '🔴', '⚫', '🟢', '🔴', '⚫', '🟢', '🔴', '⚫']
            for spin_emoji in spin_animation:
                await asyncio.sleep(0.3)
                embed.description = f"Spinning the roulette... {spin_emoji}"
                await message.edit(embed=embed)
                
            embed.description = "Selecting the winning number..."
            await message.edit(embed=embed)
            
            number_selection = []
            for _ in range(5):
                random_number = random.randint(0, 19)
                number_selection.append(str(random_number))
                await asyncio.sleep(0.5)
                embed.description = f"Selecting the number... {', '.join(number_selection)}"
                await message.edit(embed=embed)

            pockets = [f"🔴{i}" for i in range(1, 19)] + [f"⚫{i}" for i in range(1, 19)] + ["🟢0"]
            result = random.choice(pockets)
            result_color = "red" if "🔴" in result else "black" if "⚫" in result else "green"
            result_number = int(result[1:]) if result[1:].isdigit() else 0
            
            display_color = (
                discord.Color.green() if result_color == "green" 
                else discord.Color.red() if result_color == "red" 
                else discord.Color.dark_theme()
            )

            embed = discord.Embed(
                title="Roulette Results",
                description=f"The winning number is **{result_number}** ({result_color})!",
                color=display_color,
            )
            await message.edit(embed=embed)
            
            await asyncio.sleep(2)

            winners = []
            losers = []
            for bet in self.games[game_id]["bets"]:
                player = bet["db_player"]
                countryball = bet["countryball"]
                bet_number = bet["bet_number"]
                bet_color = bet["bet_color"]
                reward = 0

                if bet_number == result_number:
                    reward = 18
                elif bet_color and bet_color.lower() == result_color:
                    reward = 18 if result_color == "green" else 2

                if reward > 0:
                    ball = await countryball.ball.first()
                    for _ in range(reward - 1):
                        await BallInstance.create(
                            player=player,
                            ball=ball,
                            attack_bonus=random.randint(-20, 20),
                            health_bonus=random.randint(-20, 20),
                        )
                    winners.append(f"<@{player.discord_id}>")
                else:
                    await countryball.delete()
                    losers.append(f"<@{player.discord_id}>")

            embed = discord.Embed(
                title="Roulette Results",
                description=f"The wheel lands on {result}!",
                color=discord.Color.green() if winners else discord.Color.red(),
            )
            if winners:
                embed.add_field(name="Winners", value=", ".join(winners), inline=False)
            if losers:
                embed.add_field(name="Losers", value=", ".join(losers), inline=False)

            await message.edit(embed=embed)
            if game_id in self.games:
                del self.games[game_id]

    @roulette.command(name="add")
    async def roulette_add(
        self,
        interaction: discord.Interaction,
        game_id: int,
        countryball: BallInstanceTransform,
        bet_number: int | None = None,
        bet_color: str | None = None,
    ):
        if not countryball.countryball.enabled or not countryball.is_tradeable:
            await interaction.response.send_message(
                f"You cannot bet this {settings.collectible_name}.", ephemeral=True
            )
            return

        if game_id not in self.games:
            await interaction.response.send_message(
                "Invalid game ID. Please ensure the game is active.", ephemeral=True
            )
            return

        if any(bet["player"] == interaction.user for bet in self.games[game_id]["bets"]):
            await interaction.response.send_message(
                "You have already joined this game.", ephemeral=True
            )
            return

        if bet_color and bet_color.lower() not in ["red", "black", "green"]:
            await interaction.response.send_message(
                "Invalid color! Choose from `red`, `black`, or `green`.", ephemeral=True
            )
            return

        if bet_number and (bet_number < 0 or bet_number > 19):
            await interaction.response.send_message(
                "`bet_number` must be between 0 and 19.", ephemeral=True
            )
            return

        if (bet_color and bet_number) or (not bet_color and not bet_number):
            await interaction.response.send_message(
                "You need to select either `bet_color` or `bet_number`.", ephemeral=True
            )
            return

        db_player = await Player.get(discord_id=interaction.user.id)
        if countryball.special:
            await interaction.response.send_message(
                f"You cannot gamble with a special {settings.collectible_name}.", ephemeral=True
            )
            return

        self.games[game_id]["bets"].append(
            {
                "player": interaction.user,
                "db_player": db_player,
                "countryball": countryball,
                "bet_number": bet_number,
                "bet_color": bet_color,
            }
        )

        await interaction.response.send_message(
            f"You have joined Roulette Game #{game_id}!", ephemeral=True
        )