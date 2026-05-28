from __future__ import annotations

import logging
import re

import discord

from .cooperative_dungeons import get_cooperative_dungeon
from .dungeon_combat import DungeonCombatState, DungeonFighter
from .dungeon_party import load_members
from .models import ActiveDungeonParty
from .ui.formatting import format_hp_block, format_status_badges

logger = logging.getLogger(__name__)

DUNGEON_CHANNEL_PREFIX = "dungeon-"
DUNGEON_CATEGORY_NAMES = ("dungeons", "realm dungeons", "cooperative dungeons")


async def resolve_dungeon_category(
    guild: discord.Guild,
    category_id: str | None,
) -> discord.CategoryChannel | None:
    if category_id:
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
        logger.warning(
            "DUNGEON_CATEGORY_ID=%s is not a category in guild=%s",
            category_id,
            guild.id,
        )

    for category in guild.categories:
        if category.name.lower() in DUNGEON_CATEGORY_NAMES:
            return category

    try:
        return await guild.create_category("Dungeons", reason="Cooperative dungeon expeditions")
    except discord.Forbidden:
        logger.warning(
            "Cannot create Dungeons category in guild=%s (missing Manage Channels)",
            guild.id,
        )
        return None
    except discord.HTTPException:
        logger.exception("Failed to create Dungeons category in guild=%s", guild.id)
        return None


def dungeon_channel_slug(dungeon_name: str, leader_name: str, *, max_len: int = 90) -> str:
    def slug(text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text or "expedition"

    body = f"{slug(dungeon_name)}-{slug(leader_name)}"[:max_len]
    return DUNGEON_CHANNEL_PREFIX + body


def _party_overwrites(
    guild: discord.Guild,
    member_ids: list[int],
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    for mid in member_ids:
        member = guild.get_member(mid)
        if member is not None:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                use_application_commands=True,
            )
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
        )
    return overwrites


def format_fighter_line(fighter: DungeonFighter) -> str:
    hp = format_hp_block(
        fighter.name,
        fighter.combatant.hp,
        fighter.combatant.max_hp,
        icon="👹" if fighter.is_enemy else "❤️",
        include_header=False,
    )
    badges = format_status_badges(fighter.combatant.statuses)
    badge_text = f" {badges}" if badges else ""
    side = "👹" if fighter.is_enemy else "🧙"
    return f"{side} **{fighter.name}** — {hp}{badge_text}"


def build_dungeon_combat_embed(
    party: ActiveDungeonParty,
    state: DungeonCombatState,
) -> discord.Embed:
    dungeon = get_cooperative_dungeon(state.dungeon_id)
    title = dungeon.name if dungeon else state.dungeon_id
    actor = state.current_actor()
    actor_line = f"**{actor.name}**'s turn" if actor else "—"
    if state.pending_technique and actor and not actor.is_enemy:
        actor_line += " — **choose a target**"

    room_num = state.room_index + 1
    total_rooms = len(dungeon.rooms) if dungeon else 4
    embed = discord.Embed(
        title=f"⚔️ {title} — Room {room_num}/{total_rooms}",
        description=f"**{state.room_label}** · Round **{state.round_num}**\n{actor_line}",
        color=discord.Color.dark_red(),
    )

    allies = [f for f in state.fighters.values() if not f.is_enemy]
    foes = [f for f in state.fighters.values() if f.is_enemy]
    if allies:
        embed.add_field(name="Party", value="\n".join(format_fighter_line(f) for f in allies), inline=True)
    if foes:
        embed.add_field(name="Foes", value="\n".join(format_fighter_line(f) for f in foes), inline=True)

    if state.finished:
        if state.victory and state.room_cleared:
            footer = "Room cleared — advancing…" if not state.run_complete else "Dungeon complete."
        else:
            footer = "The party has fallen or the assault stalled."
        embed.add_field(name="Battle log", value=footer, inline=False)
    else:
        embed.add_field(
            name="Battle log",
            value="Turn-by-turn log is posted in this channel below.",
            inline=False,
        )
    return embed


def build_dungeon_cancelled_embed(*, reason: str) -> discord.Embed:
    return discord.Embed(
        title="Expedition Cancelled",
        description=reason,
        color=discord.Color.dark_grey(),
    )


def format_new_log_lines(state: DungeonCombatState) -> str | None:
    """Lines not yet posted to the dungeon channel."""
    if state.log_cursor >= len(state.log):
        return None
    return "\n".join(state.log[state.log_cursor :])


def build_lobby_embed(party: ActiveDungeonParty) -> discord.Embed:
    from .dungeon_party import format_lobby_description

    dungeon = get_cooperative_dungeon(party.dungeon_id)
    title = dungeon.name if dungeon else "Dungeon Expedition"
    return discord.Embed(
        title=f"🏛️ {title}",
        description=format_lobby_description(party),
        color=discord.Color.blurple(),
    )
