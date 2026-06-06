from __future__ import annotations

import logging
from typing import Awaitable, Callable

import discord
from sqlalchemy.orm import Session

from .config import get_config
from .discord_guild import create_abode_channel
from .models import Player
from .story_mode import (
    PendingStoryCreation,
    get_node,
    get_path_def,
    node_choices,
    node_has_input,
    player_story_chapter,
    player_story_step,
    render_node_text,
    resolve_next_node,
)
from .story_views import StoryView, build_story_embed

logger = logging.getLogger(__name__)


async def resolve_abode_channel(
    guild: discord.Guild,
    channel_id: str | None,
) -> discord.TextChannel | None:
    if not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await guild.fetch_channel(int(channel_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
    return fetched if isinstance(fetched, discord.TextChannel) else None


def should_redirect_start_to_public_channel(
    *,
    interaction_channel_id: str | None,
    abode_channel_id: str | None,
) -> bool:
    """Only replace the /start reply when the player is not already in their abode."""
    if not abode_channel_id:
        return True
    if not interaction_channel_id:
        return True
    return str(interaction_channel_id) != str(abode_channel_id)


def _player_story_view(
    player: Player,
    *,
    on_player_update: Callable[[discord.Interaction, int, str], Awaitable[None]] | None,
) -> discord.ui.View | None:
    chapter = player_story_chapter(player)
    step = player_story_step(player)
    node = get_node(chapter, step)
    if node is None:
        return None
    if node_choices(node) or (
        resolve_next_node(node)
        and step not in ("complete", "chapter1_complete")
        and not node.get("await_command")
        and not node_has_input(node)
    ):
        path_name = ""
        if player.story_path:
            path_def = get_path_def(player.story_path)
            if path_def:
                path_name = str(path_def.get("name", ""))
        return StoryView(
            player.discord_id,
            player.guild_id,
            chapter=chapter,
            node_id=step,
            player_id=player.id,
            dao_name=player.dao_name,
            spirit_root=player.spirit_root,
            origin=player.origin,
            path_name=path_name,
            on_player_update=on_player_update,
        )
    return None


def build_player_story_embed(player: Player, *, extra_text: str = "") -> discord.Embed | None:
    chapter = player_story_chapter(player)
    step = player_story_step(player)
    node = get_node(chapter, step)
    if node is None:
        return None

    path_name = ""
    if player.story_path:
        path_def = get_path_def(player.story_path)
        if path_def:
            path_name = str(path_def.get("name", ""))

    node_display = dict(node)
    text = render_node_text(
        node,
        dao_name=player.dao_name,
        spirit_root=player.spirit_root,
        origin=player.origin,
        path_name=path_name,
    )
    if extra_text:
        text = f"{text}{extra_text}"
    node_display["text"] = text

    return build_story_embed(
        node_display,
        chapter=chapter,
        dao_name=player.dao_name,
        spirit_root=player.spirit_root,
        origin=player.origin,
        path_name=path_name,
    )


async def sync_player_story_to_abode(
    client: discord.Client,
    guild: discord.Guild | None,
    session: Session,
    player: Player,
    *,
    on_player_update: Callable[[discord.Interaction, int, str], Awaitable[None]] | None = None,
    extra_text: str = "",
    repost: bool = False,
) -> discord.TextChannel | None:
    """Post or update the Elder's story embed in the player's abode."""
    if guild is None or not player.abode_channel_id:
        return None

    channel = await resolve_abode_channel(guild, player.abode_channel_id)
    if channel is None:
        return None

    embed = build_player_story_embed(player, extra_text=extra_text)
    if embed is None:
        return channel

    view = _player_story_view(player, on_player_update=on_player_update)

    if repost and player.story_message_id:
        try:
            old_msg = await channel.fetch_message(int(player.story_message_id))
            await old_msg.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logger.exception(
                "Failed to delete old story message player=%s message=%s",
                player.id,
                player.story_message_id,
            )
        player.story_message_id = None
        session.add(player)

    message: discord.Message | None = None
    if player.story_message_id:
        try:
            message = await channel.fetch_message(int(player.story_message_id))
            await message.edit(content=None, embed=embed, view=view)
            return channel
        except discord.NotFound:
            message = None
        except discord.HTTPException:
            logger.exception(
                "Failed to edit story message player=%s message=%s",
                player.id,
                player.story_message_id,
            )

    try:
        message = await channel.send(embed=embed, view=view)
        player.story_message_id = str(message.id)
        session.add(player)
    except discord.HTTPException:
        logger.exception("Failed to post story to abode player=%s channel=%s", player.id, channel.id)
    return channel


async def ensure_pending_abode(
    interaction: discord.Interaction,
    pending: PendingStoryCreation,
) -> discord.TextChannel | None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return None
    if not pending.dao_name:
        return None

    existing = await resolve_abode_channel(interaction.guild, pending.abode_channel_id or None)
    if existing is not None:
        return existing

    cfg = get_config()
    channel, _err = await create_abode_channel(
        interaction.guild,
        interaction.user,
        pending.dao_name,
        category_id=cfg.abode_category_id,
    )
    if channel is None:
        return None
    pending.abode_channel_id = str(channel.id)
    return channel


def _pending_story_view(
    pending: PendingStoryCreation,
    *,
    on_pending_update,
    on_finalize,
) -> StoryView:
    path_name = ""
    if pending.story_path:
        path_def = get_path_def(pending.story_path)
        if path_def:
            path_name = str(path_def.get("name", ""))
    return StoryView(
        pending.discord_id,
        pending.guild_id,
        chapter=1,
        node_id=pending.node_id,
        pending=pending,
        dao_name=pending.dao_name,
        path_name=path_name,
        on_pending_update=on_pending_update,
        on_finalize=on_finalize,
    )


async def deliver_pending_story_to_abode(
    interaction: discord.Interaction,
    pending: PendingStoryCreation,
    session: Session | None,
    *,
    on_pending_update,
    on_finalize,
    redirect_start: bool = True,
) -> bool:
    """Deliver creation-story beats to the abode once a dao name exists."""
    if interaction.guild is None:
        return False

    channel = await ensure_pending_abode(interaction, pending)
    if channel is None:
        return False

    node = get_node(1, pending.node_id)
    if node is None:
        return False

    path_name = ""
    if pending.story_path:
        path_def = get_path_def(pending.story_path)
        if path_def:
            path_name = str(path_def.get("name", ""))

    embed = build_story_embed(
        node,
        chapter=1,
        dao_name=pending.dao_name,
        path_name=path_name,
    )
    view = _pending_story_view(
        pending,
        on_pending_update=on_pending_update,
        on_finalize=on_finalize,
    )

    message: discord.Message | None = None
    if pending.story_message_id:
        try:
            message = await channel.fetch_message(int(pending.story_message_id))
            await message.edit(content=None, embed=embed, view=view)
        except discord.NotFound:
            message = None

    if message is None:
        message = await channel.send(embed=embed, view=view)
        pending.story_message_id = str(message.id)

    if redirect_start and should_redirect_start_to_public_channel(
        interaction_channel_id=str(interaction.channel_id or ""),
        abode_channel_id=str(channel.id),
    ):
        redirect = (
            f"**Elder Yunjian** awaits in {channel.mention}. "
            "Continue your awakening there."
        )
        if not interaction.response.is_done():
            await interaction.response.edit_message(content=redirect, embed=None, view=None)
        else:
            try:
                await interaction.edit_original_response(content=redirect, embed=None, view=None)
            except discord.HTTPException:
                pass
    elif not interaction.response.is_done():
        await interaction.response.defer()

    return True


async def maybe_sync_elder_story(
    interaction: discord.Interaction,
    session: Session,
    player: Player,
    *,
    on_player_update: Callable[[discord.Interaction, int, str], Awaitable[None]] | None = None,
    repost: bool | None = None,
) -> None:
    if interaction.guild is None:
        return
    if on_player_update is None:
        from . import bot as bot_module

        on_player_update = bot_module._story_continue_after_creation
    if repost is None:
        from .story_mode import elder_trial_active

        repost = elder_trial_active(player)
    await sync_player_story_to_abode(
        interaction.client,
        interaction.guild,
        session,
        player,
        on_player_update=on_player_update,
        repost=repost,
    )


async def send_elder_followup(
    interaction: discord.Interaction,
    session: Session,
    player: Player,
    *,
    on_player_update: Callable[[discord.Interaction, int, str], Awaitable[None]] | None = None,
) -> None:
    """Send the Elder's current story node to the interaction channel as a follow-up."""
    if on_player_update is None:
        from . import bot as bot_module

        on_player_update = bot_module._story_continue_after_creation

    # Refresh the player from DB in case story_step was updated by another session.
    session.refresh(player)

    embed = build_player_story_embed(player)
    if embed is None:
        return

    view = _player_story_view(player, on_player_update=on_player_update)
    try:
        kwargs: dict = {"embed": embed}
        if view is not None:
            kwargs["view"] = view
        await interaction.followup.send(**kwargs)
    except discord.HTTPException:
        logger.debug(
            "Failed to send elder follow-up player=%s channel=%s",
            player.id,
            interaction.channel_id,
        )
