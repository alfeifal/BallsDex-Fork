import discord
import time
import random
import string
import asyncio
import logging
import re

intents = discord.Intents.default()
intents.members = True

from discord import app_commands
from discord.ext import commands
from typing import TYPE_CHECKING, Optional, cast
from discord.ui import Button, View

from ballsdex.settings import settings
from ballsdex.core.utils.transformers import BallInstanceTransform
from ballsdex.core.utils.transformers import BallEnabledTransform
from ballsdex.core.utils.transformers import SpecialTransform, BallTransform
from ballsdex.core.utils.transformers import SpecialEnabledTransform
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.bot import BallsDexBot

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.boss.cog")
FILENAME_RE = re.compile(r"^(.+)(\.\S+)$")

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
    specials,
)

SHINYBUFFS = [2000,2000] # Shiny Buffs
CHRISTMASBUFFS = [500,500] # Shiny Buffs
MYTHICBUFFS = [3000,3000] # Shiny Buffs
BOSSBUFFS = [4000,4000] # Shiny Buffs
# ATK, HP
MAXSTATS = [100000,100000] # Max stats a card is limited to (before buffs)
# ATK, HP
DAMAGERNG = [6000,7000] # Damage a boss can deal IF attack_amount has NOT been inputted in /boss admin attack.


LOGCHANNEL = 1331582589663838270
OUTPUTCHANNEL = 1318949931054006312
#Change this if you want to a different channel for boss logs
#e.g.
#LOGCHANNEL = 1234567890987654321
async def log_action(message: str, bot: BallsDexBot, console_log: bool = False):
    if LOGCHANNEL:
        channel = bot.get_channel(LOGCHANNEL)
        if not channel:
            log.warning(f"Channel {LOGCHANNEL} not found")
            return
        if not isinstance(channel, discord.TextChannel):
            log.warning(f"Channel {channel.name} is not a text channel")  # type: ignore
            return
        await channel.send(message)
    if console_log:
        log.info(message)
        
async def get_output_channel(bot: BallsDexBot):
    channel = bot.get_channel(OUTPUTCHANNEL)
    if not channel:
        log.warning(f"Output channel {OUTPUTCHANNEL} not found")
        return None
    if not isinstance(channel, discord.TextChannel):
        log.warning(f"Channel {channel.name} is not a text channel")  # type: ignore
        return None
    return channel       

import discord
from discord.ui import View, Button
from discord import Interaction

class JoinButton(View):
    def __init__(self, boss_cog, message=None, timeout=300): # set in here how long you want the button last for in seconds
        super().__init__(timeout=timeout)
        self.boss_cog = boss_cog
        self.message = message
        self.join_button = Button(label="Join Boss Fight!", style=discord.ButtonStyle.primary, custom_id="join_boss")
        self.join_button.callback = self.button_callback
        self.add_item(self.join_button)
    
    async def button_callback(self, interaction: Interaction):
        if not self.boss_cog.boss_enabled:
            await interaction.response.send_message("Boss is disabled", ephemeral=True)
            return

        if interaction.user.id in self.boss_cog.disqualified:
            await interaction.response.send_message("You have been disqualified", ephemeral=True)
            return

        if interaction.user.id in self.boss_cog.users:
            await interaction.response.send_message("You have already joined the boss", ephemeral=True)
            return

        self.boss_cog.users.append(interaction.user.id)
        
        # Recalculate HP based on new player count
        new_hp = Boss.calculate_boss_hp(self.boss_cog.bossball.rarity, len(self.boss_cog.users))
        self.boss_cog.bossHP = new_hp
        
        await interaction.response.send_message("You have joined the Boss Battle!", ephemeral=True) 
    
        
    async def on_timeout(self):
        output_channel = await get_output_channel(self.boss_cog.bot) or self.message.channel
        # Remove button once the time ends
        for item in self.children:
            if isinstance(item, Button) and item.custom_id == "join_boss":
                self.remove_item(item)

        # Change message 
        if self.message:
            await self.message.edit(content="The boss battle has begun!", view=self)
            
        # Start first round
        if self.boss_cog.boss_enabled:
            await self.boss_cog.start_first_round(output_channel)
        
@app_commands.guilds(*settings.admin_guild_ids)
class Boss(commands.GroupCog):
    """
    Boss commands. Thanks to Mo Official for letting me steal it.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.boss_enabled = False
        self.balls = []
        self.users = []
        self.usersdamage = []
        self.usersinround = []
        self.currentvalue = ("")
        self.bossHP = 0
        self.picking = False
        self.round = 0
        self.attack = False
        self.bossattack = 0
        self.bossball = None
        self.bosswildd = []
        self.bosswilda = []
        self.disqualified = []
        self.lasthitter = 0
        self.round_task = None
        
    def calculate_boss_hp(rarity: int, players: int) -> int:
        """Calculate the bosses HP according to rarity and number of people that joined"""
        base_hp = 50000 # set the HP according to your card's average stats

        if rarity == 1:  # T1
            base_hp = 70000

        extra_hp = (players // 1) * 40000

        return base_hp + extra_hp
    
    def cleanup_tasks(self):
        """Clean up any running tasks"""
        if self.round_task and not self.round_task.done():
            self.round_task.cancel()
            self.round_task = None
        
    async def start_first_round(self, channel: discord.TextChannel):
        """Start a defending round automatically and process the other logics frfrrfr"""
        output_channel = await get_output_channel(self.bot) or channel
        if self.boss_enabled and not self.picking:
            self.round += 1
            self.picking = True
            self.attack = False

            # Genera un nombre aleatorio para la imagen del boss
            def generate_random_name():
                source = string.ascii_uppercase + string.ascii_lowercase + string.ascii_letters
                return "".join(random.choices(source, k=15))

            extension = self.bossball.wild_card.split(".")[-1]
            file_location = "./admin_panel/media/" + self.bossball.wild_card
            file_name = f"nt_{generate_random_name()}.{extension}"

            if self.bosswildd[1] == 2:
                file = await self.bosswildd[0].to_file()
            else:
                file = discord.File(file_location, filename=file_name)

            await output_channel.send(
                f"Round {self.round}\n# {self.bossball.country} is preparing to defend! {self.bot.get_emoji(self.bossball.emoji_id)}",
                file=file
            )

            # Dar tiempo para selecciones manuales
            await asyncio.sleep(20)

            # Procesar selecciones automáticas para usuarios que no hayan seleccionado
            snapshotusers = self.users.copy()
            
            for user_id in snapshotusers:
                if [user_id, self.round] not in self.usersinround:
                    try:
                        # Auto select ball for user
                        ball = await self.auto_select_ball(user_id)
                        user = await self.bot.fetch_user(user_id)
                        
                        if not ball:
                            self.users.remove(user_id)
                            self.currentvalue += f"{user} had no valid {settings.collectible_name}s and died!\n"
                            continue
                            
                        self.balls.append(ball)
                        self.usersinround.append([user_id, self.round])
                        
                        # Calculate stats with buffs
                        ballattack = min(max(ball.attack, 0), MAXSTATS[0])
                        ballhealth = min(max(ball.health, 0), MAXSTATS[1])
                        
                        # Apply special buffs
                        ball_desc = ball.description(short=True)
                        if "✨" in ball_desc:
                            ballhealth += SHINYBUFFS[1]
                            ballattack += SHINYBUFFS[0]
                        elif "❄️" in ball_desc:
                            ballhealth += CHRISTMASBUFFS[1]
                            ballattack += CHRISTMASBUFFS[0]
                        elif "💫" in ball_desc:
                            ballhealth += MYTHICBUFFS[1]
                            ballattack += MYTHICBUFFS[0]
                        elif "⚔️" in ball_desc:
                            ballhealth += BOSSBUFFS[1]
                            ballattack += BOSSBUFFS[0]
                        
                        # Process attack
                        self.bossHP -= ballattack
                        self.usersdamage.append([user_id, ballattack, ball.description(short=True, include_emoji=True, bot=self.bot)])
                        self.currentvalue += f"{user}'s {ball.description(short=True, bot=self.bot)} dealt {ballattack} damage!\n"
                        self.lasthitter = user_id
                                
                    except Exception as e:
                        log.error(f"Error processing user {user_id}: {e}")
                        continue

            self.picking = False
            
            # Write round stats
            with open("roundstats.txt", "w") as file:
                file.write(f"{self.currentvalue}")
                
            # Send round end message
            if int(self.bossHP) <= 0:
                await channel.send(
                    f"# Round {self.round} has ended {self.bot.get_emoji(self.bossball.emoji_id)}\nThe boss has been defeated!"
                )
                # Boss has been defeated, conclude the battle
                await self.auto_conclude(channel)
                return
            else:
                await channel.send(
                    f"Round {self.round} is over, calculating damage ..."
                )
                
            # Send round stats file
            with open("roundstats.txt", "rb") as file:
                await output_channel.send(file=discord.File(file, "roundstats.txt"))
                    
            self.currentvalue = ("")
            self.round_task = None
            
            # Start next round automatically
            await self.start_next_round(output_channel)


    async def auto_select_ball(self, user_id: int) -> BallInstance | None:
        """
        Automatically select the best ball from user's inventory based on current round type.
        Returns the selected ball instance or None if no valid ball is found.
        """
        try:
            # Get the player
            player, _ = await Player.get_or_create(discord_id=user_id)
            
            # Get all tradeable balls owned by the player
            player_balls = await BallInstance.filter(player=player, tradeable=True)
            if not player_balls:
                return None
                
            # Sort balls based on current round type (attack or defend)
            if self.attack:
                # For defense rounds, sort by HP
                sorted_balls = sorted(player_balls, key=lambda x: (
                    x.health + (SHINYBUFFS[1] if "✨" in x.description(short=True) else 0) +
                    (CHRISTMASBUFFS[1] if "❄️" in x.description(short=True) else 0) +
                    (MYTHICBUFFS[1] if "💫" in x.description(short=True) else 0) +
                    (BOSSBUFFS[1] if "⚔️" in x.description(short=True) else 0)
                ), reverse=True)
            else:
                # For attack rounds, sort by ATK
                sorted_balls = sorted(player_balls, key=lambda x: (
                    x.attack + (SHINYBUFFS[0] if "✨" in x.description(short=True) else 0) +
                    (CHRISTMASBUFFS[0] if "❄️" in x.description(short=True) else 0) +
                    (MYTHICBUFFS[0] if "💫" in x.description(short=True) else 0) +
                    (BOSSBUFFS[0] if "⚔️" in x.description(short=True) else 0)
                ), reverse=True)
                
            # Return the best ball that hasn't been used yet
            for ball in sorted_balls:
                if ball not in self.balls:
                    return ball
                    
            return None
        except Exception as e:
            log.error(f"Error in auto_select_ball: {e}")
            return None

    async def process_round_selections(self, channel: discord.TextChannel):
        """
        Automatically process ball selections for all users in the round
        """
        snapshotusers = self.users.copy()
        
        for user_id in snapshotusers:
            if [user_id, self.round] not in self.usersinround:
                try:
                    # Auto select ball for user
                    ball = await self.auto_select_ball(user_id)
                    user = await self.bot.fetch_user(user_id)
                    
                    if not ball:
                        if self.attack:
                            self.users.remove(user_id)
                            self.currentvalue += f"{user} had no valid {settings.collectible_name}s and died!\n"
                        continue
                        
                    self.balls.append(ball)
                    self.usersinround.append([user_id, self.round])
                    
                    # Calculate stats with buffs
                    ballattack = min(max(ball.attack, 0), MAXSTATS[0])
                    ballhealth = min(max(ball.health, 0), MAXSTATS[1])
                    
                    # Apply special buffs
                    ball_desc = ball.description(short=True)
                    if "✨" in ball_desc:
                        ballhealth += SHINYBUFFS[1]
                        ballattack += SHINYBUFFS[0]
                    elif "❄️" in ball_desc:
                        ballhealth += CHRISTMASBUFFS[1]
                        ballattack += CHRISTMASBUFFS[0]
                    elif "💫" in ball_desc:
                        ballhealth += MYTHICBUFFS[1]
                        ballattack += MYTHICBUFFS[0]
                    elif "⚔️" in ball_desc:
                        ballhealth += BOSSBUFFS[1]
                        ballattack += BOSSBUFFS[0]
                    
                    # Process attack or defense
                    if not self.attack:
                        self.bossHP -= ballattack
                        self.usersdamage.append([user_id, ballattack, ball.description(short=True, include_emoji=True, bot=self.bot)])
                        self.currentvalue += f"{user}'s {ball.description(short=True, bot=self.bot)} dealt {ballattack} damage!\n"
                        self.lasthitter = user_id
                    else:
                        if self.bossattack >= ballhealth:
                            self.users.remove(user_id)
                            self.currentvalue += f"{user}'s {ball.description(short=True, bot=self.bot)} had {ballhealth}HP and died!\n"
                        else:
                            self.currentvalue += f"{user}'s {ball.description(short=True, bot=self.bot)} had {ballhealth}HP and survived!\n"
                            
                except Exception as e:
                    log.error(f"Error processing user {user_id}: {e}")
                    continue

    async def end_round_timeout(self, channel: discord.TextChannel):
        """Modified end_round_timeout to auto-process selections"""
        output_channel = await get_output_channel(self.bot) or channel
        await asyncio.sleep(20)  # Wait for 20 seconds just for testing and cuz im too lazy to wait for a minute, set how long you want your 
        # rounds to last for here 
        
        if not self.picking:  # Round was already ended manually
            return
            
        # Process remaining users who haven't selected
        await self.process_round_selections(channel)  # Add this line to process auto-selections
                
        if self.attack:
            snapshotusers = self.users.copy()
            for user in snapshotusers:
                user_id = user
                user_obj = await self.bot.fetch_user(int(user))
                if str(user_obj) not in self.currentvalue:
                    self.currentvalue += (str(user_obj) + " has not selected on time and died!\n")
                    self.users.remove(user_id)
                    
        self.picking = False
        
        # Write round stats
        with open("roundstats.txt", "w") as file:
            file.write(f"{self.currentvalue}")
            
        # Send round end message
        if not self.attack:
            if int(self.bossHP) <= 0:
                await output_channel.send(
                    f"# Round {self.round} has ended {self.bot.get_emoji(self.bossball.emoji_id)}\nThe boss has been defeated!"
                )
                # Boss has been defeated, conclude the battle
                await self.auto_conclude(output_channel)
                return
            else:
                await output_channel.send(
                    f"Round {self.round} is over, calculating damage ..."
                )
        else:
            if len(self.users) == 0:
                await output_channel.send(
                    f"# Round {self.round} has ended {self.bot.get_emoji(self.bossball.emoji_id)}\nThe boss has dealt {self.bossattack} damage!\nThe boss has won!"
                )
                # All players died, conclude the battle
                await self.auto_conclude(channel)
                return
            else:
                await output_channel.send(
                    f"# Round {self.round} has ended {self.bot.get_emoji(self.bossball.emoji_id)}\nThe boss has dealt {self.bossattack} damage!\n"
                )
                
        # Send round stats file
        with open("roundstats.txt", "rb") as file:
            await output_channel.send(file=discord.File(file, "roundstats.txt"))
                
        self.currentvalue = ("")
        self.round_task = None
        
        
        # Start next round automatically
        await self.start_next_round(output_channel)

    async def start_next_round(self, channel: discord.TextChannel):
        """Start the next round automatically after a cooldown"""
        output_channel = await get_output_channel(self.bot) or channel
        # Announce cooldown
        countdown_message = await channel.send("Next round starts in 10 seconds...")
        for i in range(10, 0, -1):
            await countdown_message.edit(content=f"Next round starts in ``{i} seconds``...")
            await asyncio.sleep(1)
        await countdown_message.edit(content="The next round is starting now.")
        
        self.round += 1
        
        # Randomly choose between attack and defend
        self.attack = random.choice([True, False])
        
        def generate_random_name():
            source = string.ascii_uppercase + string.ascii_lowercase + string.ascii_letters
            return "".join(random.choices(source, k=15))
            
        extension = self.bossball.wild_card.split(".")[-1]
        file_location = "./admin_panel/media/" + self.bossball.wild_card
        file_name = f"nt_{generate_random_name()}.{extension}"
        
        if self.attack:
            if self.bosswilda[1] == 2:
                file = await self.bosswilda[0].to_file()
            else:
                file = discord.File(file_location, filename=file_name)
                
            self.bossattack = random.randrange(DAMAGERNG[0], DAMAGERNG[1], 100)
            
            await output_channel.send(
                (f"Round {self.round}\n# {self.bossball.country} is preparing to attack! {self.bot.get_emoji(self.bossball.emoji_id)}"),
                file=file
            )
        else:
            if self.bosswildd[1] == 2:
                file = await self.bosswildd[0].to_file()
            else:
                file = discord.File(file_location, filename=file_name)
                
            await output_channel.send(
                (f"Round {self.round}\n# {self.bossball.country} is preparing to defend! {self.bot.get_emoji(self.bossball.emoji_id)}"),
                file=file
            )
        
        self.picking = True
        
        # Start the timeout task for the new round
        self.round_task = asyncio.create_task(self.end_round_timeout(output_channel))

    async def auto_conclude(self, channel: discord.TextChannel):
        """Automatically conclude the boss battle if theres no players alive left or boss dies"""
        output_channel = await get_output_channel(self.bot) or channel
        self.picking = False
        self.boss_enabled = False
        self.cleanup_tasks()
        
        # Calculate total damage for each user
        test = self.usersdamage
        damage_totals = {}
        
        for entry in test:
            user_id = entry[0]
            damage = entry[1]
            damage_totals[user_id] = damage_totals.get(user_id, 0) + damage
        
        # Sort users by total damage
        sorted_damages = sorted(damage_totals.items(), key=lambda x: x[1], reverse=True)
        
        # Create stats embed with different descriptions based on battle outcome
        embed = discord.Embed(
            title="Boss Fight Stats",
            color=discord.Color.red()
        )
        
        # Add damage leaderboard to embed
        damage_text = "The following players dealt the most damage:\n"
        for i, (user_id, damage) in enumerate(sorted_damages[:5], 1):
            user = await self.bot.fetch_user(user_id)
            damage_text += f"{i}. {user}: *{damage:,}*\n"
        
        embed.add_field(name="Damage", value=damage_text, inline=False)
        
        rewards_text = []
        
        # If boss was defeated (HP <= 0), give special boss ball to last hitter and regular balls to top 3
        if int(self.bossHP) <= 0:
            embed.description = "The top 3 have been rewarded with a regular ball, and the winner has been given the boss ball."
            
            # Give special boss ball to last hitter
            if self.lasthitter != 0:
                player, _ = await Player.get_or_create(discord_id=self.lasthitter)
                special = [x for x in specials.values() if x.name == "Boss"][0]
                instance = await BallInstance.create(
                    ball=self.bossball,
                    player=player,
                    special=special,
                    attack_bonus=0,
                    health_bonus=0,
                )
                last_hitter_user = await self.bot.fetch_user(self.lasthitter)
                rewards_text.append(f"{last_hitter_user.mention} received the special boss ball!")
                
                await log_action(
                    f"`BOSS REWARDS` gave special {settings.collectible_name} {self.bossball.country} to {last_hitter_user} for last hit. "
                    f"Special=Boss "
                    f"ATK=0 HP=0",
                    self.bot,
                )
        else:
            # If boss won (killed everyone), only give regular balls to top 3
            embed.description = "Despite failing to defeat the boss, the top 3 damage dealers have been rewarded with a regular ball."
        
        # Reward top 3 players with regular boss balls (excluding last hitter who got special if boss was defeated)
        for i, (user_id, _) in enumerate(sorted_damages[:3]):
            if int(self.bossHP) <= 0 and user_id == self.lasthitter:
                continue  # Skip if they got the special ball (only when boss was defeated)
                
            player, _ = await Player.get_or_create(discord_id=user_id)
            instance = await BallInstance.create(
                ball=self.bossball,
                player=player,
                special=None,  # No special for top 3
                attack_bonus=0,
                health_bonus=0,
            )
            
            await log_action(
                f"`BOSS REWARDS` gave {settings.collectible_name} {self.bossball.country} to {user} for top {i+1} damage. "
                f"Special=None "
                f"ATK=0 HP=0",
                self.bot,
            )
        
        # Send the embed
        await output_channel.send(embed=embed)
        
        # Reset all battle variables
        self.round = 0
        self.balls = []
        self.users = []
        self.currentvalue = ("")
        self.usersdamage = []
        self.usersinround = []
        self.bossHP = 0
        self.attack = False
        self.bossattack = 0
        self.bossball = None
        self.bosswildd = []
        self.bosswilda = []
        self.disqualified = []
        self.lasthitter = 0

    bossadmin = app_commands.Group(name="admin", description="admin commands for boss")

    @bossadmin.command(name="start")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def start(
        self,
        interaction: discord.Interaction,
        countryball: BallTransform,
        start_image: discord.Attachment | None = None,
        defend_image: discord.Attachment | None = None,
        attack_image: discord.Attachment | None = None):
        """
        Start the boss battle with automatically calculated HP based on boss rarity and player count
        """
        ball = countryball
        if self.boss_enabled == True:
            return await interaction.response.send_message(f"There is already an ongoing boss battle", ephemeral=True)
        
        self.cleanup_tasks()
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        self.round = 0
        self.balls = []
        self.users = []
        self.currentvalue = ("")
        self.usersdamage = []
        self.usersinround = []
        self.attack = False
        self.bossattack = 0
        self.bossball = None
        self.bosswildd = []
        self.bosswilda = []
        self.disqualified = []
        self.lasthitter = 0
        
        # Calculate HP based on boss rarity and initial number of players (we'll use 1 as starting point)
        # HP will scale as more players join during the join phase
        initial_hp = Boss.calculate_boss_hp(ball.rarity, 1)
        self.bossHP = initial_hp
        
        def generate_random_name():
            source = string.ascii_uppercase + string.ascii_lowercase + string.ascii_letters
            return "".join(random.choices(source, k=15))
            
        if start_image == None:
            extension = ball.collection_card.split(".")[-1]
            file_location = "./admin_panel/media/" + ball.collection_card
            file_name = f"nt_{generate_random_name()}.{extension}"
            file=discord.File(file_location, filename=file_name)
        else:
            file = await start_image.to_file()
            
        # Create join button view with updated timeout
        view = JoinButton(self)
        
        await interaction.followup.send(
            f"Boss successfully started", ephemeral=True
        )
        
        output_channel = await get_output_channel(self.bot) or interaction.channel
        
        message = await output_channel.send(
            f"<@&1308512664124788837> A boss battle has begun <a:Crown:1331246693546594372>", # change your starting message here
            file=file,
            view=view
        )
        
        # Store message reference in view for timeout handling
        view.message = message
        
        if ball != None:
            self.boss_enabled = True
            self.bossball = ball
            if defend_image == None:
                self.bosswildd.append(None)
                self.bosswildd.append(1)
            else:
                self.bosswildd.append(defend_image)
                self.bosswildd.append(2)
            if attack_image == None:
                self.bosswilda.append(None)
                self.bosswilda.append(1)
            else:
                self.bosswilda.append(attack_image)
                self.bosswilda.append(2)


    @bossadmin.command(name="stats")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def stats(self, interaction: discord.Interaction):
        """
        See current stats of the boss
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        with open("stats.txt","w") as file:
            file.write(f"Boss:{self.bossball}\nCurrentValue:\n\n{self.currentvalue}\nUsers:{self.users}\nDisqualifiedUsers:{self.disqualified}\nUsersDamage:{self.usersdamage}\nBalls:{self.balls}\nUsersInRound:{self.usersinround}")
        with open("stats.txt","rb") as file:
            return await interaction.followup.send(file=discord.File(file,"stats.txt"), ephemeral=True)

    @bossadmin.command(name="disqualify")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def disqualify(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        user_id : str | None = None,
        undisqualify : bool | None = False,
        ):
        """
        Disqualify a member from the boss
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        if (user and user_id) or (not user and not user_id):
            await interaction.followup.send(
                "You must provide either `user` or `user_id`.", ephemeral=True
            )
            return

        if not user:
            try:
                user = await self.bot.fetch_user(int(user_id))  # type: ignore
            except ValueError:
                await interaction.followup.send(
                    "The user ID you gave is not valid.", ephemeral=True
                )
                return
            except discord.NotFound:
                await interaction.followup.send(
                    "The given user ID could not be found.", ephemeral=True
                )
                return
        else:
            user_id = user.id
        if int(user_id) in self.disqualified:
            if undisqualify == True:
                self.disqualified.remove(int(user_id))
                await interaction.followup.send(
                    f"{user} has been removed from disqualification.\nUse `/boss admin hackjoin` to join the user back.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"{user} has already been disqualified.\nSet `undisqualify` to `True` to remove a user from disqualification.", ephemeral=True
                )
        elif undisqualify == True:
            await interaction.followup.send(
                f"{user} has **not** been disqualified yet.", ephemeral=True
            )
        elif self.boss_enabled != True:
            self.disqualified.append(int(user_id))
            await interaction.followup.send(
                f"{user} will be disqualified from the next fight.", ephemeral=True
            )
        elif int(user_id) not in self.users:
            self.disqualified.append(int(user_id))
            await interaction.followup.send(
                f"{user} has been disqualified successfully.", ephemeral=True
            )
            return
        else:
            self.users.remove(int(user_id))
            self.disqualified.append(int(user_id))
            await interaction.followup.send(
                f"{user} has been disqualified successfully.", ephemeral=True
            )
            return

    @app_commands.command()
    async def ongoing(self, interaction: discord.Interaction):
        """
        Show your damage to the boss in the current fight.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        snapshotdamage = self.usersdamage.copy()
        ongoingvalue = ("")
        ongoingfull = 0
        ongoingdead = False
        for i in range(len(snapshotdamage)):
            if snapshotdamage[i][0] == interaction.user.id:
                ongoingvalue += f"{snapshotdamage[i][2]}: {snapshotdamage[i][1]}\n\n"
                ongoingfull += snapshotdamage[i][1]
        if ongoingfull == 0:
            if interaction.user.id in self.users:
                await interaction.followup.send("You have not dealt any damage.",ephemeral=True)
            elif interaction.user.id in self.disqualified:
                await interaction.followup.send("You have been disqualified.",ephemeral=True)
            else:
                await interaction.followup.send("You have not joined the battle, or you have died.",ephemeral=True)
        else:
            if interaction.user.id in self.users:
                await interaction.followup.send(f"You have dealt {ongoingfull} damage.\n{ongoingvalue}",ephemeral=True)
            elif interaction.user.id in self.disqualified:
                await interaction.followup.send(f"You have dealt {ongoingfull} damage and have been disqualified.\n{ongoingvalue}",ephemeral=True)
            else:
                await interaction.followup.send(f"You have dealt {ongoingfull} damage and you are now dead.\n{ongoingvalue}",ephemeral=True)

    @bossadmin.command(name="ping")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def ping(self, interaction: discord.Interaction, unselected: bool | None = False):
        """
        Ping all the alive players
        """
        snapshotusers = self.users.copy()
        await interaction.response.defer(ephemeral=True, thinking=True)
        if len(snapshotusers) == 0:
            return await interaction.followup.send("There are no users joined/remaining",ephemeral=True)
        pingsmsg = "-#"
        if unselected:
            for userid in snapshotusers:
                if [userid,self.round] not in self.usersinround:
                    pingsmsg = pingsmsg+" <@"+str(userid)+">"
        else:
            for userid in snapshotusers:
                pingsmsg = pingsmsg+" <@"+str(userid)+">"
        if pingsmsg == "-#":
            await interaction.followup.send("All users have selected",ephemeral=True)
        elif len(pingsmsg) < 2000:
            await interaction.followup.send("Ping Successful",ephemeral=True)
            await interaction.channel.send(pingsmsg)
        else:
            await interaction.followup.send("Message too long, exceeds 2000 character limit",ephemeral=True)
            

    @bossadmin.command(name="conclude")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    @app_commands.choices(
        winner=[
            app_commands.Choice(name="Random", value="RNG"),
            app_commands.Choice(name="Most Damage", value="DMG"),
            app_commands.Choice(name="Last Hitter", value="LAST"),
            app_commands.Choice(name="No Winner", value="None"),
        ]
    )
    async def conclude(self, interaction: discord.Interaction, winner: str):
        """
        Finish the boss, conclude the Winner
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.picking = False
        self.boss_enabled = False
        self.cleanup_tasks()
        test = self.usersdamage
        test2 = []
        total = ("")
        total2 = ("")
        totalnum = []
        for i in range(len(test)):
            if test[i][0] not in test2:
                temp = 0
                tempvalue = test[i][0]
                test2.append(tempvalue)
                for j in range(len(test)):
                    if test[j][0] == tempvalue:
                        temp += test[j][1]
                if test[i][0] in self.users:
                    user = await self.bot.fetch_user(int(tempvalue))
                    total += (f"{user} has dealt a total of " + str(temp) + " damage!\n")
                    totalnum.append([tempvalue, temp])
                else:
                    user = await self.bot.fetch_user(int(tempvalue))
                    total2 += (f"[Dead/Disqualified] {user} has dealt a total of " + str(temp) + " damage!\n")

        bosswinner = 0
        highest = 0
        if winner == "DMG":
            for k in range(len(totalnum)):
                if totalnum[k][1] > highest:
                    highest = totalnum[k][1]
                    bosswinner = totalnum[k][0]
        elif winner == "LAST":
            bosswinner = self.lasthitter
        else:
            if len(totalnum) != 0:
                bosswinner = totalnum[random.randint(0,len(totalnum)-1)][0]
        if bosswinner == 0:
            await interaction.followup.send(
                f"Boss successfully concluded", ephemeral=True
            )
            await interaction.channel.send(f"# Boss has concluded \n💀 ᴛʜᴇ ʙᴏꜱꜱ ᴘʀᴏᴠᴇᴅ ᴜɴꜱᴛᴏᴘᴘᴀʙʟᴇ, ᴄʀᴜꜱʜɪɴɢ ᴀʟʟ ᴡʜᴏ ᴅᴀʀᴇᴅ ᴛᴏ ꜰᴀᴄᴇ ɪᴛ ᴀɴᴅ ꜱᴇᴄᴜʀɪɴɢ ɪᴛꜱ ꜰɪᴇʀᴄᴇ ᴅᴏᴍɪɴɪᴏɴ. 💀")
            with open("totalstats.txt", "w") as file:
                file.write(f"{total}{total2}")
            with open("totalstats.txt", "rb") as file:
                await interaction.channel.send(file=discord.File(file, "totalstats.txt"))
            self.round = 0
            self.balls = []
            self.users = []
            self.currentvalue = ("")
            self.usersdamage = []
            self.usersinround = []
            self.bossHP = 0
            self.round = 0
            self.attack = False
            self.bossattack = 0
            self.bossball = None
            self.bosswildd = []
            self.bosswilda = []
            self.disqualified = []
            self.lasthitter = 0
            return
        if winner != "None":
            player, created = await Player.get_or_create(discord_id=bosswinner)
            special = special = [x for x in specials.values() if x.name == "Boss"][0]
            instance = await BallInstance.create(
                ball=self.bossball,
                player=player,
                special=special,
                attack_bonus=0,
                health_bonus=0,
            )
            await interaction.followup.send(
                f"Boss successfully concluded", ephemeral=True
            )
            await interaction.followup.send(
                f"# <a:Crown:1331246693546594372> Boss has concluded \n👑 <@{bosswinner}>  ꜱᴛᴏᴏᴅ ᴛᴀʟʟ ᴀɢᴀɪɴꜱᴛ ᴏᴅᴅꜱ ᴀɴᴅ ᴄʟᴀɪᴍᴇᴅ ᴠɪᴄᴛᴏʀʏ ꜰʀᴏᴍ ᴀ ᴍɪɢʜᴛʏ ʙᴏꜱꜱ, ᴡʀɪᴛɪɴɢ ᴛʜᴇɪʀ ɴᴀᴍᴇ ɪɴ ʟᴇɢᴇɴᴅꜱ. 👑\n\n"
                f"`Boss` `{self.bossball}` {settings.collectible_name} was successfully given.\n"
            )
            bosswinner_user = await self.bot.fetch_user(int(bosswinner))

            await log_action(
                f"`BOSS REWARDS` gave {settings.collectible_name} {self.bossball.country} to {bosswinner_user}. "
                f"Special=Boss"
                f"ATK=0 HP=0",
                self.bot,
            )
        else:
            await interaction.followup.send(
                f"Boss successfully concluded", ephemeral=True
            )
            await interaction.followup.send(f"# Boss has concluded \nThe boss has been defeated!")
        with open("totalstats.txt", "w") as file:
            file.write(f"{total}{total2}")
        with open("totalstats.txt", "rb") as file:
            await interaction.followup.send(file=discord.File(file, "totalstats.txt"))
        self.round = 0
        self.balls = []
        self.users = []
        self.currentvalue = ("")
        self.usersdamage = []
        self.usersinround = []
        self.bossHP = 0
        self.round = 0
        self.attack = False
        self.bossattack = 0
        self.bossball = None
        self.bosswildd = []
        self.bosswilda = []
        self.disqualified = []
        self.lasthitter = 0

    @bossadmin.command(name="hackjoin")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def hackjoin(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        user_id: str | None = None,
        all: bool | None = False,
        ):
        """
        Join a user to the boss battle. Use 'all' to add every member in the server except bots.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Handle the 'all' option to add all members
        if all:
            if not self.boss_enabled:
                return await interaction.followup.send("Boss is disabled", ephemeral=True)
                
            if not interaction.guild:
                return await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            
            try:
                members_added = 0
                processed_ids = set()  # Para evitar duplicados
                
                # 1. Primero, intentamos con los miembros en caché
                for member in interaction.guild.members:
                    if not member.bot and member.id not in self.users:
                        processed_ids.add(member.id)
                        if member.id in self.disqualified:
                            self.disqualified.remove(member.id)
                        self.users.append(member.id)
                        members_added += 1
                
                # 2. Luego, revisamos los mensajes recientes en varios canales
                for channel_id in interaction.guild.text_channels:
                    try:
                        channel = interaction.guild.get_channel(channel_id.id)
                        if channel and channel.permissions_for(interaction.guild.me).read_message_history:
                            async for message in channel.history(limit=200):
                                if message.author.id not in processed_ids and not message.author.bot and message.author.id not in self.users:
                                    processed_ids.add(message.author.id)
                                    if message.author.id in self.disqualified:
                                        self.disqualified.remove(message.author.id)
                                    self.users.append(message.author.id)
                                    members_added += 1
                    except Exception:
                        # Ignoramos errores en canales individuales
                        continue
                
                # 3. Finalmente, si tienes roles en el servidor, puedes usar miembros con roles específicos
                try:
                    for role in interaction.guild.roles:
                        if role.name != "@everyone":  # Ignoramos el rol @everyone
                            for member in role.members:
                                if member.id not in processed_ids and not member.bot and member.id not in self.users:
                                    processed_ids.add(member.id)
                                    if member.id in self.disqualified:
                                        self.disqualified.remove(member.id)
                                    self.users.append(member.id)
                                    members_added += 1
                except Exception:
                    # Ignoramos errores al procesar roles
                    pass
                
                # Si no se añadió ningún miembro, informar al usuario
                if members_added == 0:
                    return await interaction.followup.send(
                        "No se pudo añadir ningún miembro. Intenta usar el comando con usuarios específicos.", 
                        ephemeral=True
                    )
                
                # Recalculate boss HP based on new player count
                new_hp = Boss.calculate_boss_hp(self.bossball.rarity, len(self.users))
                self.bossHP = new_hp
                    
                await interaction.followup.send(
                    f"Added {members_added} server members to the boss battle. Boss HP adjusted to {new_hp}.", 
                    ephemeral=True
                )
                
                await log_action(
                    f"{members_added} server members have been added to the `{self.bossball}` Boss Battle. [hackjoin all by {interaction.user}]",
                    self.bot,
                )
                return
            except Exception as e:
                log.error(f"Error adding all members: {e}")
                return await interaction.followup.send(f"Error adding all members: {e}", ephemeral=True)
