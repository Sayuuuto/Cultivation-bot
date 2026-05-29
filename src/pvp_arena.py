from __future__ import annotations

import asyncio
import logging
import re

import discord

from .combat.discord_ui import build_combat_embed
from .combat.engine import CombatState
from .pvp_combat import PvpCombatState, build_match_summary, combat_slice_for_actor
from .pvp_match import FinalizedPvpMatch
from .realms import REALMS, SUBSTAGES
from .ui.formatting import format_hp_block, format_status_badges

logger = logging.getLogger(__name__)

ARENA_PREFIX = "arena-"
ARENA_CATEGORY_NAMES = ("arenas", "duel arenas", "pvp arenas")
ARENA_DELETE_DELAY_SECONDS = 45


def _realm_display(realm_index: int, substage: int) -> str:
    realm = REALMS[min(max(realm_index, 0), len(REALMS) - 1)]
    stage = SUBSTAGES[min(max(substage, 0), len(SUBSTAGES) - 1)]
    return f"{realm} ({stage})"


def arena_channel_slug(name_a: str, name_b: str, *, max_len: int = 100) -> str:
    def slug(name: str) -> str:
        text = name.strip().lower()
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        return text or "daoist"

    combined = f"{slug(name_a)}-vs-{slug(name_b)}"
    prefix = ARENA_PREFIX
    max_body = max(0, max_len - len(prefix))
    return prefix + combined[:max_body]


def _arena_overwrites(
    guild: discord.Guild,
    member_a: discord.Member,
    member_b: discord.Member,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member_a: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        ),
        member_b: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        ),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
        )
    return overwrites


async def resolve_arena_category(
    guild: discord.Guild,
    category_id: str | None,
) -> discord.CategoryChannel | None:
    if category_id:
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
        logger.warning(
            "ARENA_CATEGORY_ID=%s is not a category in guild=%s",
            category_id,
            guild.id,
        )

    for category in guild.categories:
        if category.name.lower() in ARENA_CATEGORY_NAMES:
            return category

    try:
        return await guild.create_category("Arenas", reason="Temporary PvP duel arenas")
    except discord.Forbidden:
        logger.warning("Cannot create Arenas category in guild=%s", guild.id)
        return None
    except discord.HTTPException:
        logger.exception("Failed to create Arenas category in guild=%s", guild.id)
        return None


def _unique_arena_name(guild: discord.Guild, base_name: str) -> str:
    if discord.utils.get(guild.text_channels, name=base_name) is None:
        return base_name
    stem = base_name[:90].rstrip("-")
    suffix = 2
    while True:
        candidate = f"{stem}-{suffix}"
        if discord.utils.get(guild.text_channels, name=candidate) is None:
            return candidate[:100]
        suffix += 1


async def create_arena_channel(
    guild: discord.Guild,
    member_a: discord.Member,
    member_b: discord.Member,
    dao_a: str,
    dao_b: str,
    *,
    category_id: str | None = None,
) -> tuple[discord.TextChannel | None, str | None]:
    channel_name = _unique_arena_name(guild, arena_channel_slug(dao_a, dao_b))
    category = await resolve_arena_category(guild, category_id)
    overwrites = _arena_overwrites(guild, member_a, member_b)
    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"PvP arena: {dao_a} vs {dao_b}",
            topic=f"Duel arena — {dao_a} vs {dao_b}. Techniques, flee, and finish — same rules as the hunt.",
        )
        return channel, None
    except discord.Forbidden:
        return None, (
            "The arena could not be opened — the bot needs **Manage Channels** "
            "to create private duel rooms."
        )
    except discord.HTTPException as exc:
        logger.exception("Arena channel creation failed guild=%s", guild.id)
        return None, f"The arena could not be opened: {exc.text}"


def _duel_slice_for_actor(state: PvpCombatState) -> CombatState:
    return combat_slice_for_actor(state)


def build_pvp_combat_embed(state: PvpCombatState) -> discord.Embed:
    actor = state.actor()
    defender = state.opponent_of(state.current_actor_id)

    if state.finished:
        title = "Duel Complete"
        color = discord.Color.green()
        description = state.log[-1] if state.log else "The duel has ended."
        embed = discord.Embed(title=f"⚔️ {title}", description=description, color=color)
        for fighter in state.fighters.values():
            embed.add_field(
                name=fighter.dao_name,
                value=(
                    f"{format_hp_block(fighter.dao_name, fighter.combatant.hp, fighter.combatant.max_hp, icon='❤️', bar_fill='🟩', include_header=False)}\n"
                    f"Status: {format_status_badges(fighter.combatant.statuses)}"
                ),
                inline=True,
            )
        if state.log:
            from .ui.formatting import format_combat_log_lines

            embed.add_field(name="Combat log", value=format_combat_log_lines(state.log, limit=8), inline=False)
        return embed

    cs = _duel_slice_for_actor(state)
    footer = f"Turn **{state.turn}** · **{actor.dao_name}** acts next"
    embed = build_combat_embed(
        f"Duel — {actor.dao_name} vs {defender.dao_name}",
        cs,
        footer=f"✨ Techniques · ⏭ Pass Turn · 🏃 Yield · {footer}",
    )
    embed.set_field_at(
        0,
        name=f"❤️ {actor.dao_name}",
        value=(
            f"{format_hp_block(actor.dao_name, cs.player.hp, cs.player.max_hp, icon='❤️', bar_fill='🟩', include_header=False)}\n"
            f"Status: {format_status_badges(cs.player.statuses)}\n"
            f"Turn **{state.turn}** · **{actor.dao_name}** to act"
        ),
        inline=True,
    )
    embed.set_field_at(
        1,
        name=f"⚔️ {defender.dao_name}",
        value=(
            f"{format_hp_block(defender.dao_name, cs.opponent.hp, cs.opponent.max_hp, icon='⚔️', bar_fill='🟥', include_header=False)}\n"
            f"Status: {format_status_badges(cs.opponent.statuses)}"
        ),
        inline=True,
    )
    return embed


def build_pvp_results_embed(finalized: FinalizedPvpMatch) -> discord.Embed:
    state = finalized.state
    winner = finalized.winner
    loser = finalized.loser
    title = "Arena Result"
    if state.surrendered:
        desc = f"**{winner.dao_name}** wins — **{loser.dao_name}** yielded the duel."
    else:
        desc = f"**{winner.dao_name}** defeats **{loser.dao_name}** in the arena."
    embed = discord.Embed(title=title, description=desc, color=discord.Color.gold())
    embed.add_field(
        name="Winner",
        value=(
            f"**{winner.dao_name}** · {_realm_display(winner.realm_index, winner.substage)}\n"
            f"+**{finalized.stones_gain}** spirit stones (now **{winner.spirit_stones}**)\n"
            f"Record **{winner.pvp_wins}W** / **{winner.pvp_losses}L**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Defeated",
        value=(
            f"**{loser.dao_name}** · {_realm_display(loser.realm_index, loser.substage)}\n"
            f"Record **{loser.pvp_wins}W** / **{loser.pvp_losses}L**"
        ),
        inline=False,
    )
    embed.add_field(name="Fight summary", value=build_match_summary(state), inline=False)
    return embed


async def post_pvp_results(
    client: discord.Client,
    guild_id: str,
    results_channel_id: str | None,
    embed: discord.Embed,
) -> None:
    if not results_channel_id:
        return
    channel = client.get_channel(int(results_channel_id))
    if channel is None:
        try:
            channel = await client.fetch_channel(int(results_channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.warning("PvP results channel unavailable id=%s", results_channel_id)
            return
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.exception("Failed to post PvP results guild=%s channel=%s", guild_id, results_channel_id)


async def schedule_arena_cleanup(
    channel: discord.TextChannel,
    *,
    delay_seconds: int = ARENA_DELETE_DELAY_SECONDS,
) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await channel.delete(reason="PvP duel complete — arena closed")
    except discord.NotFound:
        return
    except discord.HTTPException:
        logger.exception("Failed to delete arena channel id=%s", channel.id)
