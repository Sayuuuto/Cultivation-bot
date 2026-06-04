from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ...config import get_config
from ...db import get_session
from ...game import utcnow
from ...game_sects import (
    buy_from_sect_shop,
    ensure_daily_sect_task,
    format_player_sect_status,
    format_sect_list_entry,
    format_sect_shop_listing,
    format_sect_task_status,
    join_game_sect,
    leave_game_sect,
    load_game_sects,
)
from ...inventory import get_item_name
from ..helpers import (
    NOT_STARTED_HINT,
    attach_guidance,
    ensure_player,
    get_discord_id,
    get_guild_id,
)


async def game_sect_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current = (current or "").lower()
    choices: list[app_commands.Choice[str]] = []
    for sect_id, sect in load_game_sects().items():
        if current and current not in sect_id and current not in sect.name.lower():
            continue
        choices.append(app_commands.Choice(name=sect.name, value=sect_id))
    return choices[:25]


async def sect_shop_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        from ...game_sects import list_sect_shop_entries

        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []

        _shop, entries = list_sect_shop_entries(player)
        lower = (current or "").lower()
        choices: list[app_commands.Choice[str]] = []
        for entry in entries:
            name = get_item_name(entry.item_id)
            if lower and lower not in name.lower() and lower not in entry.item_id:
                continue
            choices.append(app_commands.Choice(name=f"{name} ({entry.merit_cost} merit)", value=entry.item_id))
        return choices[:25]
    finally:
        session.close()


class SectCog(commands.Cog):
    """Sect commands — join, leave, browse, and purchase from martial sects."""

    @app_commands.command(name="sect-list", description="View martial sects you may join.")
    async def sect_list_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            entries = [
                format_sect_list_entry(session, player, sect)
                for sect in load_game_sects().values()
            ]
            embed = discord.Embed(
                title="Martial Sects of the Realm",
                description=(
                    "Clans (`/clan`) are player guilds. **Sects** are fixed orders — "
                    "join with **`/sect-join`** when you meet their requirements.\n\n"
                    + "\n\n".join(entries)
                ),
                color=discord.Color.dark_teal(),
            )
            attach_guidance(embed, "sect-list", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="sect", description="View your martial sect membership.")
    async def sect_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            embed = discord.Embed(
                title="Your Martial Sect",
                description=format_player_sect_status(player),
                color=discord.Color.dark_teal(),
            )
            attach_guidance(embed, "sect", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="sect-join", description="Join a martial sect (Wudang, Shaolin, Tang, etc.).")
    @app_commands.describe(sect="Sect id from /sect-list (e.g. wudang, shaolin)")
    @app_commands.autocomplete(sect=game_sect_autocomplete)
    async def game_sect_join_cmd(self, interaction: discord.Interaction, sect: str):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            sect_id = sect.strip().lower()
            ok, msg = join_game_sect(session, player, sect_id)
            if not ok:
                await interaction.response.send_message(msg, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(
                title="Accepted as a Disciple",
                description=msg,
                color=discord.Color.green(),
            )
            attach_guidance(embed, "sect-join", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="sect-leave", description="Leave your martial sect.")
    async def game_sect_leave_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            ok, msg, _ = leave_game_sect(session, player)
            if not ok:
                await interaction.response.send_message(msg, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(
                title="You Leave the Martial Sect",
                description=msg,
                color=discord.Color.orange(),
            )
            attach_guidance(embed, "sect-leave", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="sect-task", description="View your daily martial sect assignment and progress.")
    async def sect_task_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.game_sect_id is None:
                await interaction.response.send_message(
                    "You belong to no martial sect. Use **`/sect-list`** and **`/sect-join`**.",
                    ephemeral=False,
                )
                return

            ensure_daily_sect_task(session, player)
            session.commit()

            embed = discord.Embed(
                title="Daily Sect Task",
                description=format_sect_task_status(player),
                color=discord.Color.dark_teal(),
            )
            embed.set_footer(text="Merit also trickles in from /cultivate, /gather, /hunt, /adventure, /dungeon.")
            attach_guidance(embed, "sect-task", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="sect-shop", description="Browse your martial sect's merit shop (Common–Uncommon manuals).")
    async def sect_shop_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.game_sect_id is None:
                await interaction.response.send_message(
                    "Join a martial sect first (`/sect-join`).", ephemeral=False
                )
                return

            embed = discord.Embed(
                title="Sect Merit Shop",
                description=format_sect_shop_listing(player),
                color=discord.Color.gold(),
            )
            attach_guidance(embed, "sect-shop", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="sect-buy", description="Buy a manual from your sect shop with merit.")
    @app_commands.describe(item="Manual from /sect-shop (autocomplete).")
    @app_commands.autocomplete(item=sect_shop_item_autocomplete)
    async def sect_buy_cmd(self, interaction: discord.Interaction, item: str):
        session = get_session()
        try:
            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            ok, msg = buy_from_sect_shop(session, player, item.strip())
            if not ok:
                await interaction.response.send_message(msg, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(
                title="Sect Purchase",
                description=msg + f"\nRemaining merit: **{player.sect_merit}**.",
                color=discord.Color.green(),
            )
            attach_guidance(embed, "sect-buy", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SectCog())
