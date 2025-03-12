import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from tortoise.exceptions import DoesNotExist

from typing import TYPE_CHECKING, Optional

from ballsdex.core.models import BallInstance, Player, Special, specials, balls
from ballsdex.core.utils.transformers import BallEnabledTransform
from ballsdex.settings import settings
from ballsdex.core.utils.paginator import FieldPageSource, Pages
from ballsdex.core.utils.logging import log_action

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.collector.cog")

T1Req = 50
T1Rarity = 1
CommonReq = 500
CommonRarity = 150
RoundingOption = 10

dT1Req = 3
dT1Rarity = 1
dCommonReq = 10
dCommonRarity = 150
dRoundingOption = 1

gradient = (CommonReq - T1Req) / (CommonRarity - T1Rarity)
dgradient = (dCommonReq - dT1Req) / (dCommonRarity - dT1Rarity)

class Collector(commands.GroupCog, name="claim"):
    """
    Claim special cards like Collector, Diamond, or Emerald, and periodically check for unmet cards.
    """
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        self.announcement_channel_id = 1331591409030922251  
        self.check_unmet_cards.start()  

    # Subcomando /claim collector
    @app_commands.command(name="collector")
    async def claim_collector(
        self,
        interaction: discord.Interaction,
        countryball: BallEnabledTransform
    ):
        """
        Claim a Collector card for a countryball.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to obtain the collector card for.
        """
        await self._claim_card(interaction, countryball, "Collector", gradient, T1Rarity, T1Req, RoundingOption)

    # Subcomando /claim diamond
    @app_commands.command(name="diamond")
    async def claim_diamond(
        self,
        interaction: discord.Interaction,
        countryball: BallEnabledTransform
    ):
        """
        Claim a Diamond card for a countryball.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to obtain the diamond card for.
        """
        shiny_special = next((x for x in specials.values() if x.name == "Shiny"), None)
        if not shiny_special:
            return await interaction.response.send_message("The 'Shiny' special is not configured in the bot.", ephemeral=True)
        await self._claim_card(interaction, countryball, "Diamond", dgradient, dT1Rarity, dT1Req, dRoundingOption, required_special=shiny_special)

    
    @app_commands.command(name="emerald")
    async def claim_emerald(
        self,
        interaction: discord.Interaction,
        countryball: BallEnabledTransform
    ):
        """
        Claim an Emerald card for a countryball. Requires Collector, Diamond, and specific event cards.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to obtain the emerald card for.
        """
        if interaction.response.is_done():
            return
        assert interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        
        emerald_special = next((x for x in specials.values() if x.name == "Emerald"), None)
        if not emerald_special:
            return await interaction.followup.send("The 'Emerald' special is not configured in the bot. Contact an admin.", ephemeral=True)

        if await BallInstance.filter(special=emerald_special, player__discord_id=interaction.user.id, ball=countryball).count() >= 1:
            return await interaction.followup.send(f"You already have a {countryball.country} emerald card.", ephemeral=True)

        collector_special = next((x for x in specials.values() if x.name == "Collector"), None)
        diamond_special = next((x for x in specials.values() if x.name == "Diamond"), None)
        shiny_special = next((x for x in specials.values() if x.name == "Shiny"), None)

        if not all([collector_special, diamond_special, shiny_special]):
            return await interaction.followup.send("One or more required specials (Collector, Diamond, Shiny) are missing from the bot.", ephemeral=True)

        has_collector = await BallInstance.filter(special=collector_special, player__discord_id=interaction.user.id, ball=countryball).count() >= 1
        has_diamond = await BallInstance.filter(special=diamond_special, player__discord_id=interaction.user.id, ball=countryball).count() >= 1

        non_hidden_specials = [s for s in specials.values() if not s.hidden and s.name not in ["Collector", "Diamond", "Emerald", "Shiny"]]
        special_requirements = {}
        missing_requirements = []
        required_shinies = 0

        for special in non_hidden_specials:
            total_instances = await BallInstance.filter(special=special).count()
            required_count = max(1, total_instances // 4)
            user_count = await BallInstance.filter(special=special, player__discord_id=interaction.user.id).count()

            if total_instances < 4:
                required_shinies += 1
            elif user_count < required_count:
                missing_requirements.append(f"{required_count} {special.name} cards (you have {user_count})")

            special_requirements[special.name] = {"required": required_count, "has": user_count}

        user_shinies = await BallInstance.filter(special=shiny_special, player__discord_id=interaction.user.id, ball=countryball).count()
        if user_shinies < required_shinies:
            missing_requirements.append(f"{required_shinies} Extra Shiny {countryball.country}.")

        if not has_collector:
            missing_requirements.append(f"1 Collector {countryball.country}")
        if not has_diamond:
            missing_requirements.append(f"1 Diamond {countryball.country}")

        if missing_requirements:
            await interaction.followup.send(
                f"You don't meet the requirements for an Emerald {countryball.country} card. Missing:\n" +
                "\n".join([f"- {req}" for req in missing_requirements]),
                ephemeral=True
            )
            return

        await BallInstance.create(ball=countryball, player=player, attack_bonus=0, health_bonus=0, special=emerald_special)
        await interaction.followup.send(
            f"Congrats! You have claimed an Emerald {countryball.country} card!",
            ephemeral=True
        )

    async def _claim_card(
        self,
        interaction: discord.Interaction,
        countryball: BallEnabledTransform,
        special_name: str,
        gradient: float,
        t1_rarity: float,
        t1_req: int,
        rounding_option: int,
        required_special: Optional[Special] = None
    ):
        if interaction.response.is_done():
            return
        assert interaction.guild
        filters = {"ball": countryball}
        special = next((x for x in specials.values() if x.name == special_name), None)
        if not special:
            return await interaction.response.send_message(f"The '{special_name}' special is not configured in the bot.", ephemeral=True)
        
        checkfilter = {"special": special, "player__discord_id": interaction.user.id, "ball": countryball}
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        checkcounter = await BallInstance.filter(**checkfilter).count()
        if checkcounter >= 1:
            return await interaction.followup.send(f"You already have a {countryball.country} {special_name.lower()} card.", ephemeral=True)

        filters["player__discord_id"] = interaction.user.id
        if required_special:
            filters["special"] = required_special
        balls_count = await BallInstance.filter(**filters).count()
        collector_number = int(int((gradient * (countryball.rarity - t1_rarity) + t1_req) / rounding_option) * rounding_option)

        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        if balls_count >= collector_number:
            await interaction.followup.send(
                f"Congrats! You are now a {countryball.country} {special_name.lower()} collector.",
                ephemeral=True
            )
            await BallInstance.create(ball=countryball, player=player, attack_bonus=0, health_bonus=0, special=special)
        else:
            shinytext = " Shiny✨" if required_special else ""
            await interaction.followup.send(
                f"You need {collector_number}{shinytext} {countryball.country} to create a {special_name.lower()} card. You currently have {balls_count}.",
                ephemeral=True
            )

    @app_commands.command()
    async def list(self, interaction: discord.Interaction["BallsDexBot"], diamond: bool | None = False):
        """
        Show the collector card list of the dex - inspired by GamingadlerHD, made by MoOfficial.
        """
        enabled_collectibles = [x for x in balls.values() if x.enabled]

        if not enabled_collectibles:
            await interaction.response.send_message(
                f"There are no collectibles registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        sorted_collectibles = sorted(enabled_collectibles, key=lambda x: x.rarity)
        entries = []
        text0 = "Diamond" if diamond else "Collector"
        shinytext = "Shinies✨" if diamond else "Amount"

        for collectible in sorted_collectibles:
            name = f"{collectible.country}"
            emoji = self.bot.get_emoji(collectible.emoji_id)
            emote = str(emoji) if emoji else "N/A"
            rarity1 = int(int((dgradient if diamond else gradient) * (collectible.rarity - (dT1Rarity if diamond else T1Rarity)) + (dT1Req if diamond else T1Req)) / (dRoundingOption if diamond else RoundingOption)) * (dRoundingOption if diamond else RoundingOption)
            entry = (name, f"{emote} {shinytext} required: {rarity1}")
            entries.append(entry)

        per_page = 5
        source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
        source.embed.description = f"__**{settings.bot_name} {text0} Card List**__"
        source.embed.colour = discord.Colour.from_rgb(190, 100, 190)
        source.embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(ephemeral=True)

    
    @tasks.loop(hours=1) 
    async def check_unmet_cards(self):
        """
        Automatically check for unmet Collector, Diamond, and Emerald cards, delete them, notify users, and log to channel.
        """
        await self.bot.wait_until_ready()  

        special_types = {
            "Collector": {"gradient": gradient, "t1_rarity": T1Rarity, "t1_req": T1Req, "rounding": RoundingOption, "req_special": None},
            "Diamond": {"gradient": dgradient, "t1_rarity": dT1Rarity, "t1_req": dT1Req, "rounding": dRoundingOption, "req_special": "Shiny"},
            "Emerald": {"gradient": None, "t1_rarity": None, "t1_req": None, "rounding": None, "req_special": None}
        }
        shiny_special = next((x for x in specials.values() if x.name == "Shiny"), None)
        collector_special = next((x for x in specials.values() if x.name == "Collector"), None)
        diamond_special = next((x for x in specials.values() if x.name == "Diamond"), None)
        emerald_special = next((x for x in specials.values() if x.name == "Emerald"), None)

        if not all([shiny_special, collector_special, diamond_special, emerald_special]):
            log.error("One or more required specials (Shiny, Collector, Diamond, Emerald) are missing.")
            return

        unmet_cards = {name: [] for name in special_types.keys()}

        for special_name, config in special_types.items():
            if special_name == "Emerald":
                continue
            special = next((x for x in specials.values() if x.name == special_name), None)
            instances = await BallInstance.filter(special=special).prefetch_related("player", "ball")
            for instance in instances:
                checkfilter = {"player__discord_id": instance.player.discord_id, "ball": instance.ball}
                if config["req_special"]:
                    checkfilter["special"] = shiny_special
                required_count = int(int((config["gradient"] * (instance.ball.rarity - config["t1_rarity"]) + config["t1_req"]) / config["rounding"]) * config["rounding"])
                user_count = await BallInstance.filter(**checkfilter).count()
                if user_count < required_count:
                    unmet_cards[special_name].append(instance)

        non_hidden_specials = [s for s in specials.values() if not s.hidden and s.name not in ["Collector", "Diamond", "Emerald", "Shiny"]]
        emerald_instances = await BallInstance.filter(special=emerald_special).prefetch_related("player", "ball")
        for instance in emerald_instances:
            has_collector = await BallInstance.filter(special=collector_special, player__discord_id=instance.player.discord_id, ball=instance.ball).count() >= 1
            has_diamond = await BallInstance.filter(special=diamond_special, player__discord_id=instance.player.discord_id, ball=instance.ball).count() >= 1
            
            required_shinies = 0
            for s in non_hidden_specials:
                if await BallInstance.filter(special=s).count() < 4:
                    required_shinies += 1
            
            user_shinies = await BallInstance.filter(special=shiny_special, player__discord_id=instance.player.discord_id, ball=instance.ball).count()
            special_counts = {s.name: await BallInstance.filter(special=s, player__discord_id=instance.player.discord_id).count() for s in non_hidden_specials}
            required_counts = {s.name: max(1, await BallInstance.filter(special=s).count() // 4) for s in non_hidden_specials if await BallInstance.filter(special=s).count() >= 4}

            if not has_collector or not has_diamond or user_shinies < required_shinies or any(special_counts.get(name, 0) < req for name, req in required_counts.items()):
                unmet_cards["Emerald"].append(instance)

        total_unmet = sum(len(cards) for cards in unmet_cards.values())
        if total_unmet == 0:
            log.info("Clean up done. No unmet0s were found")
            return

        log_text = f"Automatic cleanup executed. Deleted {total_unmet} unmet cards:\n"
        for special_name, cards in unmet_cards.items():
            if cards:
                log_text += f"\n**{special_name} Cards ({len(cards)}):**\n"
                for card in cards:
                    player = await self.bot.fetch_user(int(f"{card.player}"))
                    log_text += f"- {player} ({card.player.discord_id}): {card.ball.country} #{card.pk:0X}\n"
                    try:
                        await player.send(f"Your {card.ball.country} {special_name.lower()} card has been deleted due to unmet requirements.")
                    except:
                        log.warning(f"Failed to send DM to {player} ({card.player.discord_id})")
                    await card.delete()

        
        announcement_channel = self.bot.get_channel(self.announcement_channel_id)
        if not announcement_channel:
            log.error(f"Could not find announcement channel with ID {self.announcement_channel_id}.")
            return

        await announcement_channel.send(log_text)
        await log_action(
            f"Automatic task deleted {total_unmet} cards for unmet requirements: {len(unmet_cards['Collector'])} Collector, {len(unmet_cards['Diamond'])} Diamond, {len(unmet_cards['Emerald'])} Emerald.",
            self.bot,
        )

    @check_unmet_cards.before_loop
    async def before_check_unmet_cards(self):
        await self.bot.wait_until_ready()  

async def setup(bot: "BallsDexBot"):
    await bot.add_cog(Collector(bot))
