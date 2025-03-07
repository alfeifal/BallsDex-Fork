import datetime
import logging
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button
from discord.utils import format_dt
from tortoise.exceptions import BaseORMException, DoesNotExist, IntegrityError
from tortoise.expressions import Q
from ballsdex.core.models import PrivacyPolicy
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.models import Player as PlayerModel

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
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.logging import log_action
from ballsdex.core.utils.paginator import FieldPageSource, Pages, TextPageSource
from ballsdex.core.utils.transformers import (
    BallTransform,
    EconomyTransform,
    RegimeTransform,
    SpecialTransform,
    BallEnabledTransform,
    BallInstanceTransform,
    SpecialEnabledTransform,
    TradeCommandType,
)
from ballsdex.packages.countryballs.countryball import CountryBall
from ballsdex.packages.trade.display import TradeViewFormat, fill_trade_embed_fields
from ballsdex.packages.trade.trade_user import TradingUser
from ballsdex.settings import settings

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot
    from ballsdex.packages.countryballs.cog import CountryBallsSpawner

log = logging.getLogger("ballsdex.packages.adminplus.cog")
FILENAME_RE = re.compile(r"^(.+)(\.\S+)$")


async def save_file(attachment: discord.Attachment) -> Path:
    path = Path(f"./static/uploads/{attachment.filename}")
    match = FILENAME_RE.match(attachment.filename)
    if not match:
        raise TypeError("The file you uploaded lacks an extension.")
    i = 1
    while path.exists():
        path = Path(f"./static/uploads/{match.group(1)}-{i}{match.group(2)}")
        i = i + 1
    await attachment.save(path)
    return path


@app_commands.guilds(*settings.admin_guild_ids)
@app_commands.default_permissions(administrator=True)
class Adminplus(commands.GroupCog):
    """
    Bot admin (plus) commands.
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        if not self.bot.intents.members:
            self.__cog_app_commands_group__.get_command("privacy").parameters[  # type: ignore
                0
            ]._Parameter__parent.choices.pop()  # type: ignore
        self.blacklist.parent = self.__cog_app_commands_group__
        self.balls.parent = self.__cog_app_commands_group__

    blacklist = app_commands.Group(name="blacklist", description="Bot blacklist management")
    blacklist_guild = app_commands.Group(
        name="blacklistguild", description="Guild blacklist management"
    )
    balls = app_commands.Group(
        name=settings.players_group_cog_name, description="Balls management"
    )
    logs = app_commands.Group(name="logs", description="Bot logs management")
    history = app_commands.Group(name="history", description="Trade history management")

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    @app_commands.choices(
        policy=[
            app_commands.Choice(name="Open Inventory", value=PrivacyPolicy.ALLOW),
            app_commands.Choice(name="Private Inventory", value=PrivacyPolicy.DENY),
            app_commands.Choice(name="Same Server", value=PrivacyPolicy.SAME_SERVER),
        ]
    )
    async def privacy(self, interaction: discord.Interaction, policy: PrivacyPolicy):
        """
        Set the bot's privacy policy.
        """
        if policy == PrivacyPolicy.SAME_SERVER and not self.bot.intents.members:
            await interaction.response.send_message(
                "I need the `members` intent to use this policy.", ephemeral=True
            )
            return
        if settings.bot_name == "dragonballdex":
            botuserid = 1293338035500351538
        else:
            botuserid = 1237889057330303057
        player, _ = await PlayerModel.get_or_create(discord_id=botuserid)
        player.privacy_policy = policy
        await player.save()
        await interaction.response.send_message(
            f"The bot's privacy policy has been set to **{policy.name}**.", ephemeral=True
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def completion(
            self,
            interaction: discord.Interaction["BallsDexBot"],
            special: SpecialEnabledTransform | None = None,
    ):
        """
        Show completion of the BallsDex.

        Parameters
        ----------
        special: Special
            The special you want to see the completion of
        """
        user = None
        await interaction.response.defer(thinking=True)
        extra_text = f"{special.name} " if special else ""
        if user is not None:
            try:
                player = await Player.get(discord_id=user_obj.id)
            except DoesNotExist:
                await interaction.followup.send(
                    f"There are no "
                    f"{extra_text}{settings.plural_collectible_name} yet."
                )
                return

            if await inventory_privacy(self.bot, interaction, player) is False:
                return
        # Filter disabled balls, they do not count towards progression
        # Only ID and emoji is interesting for us
        bot_countryballs = {x: y.emoji_id for x, y in balls.items() if y.enabled}

        # Set of ball IDs owned by the player
        filters = {"ball__enabled": True}
        if special:
            filters["special"] = special
            bot_countryballs = {
                x: y.emoji_id
                for x, y in balls.items()
                if y.enabled and y.created_at < special.end_date
            }
        if not bot_countryballs:
            await interaction.followup.send(
                f"There are no {extra_text}{settings.plural_collectible_name}"
                " registered on this bot yet.",
                ephemeral=True,
            )
            return

        owned_countryballs = set(
            x[0]
            for x in await BallInstance.filter(**filters)
            .distinct()  # Do not query everything
            .values_list("ball_id")
        )

        entries: list[tuple[str, str]] = []

        def fill_fields(title: str, emoji_ids: set[int]):
            # check if we need to add "(continued)" to the field name
            first_field_added = False
            buffer = ""

            for emoji_id in emoji_ids:
                emoji = self.bot.get_emoji(emoji_id)
                if not emoji:
                    continue

                text = f"{emoji} "
                if len(buffer) + len(text) > 1024:
                    # hitting embed limits, adding an intermediate field
                    if first_field_added:
                        entries.append(("\u200B", buffer))
                    else:
                        entries.append((f"__**{title}**__", buffer))
                        first_field_added = True
                    buffer = ""
                buffer += text

            if buffer:  # add what's remaining
                if first_field_added:
                    entries.append(("\u200B", buffer))
                else:
                    entries.append((f"__**{title}**__", buffer))

        if owned_countryballs:
            # Getting the list of emoji IDs from the IDs of the owned countryballs
            fill_fields(
                f"Existing {settings.plural_collectible_name}",
                set(bot_countryballs[x] for x in owned_countryballs),
            )
        else:
            entries.append((f"__**Existing {settings.plural_collectible_name}**__", "Nothing yet."))

        if missing := set(y for x, y in bot_countryballs.items() if x not in owned_countryballs):
            fill_fields(f"Missing {settings.plural_collectible_name}", missing)
        else:
            entries.append(
                (
                    f"__**:tada: No missing {settings.plural_collectible_name}, "
                    "congratulations! :tada:**__",
                    "\u200B",
                )
            )  # force empty field value

        source = FieldPageSource(entries, per_page=5, inline=False, clear_description=False)
        special_str = f" ({special.name})" if special else ""
        source.embed.description = (
            f"{settings.bot_name}{special_str} progression: "
            f"**{round(len(owned_countryballs) / len(bot_countryballs) * 100, 1)}%**"
        )
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(name=(settings.bot_name), icon_url=self.bot.user.avatar.url)

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start()



    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def special_rarity(self, interaction: discord.Interaction):
        # DO NOT CHANGE THE CREDITS TO THE AUTHOR HERE!
        """
        Show the special count list of the dex - made by GamingadlerHD
        """
        # Filter enabled collectibles
        events = [x for x in specials.values()]

        if not events:
            await interaction.response.send_message(
                f"There are no events registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        # Sort collectibles by rarity in ascending order

        entries = []

        for special in events:
            name = f"{special.name}"
            emoji = special.emoji

            if emoji:
                emote = str(emoji)
            else:
                emote = "N/A"

            filters = {}
            filters["special"] = special

            count = await BallInstance.filter(**filters)
            countNum = len(count)
            # sorted_collectibles = sorted(enabled_collectibles.values(), key=lambda x: x.rarity)
            # if you want the Rarity to only show full numbers like 1 or 12 use the code part here:
            # rarity = int(collectible.rarity)
            # otherwise you want to display numbers like 1.5, 5.3, 76.9 use the normal part.

            entry = (name, f"{emote} Count: {countNum}")
            entries.append(entry)
        # This is the number of countryballs who are displayed at one page,
        # you can change this, but keep in mind: discord has an embed size limit.
        per_page = 5

        source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
        source.embed.description = (
            f"__**{settings.bot_name} events rarity**__"
        )
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(
            name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
        )

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(
            ephemeral=True,
        )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def rarity(self, interaction: discord.Interaction["BallsDexBot"], chunked: bool = True):
        # DO NOT CHANGE THE CREDITS TO THE AUTHOR HERE!
        """
        Show the ACTUAL rarities of the dex - made by GamingadlerHD
        """
        # Filter enabled collectibles
        enabled_collectibles = [x for x in balls.values() if x.enabled]

        if not enabled_collectibles:
            await interaction.response.send_message(
                f"There are no collectibles registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        # Sort collectibles by rarity in ascending order
        sorted_collectibles = sorted(enabled_collectibles, key=lambda x: x.rarity)

        entries = []

        for collectible in sorted_collectibles:
            name = f"{collectible.country}"
            emoji = self.bot.get_emoji(collectible.emoji_id)

            if emoji:
                emote = str(emoji)
            else:
                emote = "N/A"
            # if you want the Rarity to only show full numbers like 1 or 12 use the code part here:
            # rarity = int(collectible.rarity)
            # otherwise you want to display numbers like 1.5, 5.3, 76.9 use the normal part.
            rarity = collectible.rarity

            entry = (name, f"{emote} Rarity: {rarity}")
            entries.append(entry)
        # This is the number of countryballs who are displayed at one page,
        # you can change this, but keep in mind: discord has an embed size limit.
        per_page = 5

        source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
        source.embed.description = (
            f"__**{settings.bot_name} rarity**__"
        )
        source.embed.colour = discord.Colour.blurple()
        source.embed.set_author(
            name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
        )

        pages = Pages(source=source, interaction=interaction, compact=True)
        await pages.start(
            ephemeral=True,
        )

    async def _spawn_bomb(
            self,
            interaction: discord.Interaction,
            countryball: Ball | None,
            channel: discord.TextChannel,
            n: int,
    ):
        spawned = 0

        async def update_message_loop():
            nonlocal spawned
            for i in range(5 * 12 * 10):  # timeout progress after 10 minutes
                await interaction.followup.edit_message(
                    "@original",  # type: ignore
                    content=f"Spawn bomb in progress in {channel.mention}, "
                            f"{settings.collectible_name.title()}: {countryball or 'Random'}\n"
                            f"{spawned}/{n} spawned ({round((spawned / n) * 100)}%)",
                )
                await asyncio.sleep(5)
            await interaction.followup.edit_message(
                "@original", content="Spawn bomb seems to have timed out."  # type: ignore
            )

        await interaction.response.send_message(
            f"Starting spawn bomb in {channel.mention}...", ephemeral=True
        )
        task = self.bot.loop.create_task(update_message_loop())
        try:
            for i in range(n):
                if not countryball:
                    ball = await CountryBall.get_random()
                else:
                    ball = CountryBall(countryball)
                result = await ball.spawn(channel)
                if not result:
                    task.cancel()
                    await interaction.followup.edit_message(
                        "@original",  # type: ignore
                        content=f"A {settings.collectible_name} failed to spawn, probably "
                                "indicating a lack of permissions to send messages "
                                f"or upload files in {channel.mention}.",
                    )
                    return
                spawned += 1
            task.cancel()
            await interaction.followup.edit_message(
                "@original",  # type: ignore
                content=f"Successfully spawned {spawned} {settings.plural_collectible_name} "
                        f"in {channel.mention}!",
            )
        finally:
            task.cancel()

    @balls.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def spawn(
            self,
            interaction: discord.Interaction,
            countryball: BallTransform | None = None,
            channel: discord.TextChannel | None = None,
            n: int = 1,
    ):
        """
        Force spawn a random or specified countryball.

        Parameters
        ----------
        countryball: Ball | None
            The countryball you want to spawn. Random according to rarities if not specified.
        channel: discord.TextChannel | None
            The channel you want to spawn the countryball in. Current channel if not specified.
        n: int
            The number of countryballs to spawn. If no countryball was specified, it's random
            every time.
        """
        # the transformer triggered a response, meaning user tried an incorrect input
        if interaction.response.is_done():
            return

        if n < 1:
            await interaction.response.send_message(
                "`n` must be superior or equal to 1.", ephemeral=True
            )
            return
        if n > 100:
            await interaction.response.send_message(
                f"That doesn't seem reasonable to spawn {n} times, "
                "the bot will be rate-limited. Try something lower than 100.",
                ephemeral=True,
            )
            return

        if n > 1:
            await self._spawn_bomb(
                interaction, countryball, channel or interaction.channel, n  # type: ignore
            )
            await log_action(
                f"{interaction.user} spawned {settings.collectible_name}"
                f" {countryball or 'random'} {n} times in {channel or interaction.channel}.",
                self.bot,
            )

            return

        if countryball:
            if countryball.enabled == False:
                return await interaction.response.send_message(
                    f"You do not have permission to spawn this {settings.collectible_name}", ephemeral=True
                )
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not countryball:
            ball = await CountryBall.get_random()
        else:
            ball = CountryBall(countryball)
        result = await ball.spawn(channel or interaction.channel)  # type: ignore

        if result:
            await interaction.followup.send(
                f"{settings.collectible_name.title()} spawned.", ephemeral=True
            )
            await log_action(
                f"{interaction.user} spawned {settings.collectible_name} {ball.name} "
                f"in {channel or interaction.channel}.",
                self.bot,
            )

    @balls.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def wheel(
        self,
        interaction: discord.Interaction,
        shiny_percentage: float | None = float(-1),
        mythical_percentage: float | None = float(-1),
    ):
        """
        Spin the wheel!.
        """
        await interaction.response.defer(thinking=True)
        ball = await CountryBall.get_random()

        shinyresult = ""
        mythicalresult = ""
        plusatk = ""
        plushp = ""
        shinyrng = random.randint(0,100)
        mythicalrng = random.randint(0,100)
        atkrng = random.randint(-10, 10)*10
        if atkrng >= 0:
            plusatk = "+"
        hprng = random.randint(-10, 10)*10
        if hprng >= 0:
            plushp = "+"
        if shinyrng <= (shiny_percentage):
            shinyresult = f"\n***✨ It's a shiny {settings.collectible_name}! ✨***"
        elif mythicalrng <= (mythical_percentage):
            mythicalresult = f"\n*🔮 This {settings.collectible_name} exudes a mythical aura.🔮*"
        await interaction.followup.send(f"# {ball.name}\n`{plusatk}{atkrng}ATK/{plushp}{hprng}HP`{shinyresult}{mythicalresult}")


    @balls.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def give(
        self,
        interaction: discord.Interaction,
        countryball: BallTransform,
        user: discord.User,
        special: SpecialTransform | None = None,
        health_bonus: int | None = None,
        attack_bonus: int | None = None,
    ):
        """
        Give the specified countryball to a player.

        Parameters
        ----------
        countryball: Ball
        user: discord.User
        special: Special | None
        health_bonus: int | None
            Omit this to make it random.
        attack_bonus: int | None
            Omit this to make it random.
        """
        # the transformers triggered a response, meaning user tried an incorrect input
        if interaction.response.is_done():
            return
        if countryball.tradeable == False:
            return await interaction.response.send_message(f"You do not have permission to give this {settings.collectible_name}", ephemeral=True)
        paintarray = ["Shiny","Mythical","Gold","Titanium White","Black","Cobalt","Crimson","Forest Green","Saffron","Sky Blue","Pink","Purple","Lime","Orange","Grey","Burnt Sienna"]
        if special != None:
            if str(special) not in paintarray:
                return await interaction.response.send_message("You do not have permission to give this special",ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)

        player, created = await Player.get_or_create(discord_id=user.id)
        instance = await BallInstance.create(
            ball=countryball,
            player=player,
            attack_bonus=(
                attack_bonus
                if attack_bonus is not None
                else random.randint(-20, 20)
            ),
            health_bonus=(
                health_bonus
                if health_bonus is not None
                else random.randint(-20, 20)
            ),
            special=special,
        )
        await interaction.followup.send(
            f"`{countryball.country}` {settings.collectible_name} was successfully given to "
            f"`{user}`.\nSpecial: `{special.name if special else None}` • ATK: "
            f"`{instance.attack_bonus:+d}` • HP:`{instance.health_bonus:+d}` "
        )
        await log_action(
            f"{interaction.user} gave {settings.collectible_name} "
            f"{countryball.country} to {user}. (Special={special.name if special else None} "
            f"ATK={instance.attack_bonus:+d} HP={instance.health_bonus:+d}).",
            self.bot,
        )

    @balls.command(name="count")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def balls_count(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        ball: BallTransform | None = None,
        special: SpecialTransform | None = None,
    ):
        """
        Count the number of balls that a player has or how many exist in total.

        Parameters
        ----------
        user: discord.User
            The user you want to count the balls of.
        ball: Ball
        special: Special
        """
        if interaction.response.is_done():
            return
        filters = {}
        if ball:
            filters["ball"] = ball
        if special:
            filters["special"] = special
        if user:
            filters["player__discord_id"] = user.id
        await interaction.response.defer(ephemeral=True, thinking=True)
        balls = await BallInstance.filter(**filters).count()
        country = f"{ball.country} " if ball else ""
        plural = "s" if balls > 1 or balls == 0 else ""
        special_str = f"{special.name} " if special else ""
        if user:
            await interaction.followup.send(
                f"{user} has {balls} {special_str}"
                f"{country}{settings.collectible_name}{plural}."
            )
        else:
            await interaction.followup.send(
                f"There are {balls} {special_str}"
                f"{country}{settings.collectible_name}{plural}."
            )

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def count_list(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        special: SpecialTransform | None = None,):
        # DO NOT CHANGE THE CREDITS TO THE AUTHOR HERE!
        """
        Counts every character - made by GamingadlerHD and Mo Official

        Parameters
        ----------
        user: discord.User
            The user you want to count the balls of.
        special: Special
        """
        # Filter enabled collectibles
        enabled_collectibles = [x for x in balls.values() if x.enabled]

        if not enabled_collectibles:
            await interaction.response.send_message(
                f"There are no collectibles registered in {settings.bot_name} yet.",
                ephemeral=True,
            )
            return

        # Sort collectibles by rarity in ascending order
        sorted_collectibles = sorted(enabled_collectibles, key=lambda x: x.rarity)

        # Sort collectibles by rarity in ascending order

        entries = []
        nothingcheck = ""

        for collectible in sorted_collectibles:
            name = f"{collectible.country}"
            emoji = self.bot.get_emoji(collectible.emoji_id)

            if emoji:
                emote = str(emoji)
            else:
                emote = "N/A"

            filters = {}
            filters["ball"] = collectible
            if special:
                filters["special"] = special
            if user:
                filters["player__discord_id"] = user.id

            count = await BallInstance.filter(**filters)
            countNum = len(count)
            # sorted_collectibles = sorted(enabled_collectibles.values(), key=lambda x: x.rarity)
            # if you want the Rarity to only show full numbers like 1 or 12 use the code part here:
            # rarity = int(collectible.rarity)
            # otherwise you want to display numbers like 1.5, 5.3, 76.9 use the normal part.
            if countNum != 0:
                entry = (name, f"{emote} Count: {countNum}")
                entries.append(entry)
                nothingcheck = "something lol"

        # This is the number of countryballs who are displayed at one page,
        # you can change this, but keep in mind: discord has an embed size limit.
        per_page = 5
        special_str = f" ({special.name})" if special else ""
        if nothingcheck == "":
            if user:
                return await interaction.response.send_message(
                    f"{user} has no {special_str} {settings.plural_collectible_name} yet.",
                    ephemeral=True,
                )
            else:
                return await interaction.response.send_message(
                    f"There are no {special_str} {settings.plural_collectible_name} yet.",
                    ephemeral=True,
                )
        else:
            source = FieldPageSource(entries, per_page=per_page, inline=False, clear_description=False)
            source.embed.description = (
                f"__**{settings.bot_name}{special_str} count**__"
            )
            source.embed.colour = discord.Colour.blurple()
            source.embed.set_author(
                name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
            )

            pages = Pages(source=source, interaction=interaction, compact=True)
            await pages.start(
                ephemeral=True,
            )
