"""Shared helpers for Discord UI layer.

Extracted from bot.py to keep Discord handlers thin and testable.
All domain logic remains in the respective src/*.py modules.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone

import discord
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db import get_session
from ..game import (
    REALMS,
    SPIRIT_ROOTS,
    SUBSTAGES,
    ORIGINS,
    BreakthroughPreview,
    BreakthroughResult,
    CultivateResult,
    breakthrough,
    collect_passive_qi,
    compute_breakthrough_preview,
    cultivate,
    utcnow,
)
from ..models import ActiveAdventure, ActivePvpMatch, Clan, PendingDuel, Player
from ..adventure import (
    AdventureResult,
    PendingAdventure,
    abandon_adventure,
    apply_adventure_choice,
    apply_adventure_combat_outcome,
    get_active_adventure,
)
from ..character import get_character_modifiers
from ..cooldown_haste import (
    consume_haste_for_activity,
    cooldown_remaining_with_haste,
    get_haste_reduction_seconds,
)
from ..combat_stats import compute_combat_stats
from ..combat.discord_ui import build_adventure_combat_embed, build_combat_embed, build_hunt_combat_embed
from ..combat.loadout import ensure_starter_techniques, equip_technique, get_equipped_active_techniques
from ..combat.session import COMBAT_BUSY_MESSAGE, abandon_active_combat, process_combat_action
from ..announcements import post_announcement
from ..cultivate_events import apply_cultivate_bonus_drops
from ..cultivation_preview import build_breakthrough_preview_embed, format_breakthrough_chance_line
from ..effects import consume_clarity_for_breakthrough, consume_effect_charge, format_active_effects_block
from ..forge import forge_equipment_for_player
from ..foundation import grant_body_temper_charges
from ..guidance import add_guidance_to_embed
from ..manuals import roll_breakthrough_enlightenment
from ..hunt import finalize_hunt_combat, start_hunt_combat
from ..novice_trial import (
    apply_origin_starter_gifts,
    on_adventure_completed,
    on_breakthrough_success,
    on_daily_claimed,
)
from ..player_wipe import wipe_player_character
from ..pvp_arena import (
    build_pvp_combat_embed,
    build_pvp_results_embed,
    create_arena_channel,
    post_pvp_results,
    schedule_arena_cleanup,
)
from ..pvp_combat import PvpCombatState, combat_slice_for_actor, process_pvp_action
from ..pvp_match import finalize_pvp_match, load_pvp_match_state, save_pvp_match_state
from ..reminders import (
    ACTIVITY_LABELS,
    REMINDER_ACTIVITIES,
    build_reminder_status_text,
    reminder_dm_content,
    fetch_due_reminders,
    mark_reminder_sent,
    schedule_after_activity,
    set_all_reminders_enabled,
    set_reminder_enabled,
)
from ..story_delivery import (
    deliver_pending_story_to_abode,
    maybe_sync_elder_story,
    resolve_abode_channel,
    send_elder_followup,
    sync_player_story_to_abode,
)
from ..story_mode import (
    apply_path_bonuses,
    clear_pending,
    finalize_creation_state,
    get_node,
    get_path_def,
    get_pending,
    player_story_chapter,
    render_node_text,
    start_pending,
    story_command_available,
    unlock_chapter_two,
)
from ..story_views import (
    StoryView,
    build_story_embed,
    send_story_node_for_pending,
    send_story_node_for_player,
)
from ..ui.embeds import (
    build_adventure_embed_from_pending,
    build_adventure_embed_from_result,
    build_cultivate_embed,
    build_hunt_result_embed,
)
from ..discord_guild import (
    delete_abode_channel,
    provision_new_cultivator,
    strip_realm_roles,
    sync_member_realm_role,
)

logger = logging.getLogger("cultivation_bot")


def interaction_ctx(interaction: discord.Interaction) -> str:
    guild_id = str(interaction.guild.id) if interaction.guild is not None else "DM"
    user_id = str(interaction.user.id) if interaction.user is not None else "unknown"
    cmd = getattr(interaction, "command", None)
    cmd_name = getattr(cmd, "name", "unknown") if cmd is not None else "unknown"
    return f"guild={guild_id} user={user_id} cmd={cmd_name} interaction_id={interaction.id}"


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def realm_display(realm_index: int, substage: int) -> str:
    realm = REALMS[min(max(realm_index, 0), len(REALMS) - 1)]
    stage = SUBSTAGES[min(max(substage, 0), len(SUBSTAGES) - 1)]
    return f"{realm} ({stage})"


def time_left(now: datetime, past: datetime | None) -> timedelta:
    if past is None:
        return timedelta(0)
    return max(timedelta(0), now - past)


def format_seconds(seconds: int) -> str:
    seconds = max(0, seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


NOT_STARTED_HINT = "Use **`/start`** to begin your path, or **`/help`** for a full guide."


def cooldown_remaining(now: datetime, last: datetime | None, cooldown_seconds: int) -> int:
    if last is None:
        return 0
    now = to_utc(now)
    last = to_utc(last)
    elapsed = now - last
    cd = timedelta(seconds=cooldown_seconds)
    rem = cd - elapsed
    return max(0, int(rem.total_seconds()))


def activity_cooldown_remaining(
    session: Session,
    player: Player,
    now: datetime,
    activity: str,
    last: datetime | None,
    cooldown_seconds: int,
) -> int:
    base = cooldown_remaining(now, last, cooldown_seconds)
    haste = get_haste_reduction_seconds(session, player.id, activity)
    return cooldown_remaining_with_haste(base, haste)


def schedule_player_reminders(
    session: Session,
    player: Player,
    cfg,
    *activities: str,
    now: datetime | None = None,
) -> None:
    when = now or utcnow()
    for activity in activities:
        schedule_after_activity(session, player, cfg, activity, when)


def attach_guidance(
    embed: discord.Embed,
    command: str,
    player: Player | None,
    session: Session | None,
    cfg,
    now: datetime | None = None,
) -> discord.Embed:
    def remaining_fn(now_dt: datetime, last: datetime | None, cooldown_seconds: int) -> int:
        return cooldown_remaining(now_dt, last, cooldown_seconds)
    add_guidance_to_embed(embed, command, player, session, cfg, now or utcnow(), remaining_fn)
    return embed


def discord_object(maybe_id: str | None) -> discord.Object | None:
    if not maybe_id:
        return None
    try:
        return discord.Object(id=int(maybe_id))
    except ValueError:
        return None


def get_guild_id(interaction: discord.Interaction) -> str:
    assert interaction.guild is not None
    return str(interaction.guild.id)


def get_discord_id(user: discord.abc.User) -> str:
    return str(user.id)


def ensure_player(session: Session, guild_id: str, discord_id: str) -> Player | None:
    stmt = select(Player).where(Player.guild_id == guild_id, Player.discord_id == discord_id)
    return session.execute(stmt).scalar_one_or_none()


def get_clan_by_name_lookup(session: Session, guild_id: str, name: str) -> Clan | None:
    from ..clans import get_clan_by_name
    return get_clan_by_name(session, guild_id, name)


def ensure_loaded_player(player: Player) -> None:
    pass


def _apply_adventure_completion(
    session: Session,
    player: Player,
    result: AdventureResult,
    now: datetime,
) -> tuple[list[str], str | None]:
    trial_msgs, waive_cd, story_next = on_adventure_completed(
        session, player, segments_cleared=result.segments_cleared
    )
    from ..story_mode import should_apply_trial_activity_cooldown
    if not waive_cd and should_apply_trial_activity_cooldown(player, "adventure"):
        player.last_adventure_at = now
    player.last_active_at = now
    return trial_msgs, story_next


def rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
    import hashlib
    raw = f"{guild_id}:{user_id}:{salt}:{datetime.now(timezone.utc).timestamp()}"
    seed = int(hashlib.md5(raw.encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def build_duel_challenge_embed(challenger: Player, opponent: Player) -> discord.Embed:
    embed = discord.Embed(
        title="Duel Challenge",
        description=(
            f"**{challenger.dao_name}** challenges **{opponent.dao_name}** to an arena duel.\n"
            "On accept, the bot opens a private arena with the **same technique combat** as `/hunt` — "
            "equipped techniques, status effects, flee, and finish. Winner gains spirit stones."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name=challenger.dao_name, value=realm_display(challenger.realm_index, challenger.substage), inline=True)
    embed.add_field(name=opponent.dao_name, value=realm_display(opponent.realm_index, opponent.substage), inline=True)
    from ..player_guides import guide_text
    embed.set_footer(
        text=(
            f"{opponent.dao_name} must Accept or Decline within 2 minutes. "
            f"{guide_text('pvp_legality', 'duel_footer')}"
        )
    )
    return embed


def build_duel_started_embed(challenger: Player, opponent: Player, arena: discord.TextChannel) -> discord.Embed:
    return discord.Embed(
        title="Duel Accepted",
        description=f"**{opponent.dao_name}** accepted **{challenger.dao_name}**'s challenge.\nThe fight continues in {arena.mention}.",
        color=discord.Color.green(),
    )


def build_duel_expired_embed(challenge: PendingDuel) -> discord.Embed:
    return discord.Embed(
        title="Duel Expired",
        description=f"**{challenge.challenger_dao_name}**'s challenge to **{challenge.opponent_dao_name}** timed out without a response.",
        color=discord.Color.light_grey(),
    )


def build_duel_declined_embed(challenge: PendingDuel) -> discord.Embed:
    return discord.Embed(
        title="Duel Declined",
        description=f"**{challenge.opponent_dao_name}** declined **{challenge.challenger_dao_name}**'s challenge.",
        color=discord.Color.light_grey(),
    )


async def _finalize_pvp_match_discord(
    client: discord.Client,
    guild: discord.Guild,
    match: ActivePvpMatch,
    cfg,
    *,
    combat_message: discord.Message | None = None,
    combat_state: PvpCombatState | None = None,
) -> None:
    finalized = None
    results_embed = None
    arena_channel_id: str | None = None
    announcement_message: str | None = None
    session = get_session()
    try:
        db_match = session.get(ActivePvpMatch, match.id)
        if db_match is None:
            return
        arena_channel_id = db_match.arena_channel_id
        finalized = finalize_pvp_match(session, db_match, cfg)
        now = utcnow()
        schedule_player_reminders(session, finalized.challenger, cfg, "duel", now=now)
        schedule_player_reminders(session, finalized.opponent, cfg, "duel", now=now)
        results_embed = build_pvp_results_embed(finalized)
        announcement_message = (
            f"⚔️ **Arena result** — **{finalized.winner.dao_name}** defeated "
            f"**{finalized.loser.dao_name}** (+{finalized.stones_gain} spirit stones)."
        )
        session.commit()
    except Exception:
        logger.exception("Failed to finalize PvP match match_id=%s", match.id)
        return
    finally:
        session.close()

    if finalized is None or results_embed is None:
        return

    if combat_message is not None:
        try:
            state = combat_state or finalized.state
            final_embed = build_pvp_combat_embed(state)
            await combat_message.edit(embed=final_embed, view=None)
        except Exception:
            logger.exception("Failed to edit PvP combat message match_id=%s", match.id)

    await post_pvp_results(client, guild.id, cfg.pvp_results_channel_id, results_embed)
    if announcement_message:
        from ..announcements import post_announcement
        await post_announcement(client, cfg, guild_id=str(guild.id), message=announcement_message)

    if arena_channel_id:
        channel = guild.get_channel(int(arena_channel_id))
        if channel is None:
            try:
                channel = await client.fetch_channel(int(arena_channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("Arena channel unavailable for cleanup match_id=%s channel_id=%s", match.id, arena_channel_id)
                channel = None
        if isinstance(channel, discord.TextChannel):
            asyncio.create_task(schedule_arena_cleanup(channel))


async def _prepare_player_for_breakthrough(session, player, cfg, now) -> None:
    mod = get_character_modifiers(session, player)
    collect_passive_qi(player, now, cap_mult=mod.offline_efficiency)
    player.last_active_at = now


async def _finish_breakthrough_attempt(
    interaction: discord.Interaction,
    *,
    guild_id: str,
    discord_id: str,
    cfg,
    rng: random.Random,
    bt_preview,
) -> None:
    session = get_session()
    try:
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.followup.send(NOT_STARTED_HINT, ephemeral=False)
            return

        now = utcnow()
        mod = get_character_modifiers(session, player)
        await _prepare_player_for_breakthrough(session, player, cfg, now)
        bt_preview = compute_breakthrough_preview(
            player, mod, session=session, player_id=player.id
        )
        if not bt_preview.can_attempt:
            await interaction.followup.send(
                f"Your qi is below the threshold — gather at least **{bt_preview.qi_required}** qi "
                f"(you have **{player.qi}**).",
                ephemeral=False,
            )
            return

        old_realm_index = player.realm_index
        bt_rng = random.Random()
        res: BreakthroughResult = breakthrough(
            player,
            cfg,
            rng=bt_rng,
            mod=mod,
            session=session,
            player_id=player.id,
        )
        consume_clarity_for_breakthrough(session, player.id)
        _, enlighten_msg = roll_breakthrough_enlightenment(
            session, player, random.Random(), success=res.success
        )
        trial_msgs: list[str] = []
        story_next: str | None = None
        foundation_msgs: list[str] = []
        chapter_msgs: list[str] = []
        from ..story_mode import elder_trial_active
        in_trial = elder_trial_active(player)
        if res.success:
            trial_msgs, story_next = on_breakthrough_success(session, player, rng)
            if player.realm_index >= 1 and old_realm_index < 1:
                chapter_msgs = unlock_chapter_two(player)
            foundation_msgs.append(grant_body_temper_charges(player, 1))

        session.add(player)
        session.commit()

        desc = res.message
        if (
            res.success
            and player.realm_index != old_realm_index
            and interaction.guild is not None
            and isinstance(interaction.user, discord.Member)
        ):
            _, role_err = await sync_member_realm_role(
                interaction.guild,
                interaction.user,
                player.realm_index,
            )
            if role_err:
                desc += f"\n\n{role_err}"

        if enlighten_msg:
            desc += f"\n\n{enlighten_msg}"
        if trial_msgs and not in_trial:
            desc += "\n\n" + "\n".join(trial_msgs)
        if chapter_msgs:
            desc += "\n\n" + "\n".join(chapter_msgs)
        if foundation_msgs:
            desc += "\n\n" + "\n".join(foundation_msgs)
        if res.success:
            from ..notifications import refresh_sealed_manual_notification
            sealed_line = refresh_sealed_manual_notification(session, player)
            if sealed_line:
                desc += f"\n\n{sealed_line}"
            session.add(player)

        color = discord.Color.green() if res.success else discord.Color.orange()
        embed = discord.Embed(title="Breakthrough", description=desc, color=color)
        roll_note = ""
        if res.roll is not None:
            roll_note = f" · roll **{int(round(res.roll * 100))}**"
        embed.add_field(
            name="Rolled at",
            value=f"**{int(round(res.success_chance * 100))}%** success chance{roll_note}",
            inline=True,
        )
        embed.add_field(name="Qi", value=str(player.qi), inline=True)
        embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)
        attach_guidance(embed, "breakthrough", player, session, cfg, now)

        await interaction.followup.send(embed=embed, ephemeral=False)
        if in_trial:
            await send_elder_followup(
                interaction,
                session,
                player,
                on_player_update=_story_continue_after_creation,
            )
        else:
            await maybe_sync_elder_story(
                interaction,
                session,
                player,
                on_player_update=_story_continue_after_creation,
            )
        session.commit()
        if res.success:
            await post_announcement(
                interaction.client,
                cfg,
                guild_id=guild_id,
                message=(
                    f"⚡ **{player.dao_name}** broke through to "
                    f"**{realm_display(player.realm_index, player.substage)}**!"
                ),
            )
    finally:
        session.close()


async def _story_pending_update(interaction: discord.Interaction, pending) -> None:
    if pending.dao_name:
        in_abode = (
            pending.abode_channel_id
            and str(interaction.channel_id) == pending.abode_channel_id
        )
        delivered = await deliver_pending_story_to_abode(
            interaction, pending, None,
            on_pending_update=_story_pending_update,
            on_finalize=_story_finalize_creation,
            redirect_start=not in_abode,
        )
        if delivered:
            return
    await send_story_node_for_pending(
        interaction, pending, edit=True,
        on_pending_update=_story_pending_update,
        on_finalize=_story_finalize_creation,
    )


async def _story_player_node_update(
    interaction: discord.Interaction,
    player_id: int,
    next_node: str,
) -> None:
    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
            return
        player.story_step = next_node
        if next_node == "complete":
            player.story_step = "complete"
        session.add(player)
        session.commit()
        if interaction.guild is not None and player.abode_channel_id:
            await sync_player_story_to_abode(
                interaction.client, interaction.guild, session, player,
                on_player_update=_story_player_node_update,
            )
            session.commit()
        else:
            await send_story_node_for_player(
                interaction, session, player, edit=True,
                on_player_update=_story_player_node_update,
            )
    finally:
        session.close()


async def _story_continue_after_creation(
    interaction: discord.Interaction,
    player_id: int,
    next_node: str,
    *,
    next_node_override: str | None = None,
    sync_abode: bool = True,
) -> None:
    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
            return
        resolved_node = next_node_override or next_node
        player.story_step = resolved_node
        session.add(player)
        session.commit()
        if sync_abode and interaction.guild is not None and player.abode_channel_id:
            await sync_player_story_to_abode(
                interaction.client, interaction.guild, session, player,
                on_player_update=_story_continue_after_creation,
            )
            session.commit()
        else:
            await send_story_node_for_player(
                interaction, session, player, edit=False,
                on_player_update=_story_continue_after_creation,
            )
    finally:
        session.close()


async def _story_finalize_creation(interaction: discord.Interaction, pending) -> None:
    cfg = get_config()
    session = get_session()
    try:
        guild_id = pending.guild_id
        discord_id = pending.discord_id

        existing = ensure_player(session, guild_id, discord_id)
        if existing is not None:
            clear_pending(guild_id, discord_id)
            await interaction.response.edit_message(
                content="You have already begun. View **`/profile`** or **`/help`**.",
                embed=None, view=None,
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        now = utcnow()
        root = random.choice(SPIRIT_ROOTS)
        origin = random.choice(ORIGINS)
        path_def = get_path_def(pending.story_path)
        path_name = str(path_def.get("name", "")) if path_def else ""

        player = Player(
            guild_id=guild_id,
            discord_id=discord_id,
            discord_username=str(interaction.user),
            dao_name=pending.dao_name,
            gender=pending.gender or "unspecified",
            story_path=pending.story_path,
            origin=origin,
            spirit_root=root,
            moral_path="neutral",
            karma=0,
            realm_index=0,
            substage=0,
            qi=0,
            spirit_stones=0,
            last_cultivate_at=None,
            last_daily_at=None,
            last_daily_streak_claimed_at=None,
            last_pvp_at=None,
            last_active_at=now,
            passive_accrual_at=now,
            daily_streak=0,
            clan_id=None,
            clan_role="member",
            clan_contribution_qi_total=0,
            game_sect_id=None,
            sect_merit=0,
        )
        finalize_creation_state(player)
        player.story_step = "spirit_reveal"
        session.add(player)
        session.flush()

        apply_origin_starter_gifts(session, player)

        session.add(player)
        session.commit()

        await send_story_node_for_player(
            interaction, session, player, edit=False,
            on_player_update=_story_player_node_update,
        )
        await maybe_sync_elder_story(
            interaction, session, player,
            on_player_update=_story_continue_after_creation,
        )
        session.commit()
    finally:
        session.close()


def upsert_player_if_missing(session: Session, guild_id: str, discord_id: str, username: str) -> Player | None:
    player = ensure_player(session, guild_id, discord_id)
    if player is not None:
        return player
    logger.warning("Player not found guild=%s user=%s — returning None", guild_id, discord_id)
    return None


def build_profile_view(
    owner_discord_id: str,
    cfg,
    rng: random.Random,
    player: Player,
):
    from ..story_mode import current_awaited_command, elder_trial_active
    from .views.cultivate_view import CultivateView, CultivationButtons, CultivateButton, BreakthroughButton

    if not elder_trial_active(player):
        return CultivateView(owner_discord_id, cfg, rng)
    awaited = current_awaited_command(player)
    if awaited not in ("cultivate", "breakthrough"):
        return None
    view = CultivationButtons(owner_discord_id, cfg, rng)
    if awaited == "cultivate":
        view.add_item(CultivateButton(cfg, rng))
    else:
        view.add_item(BreakthroughButton(owner_discord_id, cfg, rng))
    return view
