from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..helpers import get_guild_id, interaction_ctx
from ...config import get_config
from ...post_library import post_library
from ...post_tutorial import post_tutorial

logger = logging.getLogger("cultivation_bot")


class AdminCog(commands.Cog):
    """Admin commands: post-tutorial, post-library."""

    @app_commands.command(name="post-tutorial", description="Post the full game tutorial to a channel (admin).")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Where to post (defaults to TUTORIAL_CHANNEL_ID in .env)",
        pin_intro="Pin the intro message at the top",
    )
    async def post_tutorial_cmd(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        pin_intro: bool = True,
    ):
        cfg = get_config()
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=False)
            return

        target = channel
        if target is None and cfg.tutorial_channel_id:
            target = interaction.guild.get_channel(int(cfg.tutorial_channel_id))
            if target is None:
                try:
                    target = await interaction.client.fetch_channel(int(cfg.tutorial_channel_id))
                except (discord.NotFound, discord.Forbidden, ValueError):
                    target = None

        if target is None:
            await interaction.response.send_message(
                "Pick a **channel**, or set `TUTORIAL_CHANNEL_ID` in the bot `.env`.",
                ephemeral=False,
            )
            return

        await interaction.response.defer(ephemeral=False)
        try:
            result = await post_tutorial(
                target,
                pin_intro=pin_intro,
                clear_existing=True,
                me=interaction.client.user,
            )
            await interaction.followup.send(
                f"Cleared **{result.deleted}** old message(s) and posted **{result.posted}** new message(s) "
                f"to {target.mention}.",
                ephemeral=False,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't manage messages in {target.mention}. Check **Read Message History**, "
                "**Send Messages**, **Embed Links**, and **Pin Messages**.",
                ephemeral=False,
            )
        except Exception as exc:
            logger.exception("post-tutorial failed %s", interaction_ctx(interaction))
            await interaction.followup.send(f"Failed to post tutorial: {exc}", ephemeral=False)

    @app_commands.command(name="post-library", description="Post the technique manual library to a channel (admin).")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Where to post (defaults to LIBRARY_CHANNEL_ID in .env)",
        pin_intro="Pin the intro message at the top",
    )
    async def post_library_cmd(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        pin_intro: bool = True,
    ):
        cfg = get_config()
        if interaction.guild is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=False)
            return

        target = channel
        if target is None and cfg.library_channel_id:
            target = interaction.guild.get_channel(int(cfg.library_channel_id))
            if target is None:
                try:
                    target = await interaction.client.fetch_channel(int(cfg.library_channel_id))
                except (discord.NotFound, discord.Forbidden, ValueError):
                    target = None

        if target is None:
            await interaction.response.send_message(
                "Pick a **channel**, or set `LIBRARY_CHANNEL_ID` in the bot `.env`.",
                ephemeral=False,
            )
            return

        await interaction.response.defer(ephemeral=False)
        try:
            result = await post_library(
                target,
                pin_intro=pin_intro,
                clear_existing=True,
                me=interaction.client.user,
            )
            await interaction.followup.send(
                f"Cleared **{result.deleted}** old message(s) and posted **{result.posted}** new message(s) "
                f"to {target.mention}.",
                ephemeral=False,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't manage messages in {target.mention}. Check **Read Message History**, "
                "**Send Messages**, **Embed Links**, and **Pin Messages**.",
                ephemeral=False,
            )
        except Exception as exc:
            logger.exception("post-library failed %s", interaction_ctx(interaction))
            await interaction.followup.send(f"Failed to post library: {exc}", ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog())
