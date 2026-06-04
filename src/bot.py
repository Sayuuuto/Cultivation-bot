from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import get_config
from .db import get_session, init_db
from .content import load_all_content
from .duel_challenges import expire_stale_challenges
from .game import utcnow
from .inventory import load_item_catalog
from .reminders import (
    reminder_dm_content,
    fetch_due_reminders,
    mark_reminder_sent,
)
from .shop import load_shop_catalog
from .game import ORIGINS
from .discord_ui.helpers import (
    NOT_STARTED_HINT,
    cooldown_remaining,
    ensure_player,
    format_seconds,
    get_discord_id,
    get_guild_id,
    interaction_ctx,
    realm_display,
    rng_for,
    to_utc,
)
from .discord_guild import provision_new_cultivator
from .discord_ui.views import (
    AbandonStuckCombatView,
    AdventureChoiceView,
    CombatView,
)

ORIGIN_CHOICES = [app_commands.Choice(name=o, value=o) for o in ORIGINS]

from .discord_ui.commands.misc_cog import MiscCog  # noqa: E402  — re-export for tests
start_cmd = MiscCog.start_cmd

from .discord_ui.helpers import _story_continue_after_creation  # noqa: E402  — re-export for story_delivery.py

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "DEBUG").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cultivation_bot")

load_all_content()
load_item_catalog()
load_shop_catalog()

intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


def register_cogs_sync() -> None:
    """Register all Cogs on bot.tree synchronously (no event loop needed).

    Used by tests that import *bot* and need a populated command tree
    without starting the bot client.  For production the same registration
    happens asynchronously in *setup_hook*.
    """
    from .discord_ui.commands import ALL_COGS
    for CogCls in ALL_COGS:
        cog = CogCls()
        for cmd in cog.__cog_app_commands__:
            cmd.binding = cog
            if not cmd.parent:
                bot.tree.add_command(cmd)
    from .dungeon_discord import setup_dungeon_command
    setup_dungeon_command(bot)


register_cogs_sync()


@bot.event
async def setup_hook():
    """Production Cog registration — runs when the bot starts."""
    from .discord_ui.commands import ALL_COGS
    if bot.tree.get_commands():
        return
    for CogCls in ALL_COGS:
        await bot.add_cog(CogCls())
    from .dungeon_discord import setup_dungeon_command
    setup_dungeon_command(bot)



@bot.tree.interaction_check
async def elder_trial_command_gate(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return True
    cmd = interaction.command
    if cmd is None:
        return True

    session = get_session()
    try:
        from .story_mode import check_elder_trial_command

        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return True
        msg = check_elder_trial_command(player, cmd.name)
        if msg:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=False)
            else:
                await interaction.response.send_message(msg, ephemeral=False)
            return False
        return True
    finally:
        session.close()


@tasks.loop(seconds=60)
async def send_due_reminders() -> None:
    session = get_session()
    try:
        now = utcnow()
        from .dungeon_party import expire_stale_dungeon_parties

        if expire_stale_dungeon_parties(session, now):
            session.commit()
        due = fetch_due_reminders(session, now)
        if not due:
            return
        for row in due:
            try:
                user = await bot.fetch_user(int(row.player.discord_id))
                await user.send(reminder_dm_content(row.reminder.activity, row.player))
                row.player.remind_dms_blocked = False
            except discord.Forbidden:
                row.player.remind_dms_blocked = True
                logger.warning(
                    "Reminder DM blocked guild=%s user=%s activity=%s",
                    row.player.guild_id,
                    row.player.discord_id,
                    row.reminder.activity,
                )
            except discord.HTTPException:
                logger.exception(
                    "Reminder DM failed guild=%s user=%s activity=%s",
                    row.player.guild_id,
                    row.player.discord_id,
                    row.reminder.activity,
                )
                continue
            mark_reminder_sent(session, row.reminder)
            session.add(row.player)
        session.commit()
    except Exception:
        logger.exception("Reminder background task failed")
        session.rollback()
    finally:
        session.close()


@send_due_reminders.before_loop
async def before_send_due_reminders() -> None:
    await bot.wait_until_ready()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if interaction.type is discord.InteractionType.autocomplete:
        original = getattr(error, "original", error)
        if isinstance(original, discord.NotFound):
            logger.debug(
                "Autocomplete expired for %s (user typed faster than response)",
                interaction_ctx(interaction),
            )
            return

    logger.exception("App command error: %s error=%r", interaction_ctx(interaction), error)
    content = "Something went wrong in that command. Check the bot logs."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=False)
        else:
            await interaction.response.send_message(content, ephemeral=False)
    except discord.NotFound:
        if interaction.channel is not None:
            try:
                await interaction.channel.send(
                    f"{interaction.user.mention} {content}",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:
                logger.exception("Failed channel fallback for %s", interaction_ctx(interaction))
        else:
            logger.exception("Failed to send error response for %s", interaction_ctx(interaction))
    except Exception:
        logger.exception("Failed to send error response for %s", interaction_ctx(interaction))


@bot.event
async def on_ready():
    if not send_due_reminders.is_running():
        send_due_reminders.start()

    cfg = get_config()
    print(f"Logged in as {bot.user} (guild sync: {cfg.guild_id or 'global'})")
    print(f"Database: {cfg.database_path}")
    from .ui.fonts import card_fonts_available, card_images_enabled

    if card_images_enabled():
        if card_fonts_available():
            print("Profile/techniques cards: PNG enabled (fonts OK)")
        else:
            print(
                "Profile/techniques cards: fonts missing — using text embed fallback. "
                "Ensure assets/fonts/DejaVuSans.ttf is deployed."
            )
    else:
        print("Profile/techniques cards: disabled (PROFILE_CARD_IMAGE=0)")
    try:
        session = get_session()
        try:
            from sqlalchemy import func, select
            from .models import Player

            count = session.scalar(select(func.count()).select_from(Player)) or 0
            print(f"Players in database: {count}")
        finally:
            session.close()
    except Exception as exc:
        print(f"Could not count players: {exc}")
    if getattr(bot, "_did_sync_commands", False):
        return
    bot._did_sync_commands = True
    session = get_session()
    try:
        now = utcnow()
        expired = expire_stale_challenges(session, now)
        from .dungeon_party import expire_stale_dungeon_parties

        expired_dungeons = expire_stale_dungeon_parties(session, now)
        if expired or expired_dungeons:
            session.commit()
            if expired:
                logger.info("Expired %s stale duel challenge(s) on startup.", expired)
            if expired_dungeons:
                logger.info("Cancelled %s stale dungeon expedition(s) on startup.", expired_dungeons)
    finally:
        session.close()
    try:
        command_count = len(bot.tree.get_commands())
        if cfg.guild_id:
            try:
                guild = discord.Object(id=int(cfg.guild_id))
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync()
                print(
                    f"Slash commands synced to guild ({len(synced)} commands, "
                    f"{command_count} in tree); cleared stale global commands."
                )
            except discord.Forbidden as e:
                print(f"Guild sync forbidden ({e}). Falling back to global sync (may take up to ~1h).")
                synced = await bot.tree.sync()
                print(f"Slash commands synced globally ({len(synced)} commands).")
        else:
            synced = await bot.tree.sync()
            print(f"Slash commands synced globally ({len(synced)} commands, {command_count} in tree).")
    except Exception as e:
        print(f"Slash command sync failed: {e!r}")


async def sync_commands() -> None:
    cfg = get_config()
    if cfg.guild_id:
        guild = discord.Object(id=int(cfg.guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
    else:
        await bot.tree.sync()


async def main():
    cfg = get_config()

    from .schemas import run_validation
    run_validation(raise_on_error=True)

    init_db()

    print("Bot ready. Starting event loop...")
    await bot.start(cfg.discord_token)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
