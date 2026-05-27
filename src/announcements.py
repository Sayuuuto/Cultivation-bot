from __future__ import annotations

import logging

import discord

from .config import Config

logger = logging.getLogger(__name__)


async def post_announcement(
    bot: discord.Client,
    cfg: Config,
    *,
    guild_id: str | None,
    message: str,
) -> bool:
    """Post a milestone message to ANNOUNCE_CHANNEL_ID when configured."""
    channel_id_raw = cfg.announce_channel_id
    if not channel_id_raw:
        return False
    try:
        channel_id = int(channel_id_raw)
    except ValueError:
        logger.warning("Invalid ANNOUNCE_CHANNEL_ID: %s", channel_id_raw)
        return False

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            logger.warning("Could not fetch announce channel %s", channel_id)
            return False

    if not isinstance(channel, discord.abc.Messageable):
        return False

    if guild_id is not None and isinstance(channel, discord.abc.GuildChannel):
        if str(channel.guild.id) != str(guild_id):
            return False

    try:
        await channel.send(message)
        return True
    except discord.HTTPException:
        logger.exception("Failed to post announcement to channel %s", channel_id)
        return False
