import asyncio
import logging
import random
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.utils import format_dt
from tortoise.exceptions import BaseORMException, DoesNotExist
from tortoise.expressions import Q
from random import randint

from ballsdex.core.bot import BallsDexBot
from ballsdex.core.models import Ball, BallInstance, Player, Special, Trade, TradeObject
from ballsdex.core.utils.buttons import ConfirmChoiceView
from ballsdex.core.utils.logging import log_action
from ballsdex.core.utils.transformers import (
    BallTransform,
    EconomyTransform,
    RegimeTransform,
    SpecialTransform,
)
from ballsdex.packages.countryballs.countryball import CountryBall
from ballsdex.settings import settings

log = logging.getLogger("ballsdex.packages.admin.balls")
FILENAME_RE = re.compile(r"^(.+)(\.\S+)$")

LOGCHANNEL = 1331591409030922251
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


async def save_file(attachment: discord.Attachment) -> Path:
    path = Path(f"./admin_panel/media/{attachment.filename}")
    match = FILENAME_RE.match(attachment.filename)
    if not match:
        raise TypeError("The file you uploaded lacks an extension.")
    i = 1
    while path.exists():
        path = Path(f"./admin_panel/media/{match.group(1)}-{i}{match.group(2)}")
        i = i + 1
    await attachment.save(path)
    return path.relative_to("./admin_panel/media/")


class Balls(app_commands.Group):
    """
    Countryballs management
    """

    # Fixed spawn method with correct regime parameter passing
    async def _spawn_bomb(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball: Ball | None,
        regime: RegimeTransform | None,
        channel: discord.TextChannel,
        n: int,
        special: Special | None = None,
        atk_bonus: int | None = None,
        hp_bonus: int | None = None,
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
        task = interaction.client.loop.create_task(update_message_loop())
        try:
            for i in range(n):
                if not countryball:
                    if regime:
                        # Handle regime filtering manually
                        balls = await Ball.filter(regime=regime, enabled=True)
                        if balls:
                            ball = CountryBall(random.choice(balls))
                        else:
                            task.cancel()
                            await interaction.followup.edit_message(
                                "@original",  # type: ignore
                                content=f"No {settings.plural_collectible_name} found with the specified regime.",
                            )
                            return
                    else:
                        ball = await CountryBall.get_random()
                else:
                    ball = CountryBall(countryball)
                    
                ball.special = special
                ball.atk_bonus = atk_bonus
                ball.hp_bonus = hp_bonus
                
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

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def spawn(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball: BallTransform | None = None,
        regime: RegimeTransform | None = None,
        channel: discord.TextChannel | None = None,
        n: app_commands.Range[int, 1, 100] = 1,
        special: SpecialTransform | None = None,
        atk_bonus: int | None = None,
        hp_bonus: int | None = None,
    ):
        """
        Force spawns a random or specified countryball, optionally from a specified regime.

        Parameters
        ----------
        countryball: Ball | None
            The countryball you want to spawn. Random according to rarities if not specified.
        regime: Regime | None
            The regime from which to spawn a countryball. Ignored if a countryball is specified.
        channel: discord.TextChannel | None
            The channel you want to spawn the countryball in. Current channel if not specified.
        n: int
            The number of countryballs to spawn. If no countryball was specified, it's random
            every time.
        special: Special | None
            Force the countryball to have a special attribute when caught.
        atk_bonus: int | None
            Force the countryball to have a specific attack bonus when caught.
        hp_bonus: int | None
            Force the countryball to have a specific health bonus when caught.
        """
        if interaction.response.is_done():
            return

        if n > 1:
            await self._spawn_bomb(
                interaction, countryball, regime, channel or interaction.channel, n, special, atk_bonus, hp_bonus  # type: ignore
            )
            await log_action(
                f"{interaction.user} spawned {special} {regime} {settings.collectible_name} "
                f"{countryball or 'random'} {n} times in {channel or interaction.channel}",
                interaction.client
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        if not countryball:
            if regime:
                ball = CountryBall(await Ball.filter(regime=regime).first()) #type: ignore
            else:
                ball = await CountryBall.get_random()
        if not countryball:
            ball = await CountryBall.get_random()
        else:
            ball = CountryBall(countryball)
        ball.special = special
        ball.atk_bonus = atk_bonus
        ball.hp_bonus = hp_bonus 

        result = await ball.spawn(channel or interaction.channel) #type: ignore

        if result:
            await interaction.followup.send(
                f"{settings.collectible_name.title()} spawned.", ephemeral=True
            )
            special_attrs = []
            if special is not None:
                special_attrs.append(f"special={special.name}")
            if atk_bonus is not None:
                special_attrs.append(f"atk={atk_bonus}")
            if hp_bonus is not None:
                special_attrs.append(f"hp={hp_bonus}")
            await log_action(
                f"{interaction.user} spawned {settings.collectible_name}{special}{atk_bonus}{hp_bonus}{ball.name} "
                f"in {channel or interaction.channel}"
                f"{f" ({", ".join(special_attrs)})" if special_attrs else ""}.",
                interaction.client,
            )
    
    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def wheel(
        self,
        interaction: discord.Interaction,
        countryball: BallTransform | None = None,
        regime: RegimeTransform | None = None,
        economy: EconomyTransform | None = None,
        min_attack: int | None = None,
        max_attack: int | None = None,
        min_health: int | None = None,
        max_health: int | None = None,
        special: SpecialTransform | None = None,
        rarity_min: float | None = None,
        rarity_max: float | None = None,
        tradeable_only: bool = False,
        atk_bonus: int | None = None,
        hp_bonus: int | None = None,
    ):
        """
        Spin the wheel to get a random ball with optional filters.

        Parameters
        ----------
        countryball: Ball | None
            The specific countryball you want to get. Random according to rarities if not specified.
        regime: Regime | None
            The regime from which to get a countryball. Ignored if a countryball is specified.
        economy: Economy | None
            The economy from which to get a countryball. Ignored if a countryball is specified.
        min_attack: Minimum attack stat
            Maximum attack stat
        min_health: Minimum health stat
            Maximum health stat
        special: Special | None
            Force the countryball to have a special attribute.
        tradeable_only: Only show tradeable balls
            Whether to only show tradeable balls.
        atk_bonus: int | None
            Force a specific attack bonus
        hp_bonus: int | None
            Force a specific health bonus
        """
        await interaction.response.defer(thinking=True)
        
        # If specific ball is provided, use that
        if countryball:
            ball = countryball
        else:
            # Build query with filters
            query = Q(enabled=True)
            
            if regime:
                query &= Q(regime=regime)
            
            if economy:
                query &= Q(economy=economy)
            
            if min_attack is not None:
                query &= Q(attack__gte=min_attack)
            
            if max_attack is not None:
                query &= Q(attack__lte=max_attack)
            
            if min_health is not None:
                randint (-20, 20)
            
            if max_health is not None:
                randint (-20, 20)
            
            if tradeable_only:
                query &= Q(tradeable=True)
            
            # Get a random ball matching our filters
            try:
                balls = await Ball.filter(query).all()
                if balls:
                    ball = random.choice(balls)
                else:
                    return await interaction.followup.send("No balls found matching these filters.")
                if not ball:
                    return await interaction.followup.send("No balls found matching these filters.")
            except Exception as e:
                return await interaction.followup.send(f"Error retrieving ball: {e}")
        
        # Set special event if specified
        special_event = None
        if special:
            special_event = special
        # If special not specified but we want a random special
        elif random.random() < 0:  # 10% chance for random special
            try:
                now = timezone.now() #type: ignore
                special_event = await Special.filter(
                    Q(Q(start_date__lte=now) | Q(start_date__isnull=True)) &
                    Q(Q(end_date__gte=now) | Q(end_date__isnull=True)) &
                    Q(hidden=False)
                ).order_by("?").first()
            except Exception:
                pass
        
        # Generate random stat bonuses if not specified
        attack_bonus = atk_bonus if atk_bonus is not None else random.randint(-20, 20)
        health_bonus = hp_bonus if hp_bonus is not None else random.randint(-20, 20)
        
        # Format output strings
        plusatk = "+" if attack_bonus >= 0 else ""
        plushp = "+" if health_bonus >= 0 else ""
        
        # Build regime and economy info
        regime_info = f"Regime: {ball.cached_regime.name}" if ball.cached_regime else ""
        economy_info = f"Economy: {ball.cached_economy.name}" if ball.cached_economy else ""
        system_info = f"{regime_info} | {economy_info}" if regime_info and economy_info else regime_info or economy_info
        
        # Build capacity info
        capacity_info = f"**{ball.capacity_name}**: {ball.capacity_description}"
        
        # Special card info
        special_info = f"**Special Event:** {special_event.name}" if special_event else ""
        
        # Format the final message
        message = (
            f"# {ball.country}\n"
            f"**Stats:** `{plusatk}{attack_bonus}% ATK/{plushp}{health_bonus}% HP`\n"
            f"**Base Stats:** ATK: {ball.attack} | HP: {ball.health}\n"
            f"{system_info}\n"
            f"{special_info}"
        )
        
        try:
            emoji = interaction.client.get_emoji(ball.emoji_id)
            if emoji:
                message = f"{emoji} {message}"
        except:
            pass
            
        await interaction.followup.send(message)

    @app_commands.command()
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def give(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball: BallTransform,
        user: discord.User,
        quantity: app_commands.Range[int, 1, 100] = 1,  # Added quantity parameter
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
        quantity: int
            The number of countryballs to give. Default is 1.
        special: Special | None
        health_bonus: int | None
            Omit this to make it random.
        attack_bonus: int | None
            Omit this to make it random.
        """
        # the transformers triggered a response, meaning user tried an incorrect input
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        player, created = await Player.get_or_create(discord_id=user.id)
        
        # Track the given balls for reporting
        given_balls = []
        
        # Give the specified quantity of countryballs
        for _ in range(quantity):
            instance = await BallInstance.create(
                ball=countryball,
                player=player,
                attack_bonus=(
                    attack_bonus
                    if attack_bonus is not None
                    else random.randint(-settings.max_attack_bonus, settings.max_attack_bonus)
                ),
                health_bonus=(
                    health_bonus
                    if health_bonus is not None
                    else random.randint(-settings.max_health_bonus, settings.max_health_bonus)
                ),
                special=special,
            )
            given_balls.append(instance)
        
        # Prepare the response message
        if quantity == 1:
            ball = given_balls[0]
            response = (
                f"`{countryball.country}` {settings.collectible_name} was successfully given to "
                f"`{user}`.\nSpecial: `{special.name if special else None}` • ATK: "
                f"`{ball.attack_bonus:+d}` • HP:`{ball.health_bonus:+d}` "
            )
        else:
            response = (
                f"{quantity} `{countryball.country}` {settings.plural_collectible_name} were successfully "
                f"given to `{user}`."
            )
            
        await interaction.followup.send(response)
        
        # Log the action
        if quantity == 1:
            ball = given_balls[0]
            await log_action(
                f"{interaction.user} gave {settings.collectible_name} "
                f"{countryball.country} to {user}. (Special={special.name if special else None} "
                f"ATK={ball.attack_bonus:+d} HP={ball.health_bonus:+d}).",
                interaction.client,
            )
        else:
            await log_action(
                f"{interaction.user} gave {quantity} {settings.collectible_name} {special} {countryball} {attack_bonus} {health_bonus}"
                f"{countryball.country} to {user}.",
                interaction.client,
            )

    @app_commands.command(name="info")
    @app_commands.checks.has_any_role(*settings.root_role_ids, *settings.admin_role_ids)
    async def balls_info(self, interaction: discord.Interaction[BallsDexBot], countryball_id: str):
        """
        Show information about a countryball.

        Parameters
        ----------
        countryball_id: str
            The ID of the countryball you want to get information about.
        """
        try:
            pk = int(countryball_id, 16)
        except ValueError:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave is not valid.", ephemeral=True
            )
            return
        try:
            ball = await BallInstance.get(id=pk).prefetch_related(
                "player", "trade_player", "special"
            )
        except DoesNotExist:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave does not exist.", ephemeral=True
            )
            return
        spawned_time = format_dt(ball.spawned_time, style="R") if ball.spawned_time else "N/A"
        catch_time = (
            (ball.catch_date - ball.spawned_time).total_seconds()
            if ball.catch_date and ball.spawned_time
            else "N/A"
        )
        admin_url = (
            f"[View online](<{settings.admin_url}/bd_models/ballinstance/{ball.pk}/change/>)"
            if settings.admin_url
            else ""
        )
        await interaction.response.send_message(
            f"**{settings.collectible_name.title()} ID:** {ball.pk}\n"
            f"**Player:** {ball.player}\n"
            f"**Name:** {ball.countryball}\n"
            f"**Attack:** {ball.attack}\n"
            f"**Attack bonus:** {ball.attack_bonus}\n"
            f"**Health bonus:** {ball.health_bonus}\n"
            f"**Health:** {ball.health}\n"
            f"**Special:** {ball.special.name if ball.special else None}\n"
            f"**Caught at:** {format_dt(ball.catch_date, style='R')}\n"
            f"**Spawned at:** {spawned_time}\n"
            f"**Catch time:** {catch_time} seconds\n"
            f"**Caught in:** {ball.server_id if ball.server_id else 'N/A'}\n"
            f"**Traded:** {ball.trade_player}\n{admin_url}",
            ephemeral=True,
        )
        await log_action(f"{interaction.user} got info for {ball}({ball.pk}).", interaction.client)

    @app_commands.command(name="delete")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def balls_delete(
        self, interaction: discord.Interaction[BallsDexBot], countryball_id: str
    ):
        """
        Delete a countryball.

        Parameters
        ----------
        countryball_id: str
            The ID of the countryball you want to delete.
        """
        try:
            ballIdConverted = int(countryball_id, 16)
        except ValueError:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave is not valid.", ephemeral=True
            )
            return
        try:
            ball = await BallInstance.get(id=ballIdConverted)
        except DoesNotExist:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave does not exist.", ephemeral=True
            )
            return
        await ball.delete()
        await interaction.response.send_message(
            f"{settings.collectible_name.title()} {countryball_id} deleted.", ephemeral=True
        )
        await log_action(f"{interaction.user} deleted {ball}({ball.pk}).", interaction.client)
    @app_commands.command(name="bulk_transfer")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def balls_bulk_transfer(
        self,
        interaction: discord.Interaction[BallsDexBot],
        donor: discord.User,
        receiver: discord.User,
    ):
        """
        Transfer all countryballs from one user to another.

        Parameters
        ----------
        donor: discord.User
            The user from whom the countryballs will be transferred.
        receiver: discord.User
            The user who will receive the countryballs.
        """
        # Get the player objects for both the donor and the receiver
        donor_player, _ = await Player.get_or_create(discord_id=donor.id)
        receiver_player, _ = await Player.get_or_create(discord_id=receiver.id)

        # Get all countryballs owned by the donor
        balls_to_transfer = await BallInstance.filter(player=donor_player).prefetch_related("player")

        if not balls_to_transfer:
            await interaction.response.send_message(
                f"{donor} does not own any countryballs to transfer.", ephemeral=True
            )
            return
    
        # Start the bulk transfer
        for ball in balls_to_transfer:
            ball.player = receiver_player  
            await ball.save()

            # Log the transfer in the trade table
            trade = await Trade.create(player1=donor_player, player2=receiver_player)
            await TradeObject.create(trade=trade, ballinstance=ball, player=donor_player)

        await interaction.response.send_message(
            f"Transferred all countryballs from {donor} to {receiver}.",
            ephemeral=True,
        )

        # Log the action
        await log_action(
            f"{interaction.user} bulk-transferred all countryballs from {donor} to {receiver}.",
            interaction.client,
        )
        
    @app_commands.command(name="transfer")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def balls_transfer(
        self,
        interaction: discord.Interaction[BallsDexBot],
        countryball_id: str,
        user: discord.User,
    ):
        """
        Transfer a countryball to another user.

        Parameters
        ----------
        countryball_id: str
            The ID of the countryball you want to transfer.
        user: discord.User
            The user you want to transfer the countryball to.
        """
        try:
            ballIdConverted = int(countryball_id, 16)
        except ValueError:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave is not valid.", ephemeral=True
            )
            return
        try:
            ball = await BallInstance.get(id=ballIdConverted).prefetch_related("player")
            original_player = ball.player
        except DoesNotExist:
            await interaction.response.send_message(
                f"The {settings.collectible_name} ID you gave does not exist.", ephemeral=True
            )
            return
        player, _ = await Player.get_or_create(discord_id=user.id)
        ball.player = player
        await ball.save()

        trade = await Trade.create(player1=original_player, player2=player)
        await TradeObject.create(trade=trade, ballinstance=ball, player=original_player)
        await interaction.response.send_message(
            f"Transfered {ball}({ball.pk}) from {original_player} to {user}.",
            ephemeral=True,
        )
        await log_action(
            f"{interaction.user} transferred {ball}({ball.pk}) from {original_player} to {user}.",
            interaction.client,
        )

    @app_commands.command(name="reset")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def balls_reset(
        self,
        interaction: discord.Interaction[BallsDexBot],
        user: discord.User,
        percentage: int | None = None,
    ):
        """
        Reset a player's countryballs.

        Parameters
        ----------
        user: discord.User
            The user you want to reset the countryballs of.
        percentage: int | None
            The percentage of countryballs to delete, if not all. Used for sanctions.
        """
        player = await Player.get_or_none(discord_id=user.id)
        if not player:
            await interaction.response.send_message(
                "The user you gave does not exist.", ephemeral=True
            )
            return
        if percentage and not 0 < percentage < 100:
            await interaction.response.send_message(
                "The percentage must be between 1 and 99.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not percentage:
            text = f"Are you sure you want to delete {user}'s {settings.plural_collectible_name}?"
        else:
            text = (
                f"Are you sure you want to delete {percentage}% of "
                f"{user}'s {settings.plural_collectible_name}?"
            )
        view = ConfirmChoiceView(
            interaction,
            accept_message=f"Confirmed, deleting the {settings.plural_collectible_name}...",
            cancel_message="Request cancelled.",
        )
        await interaction.followup.send(
            text,
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return
        if percentage:
            balls = await BallInstance.filter(player=player)
            to_delete = random.sample(balls, int(len(balls) * (percentage / 100)))
            for ball in to_delete:
                await ball.delete()
            count = len(to_delete)
        else:
            count = await BallInstance.filter(player=player).delete()
        await interaction.followup.send(
            f"{count} {settings.plural_collectible_name} from {user} have been deleted.",
            ephemeral=True,
        )
        await log_action(
            f"{interaction.user} deleted {percentage or 100}% of "
            f"{player}'s {settings.plural_collectible_name}.",
            interaction.client,
        )

    @app_commands.command(name="count")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def balls_count(
        self,
        interaction: discord.Interaction[BallsDexBot],
        user: discord.User | None = None,
        countryball: BallTransform | None = None,
        special: SpecialTransform | None = None,
        regime: RegimeTransform | None = None,  # Add regime parameter
    ):
        """
        Count the number of countryballs that a player has or how many exist in total.

        Parameters
        ----------
        user: discord.User
            The user you want to count the countryballs of.
        countryball: Ball
        special: Special
        regime: Regime
            Filter countryballs by regime.
        """
        if interaction.response.is_done():
            return
        filters = {}
        if countryball:
            filters["ball"] = countryball
        if special:
            filters["special"] = special
        if user:
            filters["player__discord_id"] = user.id
        
        # Add regime filter logic
        if regime:
            filters["ball__regime"] = regime
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        balls = await BallInstance.filter(**filters).count()
        verb = "is" if balls == 1 else "are"
        country = f"{countryball.country} " if countryball else ""
        plural = "s" if balls > 1 or balls == 0 else ""
        special_str = f"{special.name} " if special else ""
        regime_str = f"{regime.name} " if regime else ""
        
        if user:
            await interaction.followup.send(
                f"{user} has {balls} {special_str}{regime_str}"
                f"{country}{settings.collectible_name}{plural}."
            )
        else:
            await interaction.followup.send(
                f"There {verb} {balls} {special_str}{regime_str}"
                f"{country}{settings.collectible_name}{plural}."
            )

    @app_commands.command(name="create")
    @app_commands.checks.has_any_role(*settings.root_role_ids)
    async def balls_create(
        self,
        interaction: discord.Interaction[BallsDexBot],
        *,
        name: app_commands.Range[str, None, 48],
        regime: RegimeTransform,
        health: int,
        attack: int,
        emoji_id: app_commands.Range[str, 17, 21],
        capacity_name: app_commands.Range[str, None, 64],
        capacity_description: app_commands.Range[str, None, 256],
        collection_card: discord.Attachment,
        image_credits: str,
        economy: EconomyTransform | None = None,
        rarity: float = 0.0,
        enabled: bool = False,
        tradeable: bool = False,
        wild_card: discord.Attachment | None = None,
    ):
        """
        Shortcut command for creating countryballs. They are disabled by default.

        Parameters
        ----------
        name: str
        regime: Regime
        economy: Economy | None
        health: int
        attack: int
        emoji_id: str
            An emoji ID, the bot will check if it can access the custom emote
        capacity_name: str
        capacity_description: str
        collection_card: discord.Attachment
        image_credits: str
        rarity: float
            Value defining the rarity of this countryball, if enabled
        enabled: bool
            If true, the countryball can spawn and will show up in global completion
        tradeable: bool
            If false, all instances are untradeable
        wild_card: discord.Attachment
            Artwork used to spawn the countryball, with a default
        """
        if regime is None or interaction.response.is_done():  # economy autocomplete failed
            return

        if not emoji_id.isnumeric():
            await interaction.response.send_message(
                "`emoji_id` is not a valid number.", ephemeral=True
            )
            return
        emoji = interaction.client.get_emoji(int(emoji_id))
        if not emoji:
            await interaction.response.send_message(
                "The bot does not have access to the given emoji.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        default_path = Path("./ballsdex/core/image_generator/src/default.png")
        missing_default = ""
        if not wild_card and not default_path.exists():
            missing_default = (
                "**Warning:** The default spawn image is not set. This will result in errors when "
                f"attempting to spawn this {settings.collectible_name}. You can edit this on the "
                "web panel or add an image at `./ballsdex/core/image_generator/src/default.png`.\n"
            )

        try:
            collection_card_path = await save_file(collection_card)
        except Exception as e:
            log.exception("Failed saving file when creating countryball", exc_info=True)
            await interaction.followup.send(
                f"Failed saving the attached file: {collection_card.url}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
            return
        try:
            wild_card_path = await save_file(wild_card) if wild_card else default_path
        except Exception as e:
            log.exception("Failed saving file when creating countryball", exc_info=True)
            await interaction.followup.send(
                f"Failed saving the attached file: {collection_card.url}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
            return

        try:
            ball = await Ball.create(
                country=name,
                regime=regime,
                economy=economy,
                health=health,
                attack=attack,
                rarity=rarity,
                enabled=enabled,
                tradeable=tradeable,
                emoji_id=emoji_id,
                wild_card="/" + str(wild_card_path),
                collection_card="/" + str(collection_card_path),
                credits=image_credits,
                capacity_name=capacity_name,
                capacity_description=capacity_description,
            )
        except BaseORMException as e:
            log.exception("Failed creating countryball with admin command", exc_info=True)
            await interaction.followup.send(
                f"Failed creating the {settings.collectible_name}.\n"
                f"Partial error: {', '.join(str(x) for x in e.args)}\n"
                "The full error is in the bot logs."
            )
        else:
            files = [await collection_card.to_file()]
            if wild_card:
                files.append(await wild_card.to_file())
            await interaction.client.load_cache()
            admin_url = (
                f"[View online](<{settings.admin_url}/bd_models/ball/{ball.pk}/change/>)\n"
                if settings.admin_url
                else ""
            )
            await interaction.followup.send(
                f"Successfully created a {settings.collectible_name} with ID {ball.pk}! "
                f"The internal cache was reloaded.\n{admin_url}"
                f"{missing_default}\n"
                f"{name=} regime={regime.name} economy={economy.name if economy else None} "
                f"{health=} {attack=} {rarity=} {enabled=} {tradeable=} emoji={emoji}",
                files=files,
            )