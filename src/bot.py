from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_config
from .db import get_session, init_db
from .discord_guild import provision_new_cultivator, sync_member_realm_role
from .content import get_areas, get_dungeons, get_recipes, load_all_content
from .character import get_character_modifiers
from .areas_info import build_areas_embed
from .adventure import (
    STANCES,
    AdventureResult,
    PendingAdventure,
    abandon_adventure,
    apply_adventure_choice,
    apply_adventure_combat_outcome,
    get_active_adventure,
    resume_adventure_session,
    start_adventure_session,
)
from .cooldown_haste import (
    consume_haste_for_activity,
    cooldown_remaining_with_haste,
    get_haste_reduction_seconds,
)
from .forge import forge_equipment
from .post_tutorial import post_tutorial
from .post_library import post_library
from .recipes_info import build_recipes_embed
from .reminders import (
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
from .roots_info import build_roots_embed
from .gather import run_gather
from .hunt import finalize_hunt_combat, start_hunt_combat
from .combat.discord_ui import build_adventure_combat_embed, build_combat_embed, build_hunt_combat_embed
from .cultivate_events import apply_cultivate_bonus_drops
from .combat.loadout import ensure_starter_techniques, equip_technique, get_equipped_active_techniques
from .combat.learn import learn_technique_from_manual
from .combat.session import process_combat_action
from .combat_stats import compute_combat_stats, format_combat_stats_block
from .stats import format_stats_summary
from .shop import build_shop_embed, buy_from_shop, list_shop_listings, load_shop_catalog, resolve_shop_id
from src.cultivation_preview import format_breakthrough_chance_line
from .manuals import (
    craft_manual_from_fragments,
    roll_breakthrough_enlightenment,
)
from .ui.embeds import (
    build_adventure_embed_from_pending,
    build_adventure_embed_from_result,
    build_cultivate_embed,
    build_hunt_result_embed,
)
from .ui.formatting import technique_button_emoji
from .command_choices import (
    TECHNIQUE_SLOT_OPTIONS,
    can_bind_technique_manual,
    filter_options,
    list_affixable_slots,
    list_affordable_shop_items,
    list_craftable_recipes,
    list_enterable_dungeons,
    list_equippable_techniques,
    list_forgeable_slots,
    list_player_manuals,
    list_technique_equip_options,
    list_unlocked_areas,
    list_valid_slots_for_technique,
    resolve_forge_slot,
    resolve_technique_id,
)
from .crafting import craft_recipe
from .dungeon import run_dungeon
from .player_dashboard import build_profile_embed, build_techniques_embed
from .technique_info import (
    build_technique_detail_embed,
    list_technique_inspect_options,
    resolve_technique_inspect_target,
)
from .combat.technique_ui import TechniquesView
from .duel_challenges import (
    DUEL_CHALLENGE_TIMEOUT_SECONDS,
    ExecutedDuel,
    accept_duel_challenge,
    attach_challenge_message,
    create_duel_challenge,
    decline_duel_challenge,
    expire_duel_challenge,
    expire_stale_challenges,
)
from .equipment import apply_affix_stone, format_loadout
from .consumables import use_item
from .effects import consume_effect_charge
from .guidance import (
    add_guidance_to_embed,
    build_cooldown_embed,
    build_help_embed,
    get_start_next_steps,
    get_welcome_intro,
)
from .game import (
    ORIGINS,
    REALMS,
    SPIRIT_ROOTS,
    SUBSTAGES,
    CultivateResult,
    BreakthroughResult,
    DuelResult,
    breakthrough,
    compute_daily_rewards,
    cultivate,
    apply_stamina_regen,
    apply_offline_progress,
    qi_cap,
    utcnow,
    stamina_regen_per_hour,
    compute_breakthrough_preview,
    player_strength_for_pvp,
)
from .announcements import post_announcement
from .models import ActiveAdventure, Clan, PendingDuel, Player
from .clans import (
    can_join_clan,
    consume_clan_invitation,
    create_clan_invitation,
    get_clan_by_name,
    get_clan_top_contributors,
    list_clan_invitations_for_player,
    set_clan_invite_only,
)
from .game_sects import (
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
from .inventory import build_inventory_embed, get_player_inventory, load_item_catalog
from .item_info import build_item_detail_embed, list_inventory_item_options, resolve_inventory_item_id
from .novice_trial import (
    apply_origin_starter_gifts,
    on_breakthrough_success,
    on_daily_claimed,
    on_adventure_completed,
)

load_all_content()
load_item_catalog()
load_shop_catalog()

STANCE_CHOICES = [app_commands.Choice(name=s.title(), value=s) for s in STANCES]
ROOT_CHOICES = [app_commands.Choice(name=r, value=r) for r in SPIRIT_ROOTS]

REMIND_ACTION_CHOICES = [
    app_commands.Choice(name="Status", value="status"),
    app_commands.Choice(name="Turn on", value="on"),
    app_commands.Choice(name="Turn off", value="off"),
]
REMIND_ACTIVITY_CHOICES = [
    app_commands.Choice(name="All timers", value="all"),
    *[app_commands.Choice(name=label, value=activity) for activity, label in ACTIVITY_LABELS.items()],
]


LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cultivation_bot")


def interaction_ctx(interaction: discord.Interaction) -> str:
    guild_id = str(interaction.guild.id) if interaction.guild is not None else "DM"
    user_id = str(interaction.user.id) if interaction.user is not None else "unknown"
    cmd = getattr(interaction, "command", None)
    cmd_name = getattr(cmd, "name", "unknown") if cmd is not None else "unknown"
    return f"guild={guild_id} user={user_id} cmd={cmd_name} interaction_id={interaction.id}"


def to_utc(dt: datetime) -> datetime:
    # SQLite often returns naive datetimes; treat them as UTC in MVP.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


STONES_FOR_DUEL_COOLDOWN_NOTICE = 0


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


class CombatView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        combat_id: int,
        context: str,
        *,
        area_id: str = "",
        beast_id: str = "",
        active_id: int | None = None,
        area_name: str = "",
        techniques: list | None = None,
        technique_cooldowns: dict[str, int] | None = None,
        player_sealed: bool = False,
    ):
        super().__init__(timeout=300)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.combat_id = combat_id
        self.context = context
        self.area_id = area_id
        self.beast_id = beast_id
        self.active_id = active_id
        self.area_name = area_name
        cds = technique_cooldowns or {}

        for tech in (techniques or [])[:4]:
            emoji = technique_button_emoji(tech.category)
            cd = cds.get(tech.technique_id, 0)
            sealed_blocked = player_sealed and tech.technique_id != "basic_strike"
            label = f"{emoji} {tech.name}"
            if cd > 0:
                label = f"⏳{cd} {label}"
            elif sealed_blocked:
                label = f"🔒 {label}"
            label = label[:80]
            if tech.technique_id == "basic_strike":
                style = discord.ButtonStyle.primary
            elif cd > 0 or sealed_blocked:
                style = discord.ButtonStyle.secondary
            else:
                style = discord.ButtonStyle.danger
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"cbt:{combat_id}:tech:{tech.technique_id}",
                disabled=cd > 0 or sealed_blocked,
            )
            button.callback = self._make_technique_callback(tech.technique_id)
            self.add_item(button)

        flee_btn = discord.ui.Button(
            label="🏃 Flee",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cbt:{combat_id}:flee",
        )
        flee_btn.callback = self._make_action_callback("flee")
        self.add_item(flee_btn)

        finish_btn = discord.ui.Button(
            label="✅ Finish",
            style=discord.ButtonStyle.success,
            custom_id=f"cbt:{combat_id}:finish",
        )
        finish_btn.callback = self._make_action_callback("finish")
        self.add_item(finish_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This fight belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True

    def _make_technique_callback(self, technique_id: str):
        async def callback(interaction: discord.Interaction):
            await self._handle_action(interaction, "technique", technique_id=technique_id)

        return callback

    def _make_action_callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            await self._handle_action(interaction, action)

        return callback

    async def _handle_action(
        self,
        interaction: discord.Interaction,
        action: str,
        *,
        technique_id: str | None = None,
    ) -> None:
        session = get_session()
        try:
            cfg = get_config()
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
                return

            mod = get_character_modifiers(session, player)
            stats = compute_combat_stats(player, session, mod)
            rng = rng_for(self.guild_id, discord_id)
            result, err = process_combat_action(
                session,
                player,
                self.combat_id,
                action,
                technique_id=technique_id,
                stats=stats,
                mod=mod,
                rng=rng,
            )
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            assert result is not None
            state = result.state
            now = utcnow()

            if not state.finished:
                techniques = get_equipped_active_techniques(session, player.id)
                if self.context == "hunt":
                    embed = build_combat_embed(f"Hunt — {self.area_name}", state)
                else:
                    pending = PendingAdventure(
                        active_id=self.active_id or 0,
                        area_name=self.area_name,
                        segment=1,
                        segments_total=2,
                        prompt="Combat in progress.",
                        encounter_type="combat",
                    )
                    embed = build_adventure_combat_embed(pending, state)
                view = CombatView(
                    self.owner_discord_id,
                    self.guild_id,
                    self.combat_id,
                    self.context,
                    area_id=self.area_id,
                    beast_id=self.beast_id,
                    active_id=self.active_id,
                    area_name=self.area_name,
                    techniques=techniques,
                    technique_cooldowns=state.technique_cooldowns,
                    player_sealed=state.player.sealed,
                )
                session.commit()
                await interaction.response.edit_message(embed=embed, view=view)
                return

            if self.context == "hunt":
                hunt_res = finalize_hunt_combat(
                    session,
                    player,
                    self.area_id,
                    self.beast_id,
                    state.victory,
                    rng=rng,
                )
                player.last_hunt_at = now
                player.last_active_at = now
                consume_haste_for_activity(session, player.id, "hunt")
                schedule_player_reminders(session, player, cfg, "hunt", now=now)
                session.add(player)
                session.commit()

                color = discord.Color.dark_green() if hunt_res.success else discord.Color.dark_red()
                embed = build_hunt_result_embed(self.area_name or hunt_res.area_name, state, hunt_res)
                embed.color = color
                attach_guidance(embed, "hunt", player, session, cfg, now)
                await interaction.response.edit_message(embed=embed, view=None)
                return

            assert self.active_id is not None
            adventure_result, adv_err = apply_adventure_combat_outcome(
                session,
                player,
                self.active_id,
                victory=state.victory,
                fled=state.fled,
                rng=rng,
            )
            if adv_err:
                await interaction.response.send_message(adv_err, ephemeral=True)
                return

            if isinstance(adventure_result, PendingAdventure):
                if adventure_result.encounter_type == "combat" and adventure_result.combat_id:
                    techniques = get_equipped_active_techniques(session, player.id)
                    from .combat.session import get_active_combat, load_combat_state

                    active_combat = get_active_combat(session, player.id)
                    combat_state = load_combat_state(active_combat) if active_combat else state
                    embed = build_adventure_combat_embed(adventure_result, combat_state)
                    view = CombatView(
                        self.owner_discord_id,
                        self.guild_id,
                        adventure_result.combat_id,
                        "adventure",
                        area_id=self.area_id,
                        active_id=adventure_result.active_id,
                        area_name=adventure_result.area_name,
                        techniques=techniques,
                        technique_cooldowns=combat_state.technique_cooldowns,
                        player_sealed=combat_state.player.sealed,
                    )
                else:
                    embed = build_adventure_embed_from_pending(adventure_result)
                    attach_guidance(embed, "adventure", player, session, cfg, now)
                    view = AdventureChoiceView(
                        self.owner_discord_id,
                        self.guild_id,
                        adventure_result.active_id,
                        adventure_result.choices,
                    )
                session.commit()
                await interaction.response.edit_message(embed=embed, view=view)
                return

            assert isinstance(adventure_result, AdventureResult)
            trial_msgs = _apply_adventure_completion(session, player, adventure_result, now)
            consume_haste_for_activity(session, player.id, "adventure")
            schedule_player_reminders(session, player, cfg, "adventure", now=now)
            session.add(player)
            session.commit()
            embed = build_adventure_embed_from_result(adventure_result, player.qi)
            if trial_msgs:
                embed.add_field(name="Trial progress", value="\n".join(trial_msgs), inline=False)
            attach_guidance(embed, "adventure", player, session, cfg, now)
            await interaction.response.edit_message(embed=embed, view=None)
        finally:
            session.close()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class AdventureChoiceView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        active_id: int,
        choices: tuple,
    ):
        super().__init__(timeout=180)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.active_id = active_id
        for choice in choices[:5]:
            button = discord.ui.Button(
                label=choice.label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"adv:{active_id}:{choice.id}",
            )
            button.callback = self._make_callback(choice.id)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This choice belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True

    def _make_callback(self, choice_id: str):
        async def callback(interaction: discord.Interaction):
            session = get_session()
            try:
                cfg = get_config()
                discord_id = get_discord_id(interaction.user)
                player = ensure_player(session, self.guild_id, discord_id)
                if player is None:
                    await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
                    return

                rng = rng_for(self.guild_id, discord_id)
                result, err = apply_adventure_choice(
                    session, player, self.active_id, choice_id, rng=rng
                )
                if err:
                    await interaction.response.send_message(err, ephemeral=True)
                    return

                now = utcnow()
                if isinstance(result, PendingAdventure):
                    if result.encounter_type == "combat" and result.combat_id:
                        from .combat.session import get_active_combat, load_combat_state

                        ensure_starter_techniques(session, player.id)
                        techniques = get_equipped_active_techniques(session, player.id)
                        active_row = session.get(ActiveAdventure, result.active_id)
                        area_id = active_row.area_id if active_row else ""
                        active_combat = get_active_combat(session, player.id)
                        combat_state = load_combat_state(active_combat) if active_combat else None
                        embed = (
                            build_adventure_combat_embed(result, combat_state)
                            if combat_state
                            else build_adventure_embed_from_pending(result)
                        )
                        view = CombatView(
                            self.owner_discord_id,
                            self.guild_id,
                            result.combat_id,
                            "adventure",
                            area_id=area_id,
                            active_id=result.active_id,
                            area_name=result.area_name,
                            techniques=techniques,
                            technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                            player_sealed=combat_state.player.sealed if combat_state else False,
                        )
                    else:
                        embed = build_adventure_embed_from_pending(result)
                        attach_guidance(embed, "adventure", player, session, cfg, now)
                        view = AdventureChoiceView(
                            self.owner_discord_id,
                            self.guild_id,
                            result.active_id,
                            result.choices,
                        )
                    session.commit()
                    await interaction.response.edit_message(embed=embed, view=view)
                    return

                assert isinstance(result, AdventureResult)
                trial_msgs = _apply_adventure_completion(session, player, result, now)
                consume_haste_for_activity(session, player.id, "adventure")
                schedule_player_reminders(session, player, cfg, "adventure", now=now)
                session.add(player)
                session.commit()

                embed = build_adventure_embed_from_result(result, player.qi)
                if trial_msgs:
                    embed.add_field(name="Trial progress", value="\n".join(trial_msgs), inline=False)
                attach_guidance(embed, "adventure", player, session, cfg, now)
                await interaction.response.edit_message(embed=embed, view=None)
            finally:
                session.close()

        return callback

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


def discord_object(maybe_id: str | None) -> discord.Object | None:
    if not maybe_id:
        return None
    try:
        return discord.Object(id=int(maybe_id))
    except ValueError:
        return None


def get_guild_id(interaction: discord.Interaction) -> str:
    assert interaction.guild is not None
    # Discord IDs fit in int64; we keep as string for portability.
    return str(interaction.guild.id)


def get_discord_id(user: discord.abc.User) -> str:
    return str(user.id)


def ensure_player(session: Session, guild_id: str, discord_id: str) -> Player | None:
    stmt = select(Player).where(Player.guild_id == guild_id, Player.discord_id == discord_id)
    return session.execute(stmt).scalar_one_or_none()


def get_clan_by_name_lookup(session: Session, guild_id: str, name: str) -> Clan | None:
    return get_clan_by_name(session, guild_id, name)


def ensure_loaded_player(player: Player) -> None:
    # No-op; placeholder for future initialization.
    _ = player


def _apply_adventure_completion(
    session: Session,
    player: Player,
    result: AdventureResult,
    now: datetime,
) -> list[str]:
    trial_msgs, waive_cd = on_adventure_completed(
        session, player, segments_cleared=result.segments_cleared
    )
    if not waive_cd:
        player.last_adventure_at = now
    player.last_active_at = now
    return trial_msgs


def build_duel_challenge_embed(challenger: Player, opponent: Player) -> discord.Embed:
    embed = discord.Embed(
        title="Duel Challenge",
        description=(
            f"**{challenger.dao_name}** challenges **{opponent.dao_name}** to a spar.\n"
            f"Winner gains spirit stones. Loser keeps their Qi."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name=challenger.dao_name,
        value=realm_display(challenger.realm_index, challenger.substage),
        inline=True,
    )
    embed.add_field(
        name=opponent.dao_name,
        value=realm_display(opponent.realm_index, opponent.substage),
        inline=True,
    )
    embed.set_footer(text=f"{opponent.dao_name} must Accept or Decline within 2 minutes.")
    return embed


def build_duel_result_embed(executed: ExecutedDuel) -> discord.Embed:
    challenger = executed.challenger
    opponent = executed.opponent
    res = executed.result
    winner = challenger if res.success else opponent
    loser = opponent if res.success else challenger
    embed = discord.Embed(
        title="Duel Complete",
        description=res.message,
        color=discord.Color.green() if res.success else discord.Color.orange(),
    )
    embed.add_field(name="Challenger", value=challenger.dao_name, inline=True)
    embed.add_field(name="Defender", value=opponent.dao_name, inline=True)
    embed.add_field(name="Winner", value=winner.dao_name, inline=True)
    embed.add_field(
        name="Spirit stones",
        value=f"**{winner.dao_name}** gains **+{res.stones_delta_winner}** (now **{winner.spirit_stones}**).",
        inline=False,
    )
    embed.add_field(
        name="Realms",
        value=(
            f"{challenger.dao_name}: {realm_display(challenger.realm_index, challenger.substage)}\n"
            f"{opponent.dao_name}: {realm_display(opponent.realm_index, opponent.substage)}"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{loser.dao_name} may recover and challenge again after the duel cooldown.")
    return embed


def build_duel_declined_embed(challenge: PendingDuel) -> discord.Embed:
    return discord.Embed(
        title="Duel Declined",
        description=f"**{challenge.opponent_dao_name}** declined **{challenge.challenger_dao_name}**'s challenge.",
        color=discord.Color.light_grey(),
    )


def build_duel_expired_embed(challenge: PendingDuel) -> discord.Embed:
    return discord.Embed(
        title="Duel Expired",
        description=(
            f"**{challenge.challenger_dao_name}**'s challenge to **{challenge.opponent_dao_name}** "
            "timed out without a response."
        ),
        color=discord.Color.light_grey(),
    )


class DuelChallengeView(discord.ui.View):
    def __init__(
        self,
        challenge_id: int,
        guild_id: str,
        challenger_discord_id: str,
        opponent_discord_id: str,
    ):
        super().__init__(timeout=DUEL_CHALLENGE_TIMEOUT_SECONDS)
        self.challenge_id = challenge_id
        self.guild_id = guild_id
        self.challenger_discord_id = challenger_discord_id
        self.opponent_discord_id = opponent_discord_id
        self.message: discord.Message | None = None

    def _disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _edit_message(self, embed: discord.Embed, interaction: discord.Interaction | None = None) -> None:
        self._disable_buttons()
        target = interaction.message if interaction is not None else self.message
        if target is not None:
            await target.edit(embed=embed, view=self)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor_id = get_discord_id(interaction.user)
        if actor_id != self.opponent_discord_id:
            await interaction.response.send_message(
                "Only the challenged daoist may accept this duel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        session = get_session()
        try:
            cfg = get_config()
            challenger = ensure_player(session, self.guild_id, self.challenger_discord_id)
            opponent = ensure_player(session, self.guild_id, self.opponent_discord_id)
            if challenger is None or opponent is None:
                await interaction.followup.send(
                    "One of the duelists no longer has a character.",
                    ephemeral=True,
                )
                return

            rng = rng_for(self.guild_id, f"{challenger.discord_id}:{opponent.discord_id}")
            executed, err = accept_duel_challenge(
                session,
                self.challenge_id,
                actor_id,
                challenger,
                opponent,
                cfg,
                rng,
            )
            if err:
                await interaction.followup.send(err, ephemeral=True)
                return

            now = utcnow()
            schedule_player_reminders(session, challenger, cfg, "duel", now=now)
            schedule_player_reminders(session, opponent, cfg, "duel", now=now)
            session.commit()
            logger.info(
                "Duel accepted challenge_id=%s guild=%s challenger=%s opponent=%s success=%s",
                self.challenge_id,
                self.guild_id,
                challenger.discord_id,
                opponent.discord_id,
                executed.result.success,
            )
            embed = build_duel_result_embed(executed)
            await self._edit_message(embed, interaction)
            winner = executed.challenger if executed.result.success else executed.opponent
            loser = executed.opponent if executed.result.success else executed.challenger
            await post_announcement(
                interaction.client,
                cfg,
                guild_id=self.guild_id,
                message=(
                    f"⚔️ **Duel resolved** — **{winner.dao_name}** defeated **{loser.dao_name}** "
                    f"(+{executed.result.stones_delta_winner} spirit stones)."
                ),
            )
        except Exception:
            logger.exception("Duel accept failed challenge_id=%s", self.challenge_id)
            await interaction.followup.send(
                "The duel could not be completed. Try again later.",
                ephemeral=True,
            )
        finally:
            session.close()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor_id = get_discord_id(interaction.user)
        if actor_id != self.opponent_discord_id:
            await interaction.response.send_message(
                "Only the challenged daoist may decline this duel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        session = get_session()
        try:
            challenge, err = decline_duel_challenge(session, self.challenge_id, actor_id)
            if err:
                await interaction.followup.send(err, ephemeral=True)
                return
            session.commit()
            assert challenge is not None
            await self._edit_message(build_duel_declined_embed(challenge), interaction)
        finally:
            session.close()

    async def on_timeout(self) -> None:
        session = get_session()
        try:
            challenge = expire_duel_challenge(session, self.challenge_id)
            if challenge is not None:
                session.commit()
                await self._edit_message(build_duel_expired_embed(challenge))
        finally:
            session.close()


class CultivationButtons(discord.ui.View):
    def __init__(self, owner_discord_id: str, cfg, rng: random.Random):
        super().__init__(timeout=None)
        self.owner_discord_id = owner_discord_id
        self.cfg = cfg
        self.rng = rng

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if get_discord_id(interaction.user) != self.owner_discord_id:
            logger.warning(
                "Button press denied %s owner=%s",
                interaction_ctx(interaction),
                self.owner_discord_id,
            )
            await interaction.response.send_message(
                "That cultivation command is not yours to press.",
                ephemeral=True,
            )
            return False
        return True


class CultivateButton(discord.ui.Button):
    def __init__(self, cfg, rng: random.Random):
        super().__init__(label="🧘 Cultivate", style=discord.ButtonStyle.primary)
        self.cfg = cfg
        self.rng = rng

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            logger.info("BUTTON Cultivate begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
                return

            now = utcnow()
            remaining = activity_cooldown_remaining(
                session,
                player,
                now,
                "cultivate",
                player.last_cultivate_at,
                self.cfg.cultivate_cooldown_seconds,
            )
            if remaining > 0:
                logger.debug(
                    "Button /cultivate cooldown block guild=%s user=%s remaining=%ss",
                    guild_id,
                    discord_id,
                    remaining,
                )
                await interaction.response.send_message(
                    f"The qi pool is not ready yet. Wait {format_seconds(remaining)}.",
                    ephemeral=True,
                )
                return

            # Apply offline + stamina regen for fairness.
            apply_stamina_regen(player, now)
            mod = get_character_modifiers(session, player)
            passive_before = apply_offline_progress(
                player, now, self.cfg.offline_cap_minutes, cap_mult=mod.offline_cap_mult
            )
            if passive_before > 0:
                player.qi += passive_before
                player.last_active_at = now

            clan = None
            if player.clan_id is not None:
                clan = session.get(Clan, player.clan_id)

            res: CultivateResult = cultivate(
                player, clan, self.cfg, rng=self.rng, mod=mod, session=session, player_id=player.id
            )
            applied_drops = apply_cultivate_bonus_drops(session, player.id, res.bonus_drops or {})
            if "qi_gathering" in mod.active_effects:
                consume_effect_charge(session, player.id, "qi_gathering")
            consume_haste_for_activity(session, player.id, "cultivate")
            player.last_cultivate_at = now
            schedule_player_reminders(session, player, self.cfg, "cultivate", now=now)

            session.add(player)
            if clan is not None:
                session.add(clan)
            session.commit()
            logger.info(
                "BUTTON Cultivate complete guild=%s user=%s qi_gain=%s stones_gain=%s new_qi=%s new_stamina=%s realm=%s/%s",
                guild_id,
                discord_id,
                res.qi_gain,
                res.stones_gain,
                player.qi,
                player.stamina,
                player.realm_index,
                player.substage,
            )

            embed = build_cultivate_embed(
                res,
                player,
                realm_display=realm_display(player.realm_index, player.substage),
                passive_qi=passive_before,
                applied_drops=applied_drops,
            )
            attach_guidance(embed, "cultivate", player, session, self.cfg, now)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        finally:
            session.close()


class BreakthroughButton(discord.ui.Button):
    def __init__(self, cfg, rng: random.Random):
        super().__init__(label="Attempt Breakthrough", style=discord.ButtonStyle.secondary)
        self.cfg = cfg
        self.rng = rng

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            logger.info("BUTTON Breakthrough begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
                return

            now = utcnow()
            apply_stamina_regen(player, now)
            mod = get_character_modifiers(session, player)
            offline_qi = apply_offline_progress(player, now, self.cfg.offline_cap_minutes, cap_mult=mod.offline_cap_mult)
            if offline_qi > 0:
                player.qi += offline_qi
                player.last_active_at = now

            player.last_active_at = now
            mod = get_character_modifiers(session, player)
            bt_preview = compute_breakthrough_preview(player, mod)
            res: BreakthroughResult = breakthrough(player, self.cfg, rng=self.rng, mod=mod)
            _, enlighten_msg = roll_breakthrough_enlightenment(
                session, player, self.rng, success=res.success
            )
            trial_msgs: list[str] = []
            if res.success:
                trial_msgs = on_breakthrough_success(session, player, self.rng)
            if "clarity" in mod.active_effects and res.success:
                consume_effect_charge(session, player.id, "clarity")

            session.add(player)
            session.commit()
            logger.info(
                "BUTTON Breakthrough complete guild=%s user=%s success=%s qi=%s new_realm=%s/%s",
                guild_id,
                discord_id,
                res.success,
                player.qi,
                player.realm_index,
                player.substage,
            )

            color = discord.Color.green() if res.success else discord.Color.orange()
            desc = res.message
            if enlighten_msg:
                desc += f"\n\n{enlighten_msg}"
            if trial_msgs:
                desc += "\n\n" + "\n".join(trial_msgs)
            embed = discord.Embed(title="Breakthrough", description=desc, color=color)
            if bt_preview.can_attempt:
                embed.add_field(
                    name="Attempted at",
                    value=f"**{int(round(bt_preview.success_chance * 100))}%** success chance",
                    inline=True,
                )
            else:
                embed.add_field(
                    name="Breakthrough odds",
                    value=format_breakthrough_chance_line(bt_preview, player),
                    inline=False,
                )
            embed.add_field(name="Qi", value=str(player.qi), inline=True)
            embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)
            attach_guidance(embed, "breakthrough", player, session, self.cfg, now)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        finally:
            session.close()


class CultivateView(CultivationButtons):
    def __init__(self, owner_discord_id: str, cfg, rng: random.Random):
        super().__init__(owner_discord_id, cfg, rng)
        self.add_item(CultivateButton(cfg, rng))
        self.add_item(BreakthroughButton(cfg, rng))


intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


@tasks.loop(seconds=60)
async def send_due_reminders() -> None:
    session = get_session()
    try:
        due = fetch_due_reminders(session, utcnow())
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


def rng_for(guild_id: str, user_id: str) -> random.Random:
    # Stable per-user/per-guild randomness source is not required; this is only to avoid
    # extremely similar outcomes during quick retries.
    seed = hash((guild_id, user_id, datetime.now(timezone.utc).date().isoformat())) & 0xFFFFFFFF
    return random.Random(seed)


# Slash-command choices (must be defined before command decorators).
ORIGIN_CHOICES = [app_commands.Choice(name=o, value=o) for o in ORIGINS]


async def upsert_player_if_missing(interaction: discord.Interaction) -> Player | None:
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        return player
    finally:
        session.close()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    # Centralized logging for slash command failures.
    logger.exception("App command error: %s error=%r", interaction_ctx(interaction), error)
    try:
        content = "Something went wrong in that command. Check the bot logs."
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        # Avoid raising from error handler.
        logger.exception("Failed to send error response for %s", interaction_ctx(interaction))


@bot.event
async def on_ready():
    if not send_due_reminders.is_running():
        send_due_reminders.start()

    cfg = get_config()
    print(f"Logged in as {bot.user} (guild sync: {cfg.guild_id or 'global'})")
    # Sync commands only after the bot is ready, otherwise discord.py may not
    # have an application_id yet (causes MissingApplicationID).
    if getattr(bot, "_did_sync_commands", False):
        return
    bot._did_sync_commands = True
    session = get_session()
    try:
        expired = expire_stale_challenges(session, utcnow())
        if expired:
            session.commit()
            logger.info("Expired %s stale duel challenge(s) on startup.", expired)
    finally:
        session.close()
    try:
        command_count = len(bot.tree.get_commands())
        if cfg.guild_id:
            try:
                guild = discord.Object(id=int(cfg.guild_id))
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                # Remove stale global commands (e.g. legacy /start with moral_path).
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync()
                print(
                    f"Slash commands synced to guild ({len(synced)} commands, "
                    f"{command_count} in tree); cleared stale global commands."
                )
            except discord.Forbidden as e:
                # Common cause: bot isn't invited/authorized for application commands
                # in that guild, or GUILD_ID isn't the actual server id.
                print(f"Guild sync forbidden ({e}). Falling back to global sync (may take up to ~1h).")
                synced = await bot.tree.sync()
                print(f"Slash commands synced globally ({len(synced)} commands).")
        else:
            synced = await bot.tree.sync()
            print(f"Slash commands synced globally ({len(synced)} commands, {command_count} in tree).")
    except Exception as e:
        # Keep the bot online even if sync fails; surface error in console.
        print(f"Slash command sync failed: {e!r}")


@bot.tree.command(
    name="start",
    description="Begin your cultivation path — choose your dao name and origin.",
)
@app_commands.choices(origin=ORIGIN_CHOICES)
@app_commands.describe(
    dao_name="Your dao name (as you wish the world to remember).",
    origin="Your background — each origin grants different starting gifts and manuals.",
)
async def start_cmd(
    interaction: discord.Interaction,
    dao_name: str,
    origin: app_commands.Choice[str],
):
    cfg = get_config()
    session = get_session()
    try:
        logger.info(
            "CMD /start begin %s dao=%r origin=%r",
            interaction_ctx(interaction),
            dao_name,
            origin.value,
        )
        # Acknowledge interaction immediately to avoid Discord's "application did not respond" timeout.
        await interaction.response.defer(thinking=True)

        if interaction.guild is None:
            await interaction.followup.send("This bot works inside a server.", ephemeral=True)
            return

        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)

        existing = ensure_player(session, guild_id, discord_id)
        if existing is not None:
            await interaction.followup.send(
                "You have already begun. View **`/profile`**, check timers with **`/cooldown`**, or see **`/help`**.",
                ephemeral=True,
            )
            return

        now = utcnow()
        root = random.choice(SPIRIT_ROOTS)

        player = Player(
            guild_id=guild_id,
            discord_id=discord_id,
            discord_username=str(interaction.user),
            dao_name=dao_name,
            origin=origin.value,
            spirit_root=root,
            moral_path="neutral",
            karma=0,
            realm_index=0,
            substage=0,
            qi=0,
            spirit_stones=0,
            stamina=100,
            stamina_last_updated_at=now,
            last_cultivate_at=None,
            last_daily_at=None,
            last_daily_streak_claimed_at=None,
            last_pvp_at=None,
            last_active_at=now,
            daily_streak=0,
            clan_id=None,
            clan_role="member",
            clan_contribution_qi_total=0,
            game_sect_id=None,
            sect_merit=0,
        )
        session.add(player)
        session.flush()
        gift_msgs = apply_origin_starter_gifts(session, player)
        ensure_starter_techniques(session, player.id)
        session.commit()
        logger.debug(
            "Created player id=%s guild_id=%s discord_id=%s dao=%r root=%r karma=%s",
            player.id,
            guild_id,
            discord_id,
            dao_name,
            root,
            player.karma,
        )

        member = interaction.user
        abode_note = ""
        if isinstance(member, discord.Member):
            provision = await provision_new_cultivator(
                interaction.guild,
                member,
                dao_name,
                player.realm_index,
                abode_category_id=cfg.abode_category_id,
            )
            if provision.channel is not None:
                player.abode_channel_id = str(provision.channel.id)
                session.add(player)
                session.commit()
                abode_note = (
                    f"\n\nYour private abode is ready: {provision.channel.mention} "
                    "— cultivate and venture from there."
                )
            elif provision.channel_error:
                abode_note = f"\n\n{provision.channel_error}"
            if provision.role is not None:
                abode_note += f"\nYou bear the **{provision.role.name}** rank."
            elif provision.role_error:
                abode_note += f"\n\n{provision.role_error}"

        embed = discord.Embed(
            title="Your Cultivation Begins",
            description=(
                f"{get_welcome_intro()}\n\n"
                f"You name yourself **{dao_name}**. The past recedes: **{origin.value}**.\n"
                f"Your spirit root is revealed: **{root}**.\n"
                f"Your karma begins at **0** — choices in adventures will shape your dao."
                f"{abode_note}"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)
        embed.add_field(name="Qi", value=str(player.qi), inline=True)
        embed.add_field(name="Spirit Stones", value=str(player.spirit_stones), inline=True)
        embed.add_field(name="Stamina", value=f"{player.stamina}/100", inline=True)
        if gift_msgs:
            embed.add_field(name="Origin gifts", value="\n".join(gift_msgs), inline=False)
        embed.add_field(
            name="Outer Disciple Trial",
            value="Begin with **`/daily`**, then follow the trial steps on **`/profile`**.",
            inline=False,
        )
        embed.add_field(name="Your first steps", value=get_start_next_steps(), inline=False)
        attach_guidance(embed, "start", player, session, cfg, now)
        await interaction.followup.send(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="reroll_root", description="Reroll your spirit root (limited).")
async def reroll_root_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /reroll_root begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        # MVP gating:
        # - First reroll is free (if not used yet).
        # - After that, require stones and wait 7 days between rerolls.
        free_used = bool(player.spirit_root_reroll_free_used)
        cost = 50
        gate_days = 7

        if not free_used:
            player.spirit_root = random.choice(SPIRIT_ROOTS)
            player.spirit_root_reroll_free_used = True
            player.spirit_root_last_reroll_at = now
            session.add(player)
            session.commit()
        else:
            if player.spirit_root_last_reroll_at is not None:
                elapsed = now - player.spirit_root_last_reroll_at
                if elapsed < timedelta(days=gate_days):
                    remaining = int((timedelta(days=gate_days) - elapsed).total_seconds())
                    await interaction.response.send_message(
                        f"Reroll is not yet ready. Wait {format_seconds(remaining)}.",
                        ephemeral=True,
                    )
                    return
            if player.spirit_stones < cost:
                await interaction.response.send_message(
                    f"You need {cost} spirit stones for a reroll.",
                    ephemeral=True,
                )
                return

            player.spirit_stones -= cost
            player.spirit_root = random.choice(SPIRIT_ROOTS)
            player.spirit_root_last_reroll_at = now
            session.add(player)
            session.commit()

        embed = discord.Embed(
            title="Spirit Root Reforged",
            description=f"Your spirit root is now **{player.spirit_root}**.",
            color=discord.Color.green(),
        )
        attach_guidance(embed, "reroll_root", player, session, cfg, now)
        logger.info(
            "Reroll complete guild=%s user=%s free_used=%s new_root=%r stones=%s",
            guild_id,
            discord_id,
            free_used,
            player.spirit_root,
            player.spirit_stones,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="reset", description="Reset your character (realm, qi, stones).")
@app_commands.choices(origin=ORIGIN_CHOICES)
@app_commands.describe(
    confirm="Set to true to confirm the reset.",
    dao_name="New dao name.",
    origin="New origin/background.",
)
async def reset_cmd(
    interaction: discord.Interaction,
    confirm: bool,
    dao_name: str,
    origin: app_commands.Choice[str],
):
    if not confirm:
        await interaction.response.send_message("Set `confirm=true` to proceed.", ephemeral=True)
        return

    cfg = get_config()
    session = get_session()
    try:
        logger.info(
            "CMD /reset begin %s dao=%r origin=%r",
            interaction_ctx(interaction),
            dao_name,
            origin.value,
        )
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        # Leave clan on reset; clear martial sect membership.
        if player.clan_id is not None:
            clan = session.get(Clan, player.clan_id)
            if clan is not None:
                clan.member_count = max(0, clan.member_count - 1)
                session.add(clan)
            player.clan_id = None
            player.clan_role = "member"
            player.clan_contribution_qi_total = 0

        player.game_sect_id = None
        player.sect_merit = 0
        player.sect_joined_at = None
        player.last_sect_task_date = None
        player.sect_daily_task_id = None
        player.sect_daily_task_progress = 0
        player.sect_daily_task_date = None
        player.sect_leave_cooldown_until = None

        # Reset progression.
        player.dao_name = dao_name
        player.origin = origin.value
        player.karma = 0
        player.moral_path = "neutral"
        player.realm_index = 0
        player.substage = 0
        player.qi = 0
        player.spirit_stones = 0
        player.stamina = 100
        player.stamina_last_updated_at = now
        player.last_cultivate_at = None
        player.last_gather_at = None
        player.last_hunt_at = None
        player.last_daily_at = None
        player.last_daily_streak_claimed_at = None
        player.last_pvp_at = None
        player.daily_streak = 0
        player.last_active_at = now
        player.spirit_root = random.choice(SPIRIT_ROOTS)
        player.spirit_root_reroll_free_used = False
        player.spirit_root_last_reroll_at = None
        player.novice_trial_step = 0
        player.novice_cultivates = 0
        player.adventures_completed = 0

        session.add(player)
        session.flush()
        apply_origin_starter_gifts(session, player)
        ensure_starter_techniques(session, player.id)
        session.commit()

        realm_role_note = ""
        if interaction.guild is not None and isinstance(interaction.user, discord.Member):
            _, role_err = await sync_member_realm_role(
                interaction.guild,
                interaction.user,
                player.realm_index,
            )
            if role_err:
                realm_role_note = f"\n\n{role_err}"

        logger.info(
            "Reset complete guild=%s user=%s new_root=%r new_realm=%s/%s qi=%s stones=%s",
            guild_id,
            discord_id,
            player.spirit_root,
            player.realm_index,
            player.substage,
            player.qi,
            player.spirit_stones,
        )

        embed = discord.Embed(
            title="Your Past is Rewritten",
            description=(
                f"You restart as `{dao_name}` with `{origin.value}`.\n"
                f"Spirit root: `{player.spirit_root}`.\n"
                f"Karma reset to **0**."
                f"{realm_role_note}"
            ),
            color=discord.Color.gold(),
        )
        attach_guidance(embed, "profile", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="profile", description="View your cultivation profile.")
async def profile_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /profile begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        logger.debug(
            "Profile pre qi=%s stones=%s stamina=%s last_active_at=%s realm=%s/%s",
            player.qi,
            player.spirit_stones,
            player.stamina,
            player.last_active_at,
            player.realm_index,
            player.substage,
        )
        apply_stamina_regen(player, now)
        mod = get_character_modifiers(session, player)
        offline_qi = apply_offline_progress(player, now, cfg.offline_cap_minutes, cap_mult=mod.offline_cap_mult)
        if offline_qi > 0:
            player.qi += offline_qi
            player.last_active_at = now

        player.last_active_at = now
        session.add(player)
        session.commit()
        mod = get_character_modifiers(session, player)

        embed = build_profile_embed(
            player,
            session,
            cfg,
            now,
            offline_qi=offline_qi,
            combat=compute_combat_stats(player, session, mod),
            realm_display=realm_display(player.realm_index, player.substage),
            remaining_fn=cooldown_remaining,
        )

        rng = rng_for(guild_id, discord_id)
        view = CultivateView(owner_discord_id=discord_id, cfg=cfg, rng=rng)
        attach_guidance(embed, "profile", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    finally:
        session.close()


async def shop_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is not None:
            options = list_affordable_shop_items(player)
        else:
            options = [
                (listing.shop_id, f"{listing.name} ({listing.price} stones)")
                for listing in list_shop_listings()
            ]
        return [
            app_commands.Choice(name=label[:100], value=value)
            for value, label in filter_options(options, current)
        ]
    finally:
        session.close()


def _choices_from_options(options: list[tuple[str, str]], current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=label[:100], value=value)
        for value, label in filter_options(options, current)
    ]


async def learn_manual_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_player_manuals(session, player.id), current)
    finally:
        session.close()


async def technique_inspect_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_technique_inspect_options(session, player.id), current)
    finally:
        session.close()


async def inventory_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_inventory_item_options(session, player.id), current)
    finally:
        session.close()


async def craft_pill_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_craftable_recipes(session, player.id, "pill"), current)
    finally:
        session.close()


async def craft_key_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_craftable_recipes(session, player.id, "key"), current)
    finally:
        session.close()


async def area_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_unlocked_areas(player), current)
    finally:
        session.close()


async def all_areas_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    options = [
        (area_id, f"{area.name} ({area.recommended_text})")
        for area_id, area in get_areas().items()
    ]
    return _choices_from_options(options, current)


async def dungeon_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_enterable_dungeons(session, player), current)
    finally:
        session.close()


async def forge_slot_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_forgeable_slots(session, player.id), current)
    finally:
        session.close()


async def affix_slot_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_affixable_slots(session, player.id), current)
    finally:
        session.close()


async def technique_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_equippable_techniques(session, player), current)
    finally:
        session.close()


async def technique_equip_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_technique_equip_options(session, player), current)
    finally:
        session.close()


async def technique_slot_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    technique_raw = getattr(interaction.namespace, "technique", None)
    if not technique_raw:
        options = [(slot, f"Slot {slot}" if slot != "passive" else "Passive") for slot in TECHNIQUE_SLOT_OPTIONS]
        return _choices_from_options(options, current)

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        technique_id = resolve_technique_id(str(technique_raw))
        if technique_id is None:
            return []
        return _choices_from_options(list_valid_slots_for_technique(session, player, technique_id), current)
    finally:
        session.close()


@bot.tree.command(name="shop", description="Buy pills, gear, and supplies with spirit stones.")
@app_commands.describe(
    item="Item to buy (leave empty to browse the catalog).",
    quantity="How many to buy (equipment is always 1).",
)
@app_commands.autocomplete(item=shop_item_autocomplete)
async def shop_cmd(
    interaction: discord.Interaction,
    item: str | None = None,
    quantity: app_commands.Range[int, 1, 99] = 1,
):
    cfg = get_config()
    if interaction.guild is None:
        await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
        return

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if item is None:
            embed = build_shop_embed(player)
            attach_guidance(embed, "shop", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        shop_id = resolve_shop_id(item) or item
        rng = rng_for(guild_id, discord_id)
        ok, message = buy_from_shop(session, player, shop_id, int(quantity), rng=rng)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        session.commit()
        embed = discord.Embed(
            title="Purchase complete",
            description=message,
            color=discord.Color.green(),
        )
        attach_guidance(embed, "shop", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="inventory", description="View your storage ring — item names grouped by type.")
async def inventory_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /inventory begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        load_item_catalog()
        stacks = get_player_inventory(session, player.id)
        embed = build_inventory_embed(player, stacks)
        attach_guidance(embed, "inventory", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="item", description="Inspect an item — effects, crafting uses, and how to obtain more.")
@app_commands.describe(name="Item from your inventory (autocomplete).")
@app_commands.autocomplete(name=inventory_item_autocomplete)
async def item_cmd(interaction: discord.Interaction, name: str):
    session = get_session()
    try:
        load_item_catalog()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        item_id = resolve_inventory_item_id(session, player.id, name)
        if item_id is None:
            await interaction.response.send_message(
                "That item is not in your bag. Check **`/inventory`** or pick from autocomplete.",
                ephemeral=True,
            )
            return

        embed = build_item_detail_embed(item_id, session=session, player_id=player.id)
        if embed is None:
            await interaction.response.send_message("Unknown item.", ephemeral=True)
            return

        cfg = get_config()
        attach_guidance(embed, "item", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(
    name="technique",
    description="Read what a martial art does — before or after you study its manual.",
)
@app_commands.describe(name="Learned art or manual in your bag (autocomplete).")
@app_commands.autocomplete(name=technique_inspect_autocomplete)
async def technique_cmd(interaction: discord.Interaction, name: str):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        tech, manual_item_id = resolve_technique_inspect_target(session, player.id, name)
        if tech is None:
            await interaction.response.send_message(
                "That art is not in your scripture yet. Pick a **learned** technique or a **manual in your bag**.",
                ephemeral=True,
            )
            return

        embed = build_technique_detail_embed(
            tech,
            session=session,
            player_id=player.id,
            manual_item_id=manual_item_id,
        )
        attach_guidance(embed, "technique", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="help", description="Learn how to play and see all commands.")
async def help_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        embed = build_help_embed()
        if player is not None:
            attach_guidance(embed, "help", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="post-tutorial", description="Post the full game tutorial to a channel (admin).")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    channel="Where to post (defaults to TUTORIAL_CHANNEL_ID in .env)",
    pin_intro="Pin the intro message at the top",
)
async def post_tutorial_cmd(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    pin_intro: bool = True,
):
    cfg = get_config()
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
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
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
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
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"I can't manage messages in {target.mention}. Check **Read Message History**, "
            "**Send Messages**, **Embed Links**, and **Pin Messages**.",
            ephemeral=True,
        )
    except Exception as exc:
        logger.exception("post-tutorial failed %s", interaction_ctx(interaction))
        await interaction.followup.send(f"Failed to post tutorial: {exc}", ephemeral=True)


@bot.tree.command(name="post-library", description="Post the technique manual library to a channel (admin).")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    channel="Where to post (defaults to LIBRARY_CHANNEL_ID in .env)",
    pin_intro="Pin the intro message at the top",
)
async def post_library_cmd(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
    pin_intro: bool = True,
):
    cfg = get_config()
    if interaction.guild is None:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
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
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
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
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"I can't manage messages in {target.mention}. Check **Read Message History**, "
            "**Send Messages**, **Embed Links**, and **Pin Messages**.",
            ephemeral=True,
        )
    except Exception as exc:
        logger.exception("post-library failed %s", interaction_ctx(interaction))
        await interaction.followup.send(f"Failed to post library: {exc}", ephemeral=True)


@bot.tree.command(name="cooldown", description="See which commands are ready and which are on timer.")
async def cooldown_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        embed = build_cooldown_embed(player, cfg, now, cooldown_remaining, session=session)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="remind", description="Get DM pings when cooldowns are ready (opt-in).")
@app_commands.describe(
    action="Show status, turn reminders on, or turn them off.",
    activity="Which timer to manage (required for on/off).",
)
@app_commands.choices(action=REMIND_ACTION_CHOICES, activity=REMIND_ACTIVITY_CHOICES)
async def remind_cmd(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    activity: app_commands.Choice[str] | None = None,
):
    cfg = get_config()
    if interaction.guild is None:
        await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
        return

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        if action.value == "status":
            description = build_reminder_status_text(session, player, cfg, now)
            embed = discord.Embed(
                title="Cooldown reminders",
                description=description,
                color=discord.Color.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if activity is None:
            await interaction.response.send_message(
                "Pick an **activity** when turning reminders on or off (or choose **All timers**).",
                ephemeral=True,
            )
            return

        if action.value == "on":
            if activity.value == "all":
                set_all_reminders_enabled(session, player, cfg, True, now)
                msg = "Reminders **on** for all timers. You'll get a DM when each is ready."
            else:
                set_reminder_enabled(session, player, cfg, activity.value, True, now)
                msg = f"Reminders **on** for **{ACTIVITY_LABELS[activity.value]}**."
        else:
            if activity.value == "all":
                set_all_reminders_enabled(session, player, cfg, False, now)
                msg = "Reminders **off** for all timers."
            else:
                set_reminder_enabled(session, player, cfg, activity.value, False, now)
                msg = f"Reminders **off** for **{ACTIVITY_LABELS[activity.value]}**."

        session.commit()
        embed = discord.Embed(
            title="Cooldown reminders",
            description=msg + "\n\n" + build_reminder_status_text(session, player, cfg, now),
            color=discord.Color.blurple(),
        )
        if player.remind_dms_blocked:
            embed.set_footer(
                text="Enable DMs from server members in Discord settings to receive pings."
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="cultivate", description="Cultivate qi to strengthen yourself.")
async def cultivate_cmd(interaction: discord.Interaction):
    # Uses the same logic as the button, but sends it non-UI.
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /cultivate begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        remaining = activity_cooldown_remaining(
            session,
            player,
            now,
            "cultivate",
            player.last_cultivate_at,
            cfg.cultivate_cooldown_seconds,
        )
        if remaining > 0:
            logger.debug(
                "Cooldown block /cultivate guild=%s user=%s remaining=%ss last_cultivate_at=%s",
                guild_id,
                discord_id,
                remaining,
                player.last_cultivate_at,
            )
            await interaction.response.send_message(
                f"You are not ready to cultivate yet. Wait {format_seconds(remaining)}.",
                ephemeral=True,
            )
            return

        logger.debug(
            "Pre-cultivate state qi=%s stamina=%s last_active_at=%s last_cultivate_at=%s realm=%s/%s",
            player.qi,
            player.stamina,
            player.last_active_at,
            player.last_cultivate_at,
            player.realm_index,
            player.substage,
        )
        apply_stamina_regen(player, now)
        mod = get_character_modifiers(session, player)
        passive_before = apply_offline_progress(
            player, now, cfg.offline_cap_minutes, cap_mult=mod.offline_cap_mult
        )
        if passive_before > 0:
            player.qi += passive_before
            player.last_active_at = now

        clan = None
        if player.clan_id is not None:
            clan = session.get(Clan, player.clan_id)

        rng = rng_for(guild_id, discord_id)
        res: CultivateResult = cultivate(
            player, clan, cfg, rng=rng, mod=mod, session=session, player_id=player.id
        )
        applied_drops = apply_cultivate_bonus_drops(session, player.id, res.bonus_drops or {})
        if "qi_gathering" in mod.active_effects:
            consume_effect_charge(session, player.id, "qi_gathering")
        consume_haste_for_activity(session, player.id, "cultivate")
        player.last_cultivate_at = now
        schedule_player_reminders(session, player, cfg, "cultivate", now=now)

        session.add(player)
        if clan is not None:
            session.add(clan)
        session.commit()

        logger.info(
            "Cultivate complete guild=%s user=%s qi_gain=%s stones_gain=%s new_qi=%s new_stamina=%s realm=%s/%s",
            guild_id,
            discord_id,
            res.qi_gain,
            res.stones_gain,
            player.qi,
            player.stamina,
            player.realm_index,
            player.substage,
        )

        embed = build_cultivate_embed(
            res,
            player,
            realm_display=realm_display(player.realm_index, player.substage),
            passive_qi=passive_before,
            applied_drops=applied_drops,
        )
        attach_guidance(embed, "cultivate", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="breakthrough", description="Attempt a breakthrough when your qi is sufficient.")
async def breakthrough_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /breakthrough begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        logger.debug(
            "Pre-breakthrough state qi=%s stamina=%s realm=%s/%s",
            player.qi,
            player.stamina,
            player.realm_index,
            player.substage,
        )
        apply_stamina_regen(player, now)
        mod = get_character_modifiers(session, player)
        offline_qi = apply_offline_progress(player, now, cfg.offline_cap_minutes, cap_mult=mod.offline_cap_mult)
        if offline_qi > 0:
            player.qi += offline_qi
            player.last_active_at = now
        player.last_active_at = now

        bt_preview = compute_breakthrough_preview(player, mod)
        old_realm_index = player.realm_index
        rng = rng_for(guild_id, discord_id)
        res: BreakthroughResult = breakthrough(player, cfg, rng=rng, mod=mod)
        _, enlighten_msg = roll_breakthrough_enlightenment(
            session, player, rng, success=res.success
        )
        trial_msgs: list[str] = []
        if res.success:
            trial_msgs = on_breakthrough_success(session, player, rng)
        if "clarity" in mod.active_effects and res.success:
            consume_effect_charge(session, player.id, "clarity")

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

        logger.info(
            "Breakthrough complete guild=%s user=%s success=%s qi=%s new_realm=%s/%s",
            guild_id,
            discord_id,
            res.success,
            player.qi,
            player.realm_index,
            player.substage,
        )

        color = discord.Color.green() if res.success else discord.Color.orange()
        if enlighten_msg:
            desc += f"\n\n{enlighten_msg}"
        if trial_msgs:
            desc += "\n\n" + "\n".join(trial_msgs)
        embed = discord.Embed(title="Breakthrough", description=desc, color=color)
        if bt_preview.can_attempt:
            embed.add_field(
                name="Attempted at",
                value=f"**{int(round(bt_preview.success_chance * 100))}%** success chance",
                inline=True,
            )
        else:
            embed.add_field(
                name="Breakthrough odds",
                value=format_breakthrough_chance_line(bt_preview, player),
                inline=False,
            )
        embed.add_field(name="Qi", value=str(player.qi), inline=True)
        embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)
        if enlighten_msg:
            embed.add_field(name="Enlightenment", value=enlighten_msg, inline=False)
        attach_guidance(embed, "breakthrough", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=True)
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


@bot.tree.command(name="daily", description="Claim today's cultivation stipend (UTC).")
async def daily_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /daily begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        # Offline Qi cap (daily still counts as an action).
        apply_stamina_regen(player, now)
        mod = get_character_modifiers(session, player)
        offline_qi = apply_offline_progress(player, now, cfg.offline_cap_minutes, cap_mult=mod.offline_cap_mult)
        if offline_qi > 0:
            player.qi += offline_qi
            player.last_active_at = now

        if player.last_daily_at is not None:
            # UTC day check (simplest MVP approach).
            last_daily = player.last_daily_at
            if last_daily.tzinfo is None:
                last_day = last_daily.date()
            else:
                last_day = last_daily.astimezone(timezone.utc).date()
            today = now.date()
            if last_day == today:
                logger.debug(
                    "/daily already claimed guild=%s user=%s last_daily_at=%s",
                    guild_id,
                    discord_id,
                    player.last_daily_at,
                )
                await interaction.response.send_message("You have already claimed today's stipend.", ephemeral=True)
                return

        # Streak logic: consecutive days increments; otherwise resets.
        if player.last_daily_at is None:
            player.daily_streak = 1
        else:
            last_daily = player.last_daily_at
            if last_daily.tzinfo is None:
                last_day = last_daily.date()
            else:
                last_day = last_daily.astimezone(timezone.utc).date()
            days_diff = (now.date() - last_day).days
            if days_diff == 1:
                player.daily_streak += 1
            else:
                player.daily_streak = 1

        stones, qi = compute_daily_rewards(player)
        logger.info(
            "Daily claimed guild=%s user=%s stones=%s qi=%s streak=%s realm=%s/%s",
            guild_id,
            discord_id,
            stones,
            qi,
            player.daily_streak,
            player.realm_index,
            player.substage,
        )
        player.spirit_stones += stones
        player.qi += qi
        player.last_daily_at = now
        player.last_active_at = now
        trial_msgs = on_daily_claimed(player)
        schedule_player_reminders(session, player, cfg, "daily", now=now)

        session.add(player)
        session.commit()

        embed = discord.Embed(
            title="Daily Stipend",
            description=(
                f"You accept the day's offerings.\n"
                f"+{stones} spirit stones, +{qi} qi."
                + (f"\n\n" + "\n".join(trial_msgs) if trial_msgs else "")
            ),
            color=discord.Color.purple(),
        )
        embed.add_field(name="Daily Streak", value=str(player.daily_streak), inline=True)
        attach_guidance(embed, "daily", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="leaderboard", description="View the realm leaderboard (server only).")
async def leaderboard_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /leaderboard begin %s", interaction_ctx(interaction))
        guild_id = get_guild_id(interaction)
        stmt = (
            select(Player)
            .where(Player.guild_id == guild_id)
            .order_by(Player.realm_index.desc(), Player.substage.desc(), Player.qi.desc())
            .limit(10)
        )
        players = list(session.execute(stmt).scalars().all())
        if not players:
            logger.debug("Leaderboard empty guild=%s", guild_id)
            await interaction.response.send_message("No cultivators yet. Someone run `/start`.", ephemeral=True)
            return

        lines = []
        for i, p in enumerate(players, start=1):
            lines.append(f"{i}. {p.dao_name} — {realm_display(p.realm_index, p.substage)} | Qi {p.qi}")

        embed = discord.Embed(title="Realm Leaderboard", description="\n".join(lines), color=discord.Color.teal())
        logger.info("Leaderboard guild=%s top_count=%s", guild_id, len(players))
        player = ensure_player(session, guild_id, get_discord_id(interaction.user))
        attach_guidance(embed, "leaderboard", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="duel", description="Challenge another daoist to a spar.")
@app_commands.describe(opponent="Another player who has used /start (not a bot).")
async def duel_cmd(interaction: discord.Interaction, opponent: discord.User):
    cfg = get_config()
    if interaction.guild is None:
        await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
        return

    if opponent.bot:
        await interaction.response.send_message("You cannot duel a bot.", ephemeral=True)
        return

    guild_id = get_guild_id(interaction)
    challenger_id = get_discord_id(interaction.user)
    opponent_id = get_discord_id(opponent)

    if challenger_id == opponent_id:
        await interaction.response.send_message("You cannot duel yourself.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    session = get_session()
    try:
        logger.info("CMD /duel challenge %s opponent=%s", interaction_ctx(interaction), opponent.id)

        challenger = ensure_player(session, guild_id, challenger_id)
        opponent_player = ensure_player(session, guild_id, opponent_id)
        if challenger is None or opponent_player is None:
            missing = []
            if challenger is None:
                missing.append("you")
            if opponent_player is None:
                missing.append(f"**{opponent.display_name}**")
            await interaction.followup.send(
                f"Both players need a character. Missing: {', '.join(missing)}. Use **`/start`** first.",
                ephemeral=True,
            )
            return

        challenge, err = create_duel_challenge(session, guild_id, challenger, opponent_player, cfg)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        assert challenge is not None
        session.commit()

        embed = build_duel_challenge_embed(challenger, opponent_player)
        view = DuelChallengeView(challenge.id, guild_id, challenger_id, opponent_id)
        public_msg = await interaction.channel.send(
            content=f"{opponent.mention} — **{challenger.dao_name}** challenges you to a duel!",
            embed=embed,
            view=view,
        )
        view.message = public_msg
        attach_challenge_message(session, challenge, str(public_msg.channel.id), str(public_msg.id))
        session.commit()

        await interaction.followup.send(
            f"Challenge sent to **{opponent_player.dao_name}** in this channel. "
            f"They have **2 minutes** to Accept or Decline.",
            ephemeral=True,
        )
    except Exception:
        logger.exception("CMD /duel failed %s", interaction_ctx(interaction))
        await interaction.followup.send(
            "The duel challenge could not be sent. Check bot logs or try again in a moment.",
            ephemeral=True,
        )
    finally:
        session.close()


def duel_from_players(
    challenger: Player,
    opponent_player: Player,
    cfg,
    rng: random.Random,
    challenger_mod=None,
    opponent_mod=None,
) -> DuelResult:
    from .game import duel

    return duel(challenger, opponent_player, cfg, rng=rng, challenger_mod=challenger_mod, opponent_mod=opponent_mod)


@bot.tree.command(name="areas", description="Compare adventure zones, loot, and realm requirements.")
@app_commands.describe(area="Optional: details for one area including rare events.")
@app_commands.autocomplete(area=all_areas_autocomplete)
async def areas_cmd(
    interaction: discord.Interaction,
    area: str | None = None,
):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)

        area_id = area if area in get_areas() else None
        embed = build_areas_embed(player, area_id=area_id)
        if player is not None:
            attach_guidance(embed, "areas", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="roots", description="Spirit root tier list, early vs late power, and stat bonuses.")
@app_commands.describe(root="Optional: details for one spirit root.")
@app_commands.choices(root=ROOT_CHOICES)
async def roots_cmd(
    interaction: discord.Interaction,
    root: app_commands.Choice[str] | None = None,
):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)

        root_name = root.value if root is not None else None
        embed = build_roots_embed(root_name=root_name)
        if player is not None and root_name is None:
            embed.add_field(
                name="Your root",
                value=f"**{player.spirit_root}** — use `/roots root:` for your match-up details.",
                inline=False,
            )
        if player is not None:
            attach_guidance(embed, "roots", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="gather", description="Harvest herbs and ore from a region (5 min cooldown).")
@app_commands.describe(area="Where to gather materials.")
@app_commands.autocomplete(area=area_autocomplete)
async def gather_cmd(interaction: discord.Interaction, area: str):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        remaining = activity_cooldown_remaining(
            session,
            player,
            now,
            "gather",
            player.last_gather_at,
            cfg.gather_cooldown_seconds,
        )
        if remaining > 0:
            haste = get_haste_reduction_seconds(session, player.id, "gather")
            extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
            await interaction.response.send_message(
                f"The soil needs time to recover. Wait {format_seconds(remaining)}.{extra}",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        res = run_gather(session, player, area, rng=rng)
        if not res.success:
            await interaction.response.send_message(res.messages[0], ephemeral=True)
            return

        player.last_gather_at = now
        player.last_active_at = now
        consume_haste_for_activity(session, player.id, "gather")
        schedule_player_reminders(session, player, cfg, "gather", now=now)
        session.add(player)
        session.commit()

        from .inventory import get_item_name

        drop_lines = [f"**{get_item_name(item_id)}** ×{qty}" for item_id, qty in res.drops.items()]
        embed = discord.Embed(
            title=f"Gather — {res.area_name}",
            description="\n".join(res.messages),
            color=discord.Color.green(),
        )
        if drop_lines:
            embed.add_field(name="Collected", value="\n".join(drop_lines), inline=False)
        attach_guidance(embed, "gather", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="hunt", description="Track and fight spirit beasts for cores and parts (5 min cooldown).")
@app_commands.describe(area="Where to hunt beasts.")
@app_commands.autocomplete(area=area_autocomplete)
async def hunt_cmd(interaction: discord.Interaction, area: str):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        remaining = activity_cooldown_remaining(
            session,
            player,
            now,
            "hunt",
            player.last_hunt_at,
            cfg.hunt_cooldown_seconds,
        )
        if remaining > 0:
            haste = get_haste_reduction_seconds(session, player.id, "hunt")
            extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
            await interaction.response.send_message(
                f"You must recover before hunting again. Wait {format_seconds(remaining)}.{extra}",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        start, err = start_hunt_combat(session, player, area, rng=rng)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        assert start is not None
        ensure_starter_techniques(session, player.id)
        techniques = get_equipped_active_techniques(session, player.id)
        session.commit()

        embed = build_hunt_combat_embed(start)
        view = CombatView(
            discord_id,
            guild_id,
            start.combat_id,
            "hunt",
            area_id=start.area_id,
            beast_id=start.beast_id,
            area_name=start.area_name,
            techniques=techniques,
            technique_cooldowns={},
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="techniques", description="View your martial build and manage manuals & loadout.")
async def techniques_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        ensure_starter_techniques(session, player.id)
        session.commit()
        embed = build_techniques_embed(session, player)
        view = TechniquesView(str(interaction.user.id), session, player)
        attach_guidance(embed, "techniques", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(
    name="equip-technique",
    description="Equip a learned art — active slots 1–4 (manual use) or passive slot (always on).",
)
@app_commands.describe(
    setup="Pick technique + slot from the list (recommended).",
    technique="Or pick a technique, then a slot.",
    slot="Active slots 1–4, or passive for always-on arts.",
)
@app_commands.autocomplete(setup=technique_equip_autocomplete, technique=technique_autocomplete, slot=technique_slot_autocomplete)
async def equip_technique_cmd(
    interaction: discord.Interaction,
    setup: str | None = None,
    technique: str | None = None,
    slot: str | None = None,
):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        technique_id: str | None = None
        slot_value: str | None = None

        if setup:
            if "|" not in setup:
                await interaction.response.send_message(
                    "Pick a full loadout option from the **`setup`** list.",
                    ephemeral=True,
                )
                return
            technique_id, slot_value = setup.split("|", 1)
        elif technique and slot:
            technique_id = resolve_technique_id(technique)
            slot_value = slot.lower()
        else:
            await interaction.response.send_message(
                "Use **`setup`** (recommended) or provide both **`technique`** and **`slot`**.",
                ephemeral=True,
            )
            return

        if technique_id is None:
            await interaction.response.send_message(
                "Pick a technique from the **`/equip-technique`** list.",
                ephemeral=True,
            )
            return

        if slot_value not in TECHNIQUE_SLOT_OPTIONS:
            await interaction.response.send_message("Slot must be 1–4 or passive.", ephemeral=True)
            return

        ok, message = equip_technique(session, player, technique_id, slot_value)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        session.commit()
        await interaction.response.send_message(message, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="learn", description="Study a technique manual from your inventory.")
@app_commands.describe(manual="Pick a manual you carry.")
@app_commands.autocomplete(manual=learn_manual_autocomplete)
async def learn_cmd(interaction: discord.Interaction, manual: str):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        ok, message = learn_technique_from_manual(session, player.id, manual)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        session.commit()
        await interaction.response.send_message(message, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="adventure", description="Start an interactive adventure with choices.")
@app_commands.choices(stance=STANCE_CHOICES)
@app_commands.describe(
    area="Where to explore.",
    stance="How boldly you press forward. Risky choices can boost loot or fail the run.",
)
@app_commands.autocomplete(area=area_autocomplete)
async def adventure_cmd(
    interaction: discord.Interaction,
    area: str,
    stance: app_commands.Choice[str],
):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        remaining = activity_cooldown_remaining(
            session,
            player,
            now,
            "adventure",
            player.last_adventure_at,
            cfg.adventure_cooldown_seconds,
        )
        if remaining > 0:
            haste = get_haste_reduction_seconds(session, player.id, "adventure")
            extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
            await interaction.response.send_message(
                f"You need to recover before another adventure. Wait {format_seconds(remaining)}.{extra}",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        pending, err = start_adventure_session(session, player, area, stance.value, rng=rng)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        assert pending is not None
        session.commit()

        if pending.encounter_type == "combat" and pending.combat_id:
            from .combat.session import get_active_combat, load_combat_state

            ensure_starter_techniques(session, player.id)
            techniques = get_equipped_active_techniques(session, player.id)
            active_combat = get_active_combat(session, player.id)
            combat_state = load_combat_state(active_combat) if active_combat else None
            embed = (
                build_adventure_combat_embed(pending, combat_state)
                if combat_state
                else build_adventure_embed_from_pending(pending)
            )
            view = CombatView(
                discord_id,
                guild_id,
                pending.combat_id,
                "adventure",
                area_id=area,
                active_id=pending.active_id,
                area_name=pending.area_name,
                techniques=techniques,
                technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                player_sealed=combat_state.player.sealed if combat_state else False,
            )
        else:
            embed = build_adventure_embed_from_pending(pending)
            view = AdventureChoiceView(discord_id, guild_id, pending.active_id, pending.choices)
        attach_guidance(embed, "adventure", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="adventure-continue", description="Resume a paused adventure and pick up where you left off.")
async def adventure_continue_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        pending, err = resume_adventure_session(session, player)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        assert pending is not None
        if pending.encounter_type == "combat" and pending.combat_id:
            from .combat.session import get_active_combat, load_combat_state

            ensure_starter_techniques(session, player.id)
            techniques = get_equipped_active_techniques(session, player.id)
            active_row = get_active_adventure(session, player.id)
            area_id = active_row.area_id if active_row else ""
            active_combat = get_active_combat(session, player.id)
            combat_state = load_combat_state(active_combat) if active_combat else None
            embed = (
                build_adventure_combat_embed(pending, combat_state)
                if combat_state
                else build_adventure_embed_from_pending(pending)
            )
            view = CombatView(
                discord_id,
                guild_id,
                pending.combat_id,
                "adventure",
                area_id=area_id,
                active_id=pending.active_id,
                area_name=pending.area_name,
                techniques=techniques,
                technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                player_sealed=combat_state.player.sealed if combat_state else False,
            )
        else:
            embed = build_adventure_embed_from_pending(pending)
            view = AdventureChoiceView(discord_id, guild_id, pending.active_id, pending.choices)
        attach_guidance(embed, "adventure", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="adventure-abandon", description="Withdraw from your current adventure without rewards.")
async def adventure_abandon_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        ok, message = abandon_adventure(session, player.id)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        session.commit()
        await interaction.response.send_message(message, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="recipes", description="View craft recipes, pill effects, and forge costs.")
@app_commands.describe(category="Filter by pills, keys, or forging.")
@app_commands.choices(
    category=[
        app_commands.Choice(name="All", value="all"),
        app_commands.Choice(name="Pills", value="pill"),
        app_commands.Choice(name="Keys", value="key"),
        app_commands.Choice(name="Forging", value="forge"),
    ]
)
async def recipes_cmd(
    interaction: discord.Interaction,
    category: app_commands.Choice[str] | None = None,
):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)

        cat = category.value if category is not None else None
        recipe_type = None if cat in (None, "all") else cat
        embed = build_recipes_embed(recipe_type=recipe_type)
        if player is not None:
            attach_guidance(embed, "recipes", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="forge", description="Forge equipment for a slot using adventure materials.")
@app_commands.describe(slot="Pick a slot you can forge right now.")
@app_commands.autocomplete(slot=forge_slot_autocomplete)
async def forge_cmd(interaction: discord.Interaction, slot: str):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        resolved_slot = resolve_forge_slot(slot)
        if resolved_slot is None:
            await interaction.response.send_message(
                "Pick a forge option from the **`/forge`** list — you need the listed materials.",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        res = forge_equipment(session, player.id, resolved_slot, rng=rng)
        if not res.success:
            await interaction.response.send_message(res.message, ephemeral=True)
            return

        session.commit()
        embed = discord.Embed(title="Forging Complete", description=res.message, color=discord.Color.gold())
        attach_guidance(embed, "forge", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="stats", description="View forged equipment stats and how they affect your dao.")
async def stats_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        mod = get_character_modifiers(session, player)
        summary = format_stats_summary(session, player.id, player=player, mod=mod)
        embed = discord.Embed(
            title=f"{player.dao_name} — Stats",
            description=summary,
            color=discord.Color.dark_purple(),
        )
        embed.add_field(name="Adventure Success", value=f"+{mod.adventure_success:.2f}", inline=True)
        embed.add_field(name="Adventure Defense", value=f"+{mod.adventure_defense:.2f}", inline=True)
        embed.add_field(name="Drop Luck", value=f"+{mod.drop_luck:.2f}", inline=True)
        embed.add_field(name="Rare Events", value=f"×{mod.rare_event_mult:.2f}", inline=True)
        embed.add_field(name="PvP Power", value=f"+{mod.pvp_power:.2f}", inline=True)
        embed.add_field(name="Dungeon Damage", value=f"+{mod.dungeon_damage:.2f}", inline=True)
        attach_guidance(embed, "stats", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


craft_group = app_commands.Group(name="craft", description="Craft pills, keys, and technique manuals.")


@craft_group.command(name="pill", description="Craft a pill from materials.")
@app_commands.describe(recipe="Pick a recipe you can brew now.", amount="How many to attempt (1-10).")
@app_commands.autocomplete(recipe=craft_pill_autocomplete)
async def craft_pill_cmd(
    interaction: discord.Interaction,
    recipe: str,
    amount: app_commands.Range[int, 1, 10] = 1,
):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if get_recipes().get(recipe) is None:
            await interaction.response.send_message(
                "Pick a pill recipe from the **`/craft pill`** list.",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        res = craft_recipe(session, player, recipe, amount=amount, rng=rng)
        session.add(player)
        session.commit()

        color = discord.Color.green() if res.success else discord.Color.orange()
        embed = discord.Embed(title="Alchemy", description=res.message, color=color)
        if res.crafted:
            from .inventory import get_item_name

            lines = [f"{get_item_name(k)} ×{v}" for k, v in res.crafted.items()]
            embed.add_field(name="Crafted", value="\n".join(lines), inline=False)
        attach_guidance(embed, "craft_pill", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@craft_group.command(name="key", description="Craft a dungeon key.")
@app_commands.describe(recipe="Pick a key recipe you can forge now.")
@app_commands.autocomplete(recipe=craft_key_autocomplete)
async def craft_key_cmd(interaction: discord.Interaction, recipe: str):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if get_recipes().get(recipe) is None:
            await interaction.response.send_message(
                "Pick a key recipe from the **`/craft key`** list.",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        res = craft_recipe(session, player, recipe, amount=1, rng=rng)
        session.add(player)
        session.commit()

        color = discord.Color.green() if res.success else discord.Color.red()
        embed = discord.Embed(title="Key Forging", description=res.message, color=color)
        attach_guidance(embed, "craft_key", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@craft_group.command(name="manual", description="Bind technique fragments into a manual.")
async def craft_manual_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if not can_bind_technique_manual(session, player.id):
            from .manuals import MANUAL_CRAFT_INPUTS
            from .drop_sources import format_missing_materials_message

            await interaction.response.send_message(
                format_missing_materials_message(session, player.id, MANUAL_CRAFT_INPUTS, action="manual"),
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        res = craft_manual_from_fragments(session, player, rng=rng)
        session.add(player)
        session.commit()

        color = discord.Color.green() if res.success else discord.Color.orange()
        embed = discord.Embed(title="Manual Binding", description=res.message, color=color)
        if res.crafted:
            from .inventory import get_item_name

            lines = [f"{get_item_name(k)} ×{v}" for k, v in res.crafted.items()]
            embed.add_field(name="Bound", value="\n".join(lines), inline=False)
        attach_guidance(embed, "craft_manual", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


bot.tree.add_command(craft_group)


@bot.tree.command(name="dungeon", description="Enter a key-gated dungeon.")
@app_commands.describe(name="Pick a dungeon you can enter now.", mode="Solo for now.")
@app_commands.autocomplete(name=dungeon_autocomplete)
async def dungeon_cmd(
    interaction: discord.Interaction,
    name: str,
    mode: str = "solo",
):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        remaining = activity_cooldown_remaining(
            session,
            player,
            now,
            "dungeon",
            player.last_dungeon_at,
            cfg.dungeon_cooldown_seconds,
        )
        if remaining > 0:
            await interaction.response.send_message(
                f"The dungeon gate is sealed. Wait {format_seconds(remaining)}.",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        res = run_dungeon(session, player, name, mode=mode, rng=rng)
        if res.outcome in {"invalid", "underleveled", "no_key"}:
            await interaction.response.send_message(res.messages[0], ephemeral=True)
            return

        player.last_dungeon_at = now
        player.last_active_at = now
        consume_haste_for_activity(session, player.id, "dungeon")
        schedule_player_reminders(session, player, cfg, "dungeon", now=now)
        session.add(player)
        session.commit()

        color = discord.Color.gold() if res.success else discord.Color.dark_red()
        embed = discord.Embed(
            title=f"Dungeon — {res.dungeon_name}",
            description="\n".join(res.messages),
            color=color,
        )
        embed.add_field(name="Outcome", value=res.outcome.title(), inline=True)
        embed.add_field(name="Qi", value=str(player.qi), inline=True)
        attach_guidance(embed, "dungeon", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="equip", description="Apply an Affix Stone to an equipment slot.")
@app_commands.describe(slot="Pick equipped gear with an Affix Stone ready.")
@app_commands.autocomplete(slot=affix_slot_autocomplete)
async def equip_cmd(interaction: discord.Interaction, slot: str):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        slot = slot.lower()
        if slot not in {"weapon", "armor", "accessory", "talisman"}:
            await interaction.response.send_message(
                "Pick a slot from the **`/equip`** list.",
                ephemeral=True,
            )
            return

        rng = rng_for(guild_id, discord_id)
        ok, message, _affix = apply_affix_stone(session, player.id, slot, rng=rng)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        session.commit()
        embed = discord.Embed(title="Equipment Affixed", description=message, color=discord.Color.blue())
        attach_guidance(embed, "equip", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="loadout", description="View equipment affixes and derived modifiers.")
async def loadout_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        mod = get_character_modifiers(session, player)
        equipment_text = format_loadout(session, player.id)

        embed = discord.Embed(title=f"{player.dao_name} — Loadout", description=equipment_text, color=discord.Color.dark_blue())
        embed.add_field(name="Cultivate Qi", value=f"×{mod.cultivate_qi_mult:.2f}", inline=True)
        embed.add_field(name="Breakthrough Stability", value=f"+{mod.breakthrough_stability:.2f}", inline=True)
        embed.add_field(name="Adventure Success", value=f"+{mod.adventure_success:.2f}", inline=True)
        embed.add_field(name="Drop Luck", value=f"+{mod.drop_luck:.2f}", inline=True)
        embed.add_field(name="Dungeon Damage", value=f"+{mod.dungeon_damage:.2f}", inline=True)
        embed.add_field(name="PvP Power", value=f"+{mod.pvp_power:.2f}", inline=True)
        if mod.active_effects:
            embed.add_field(name="Active Effects", value=", ".join(mod.active_effects), inline=False)
        attach_guidance(embed, "loadout", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="use", description="Consume a pill or special item from your inventory.")
@app_commands.describe(item="Pick from the list, or type a pill name (e.g. Qi Gathering Pill).")
async def use_cmd(interaction: discord.Interaction, item: str):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        rng = rng_for(guild_id, discord_id)
        ok, message = use_item(session, player, item, rng=rng)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        session.add(player)
        session.commit()
        embed = discord.Embed(title="Item Used", description=message, color=discord.Color.green())
        attach_guidance(embed, "use", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@use_cmd.autocomplete("item")
async def use_item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    from .consumables import list_usable_inventory
    from .inventory import get_item_name

    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []

        current_lower = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for item_id, qty in list_usable_inventory(session, player.id):
            name = get_item_name(item_id)
            if current_lower and current_lower not in name.lower() and current_lower not in item_id:
                continue
            label = f"{name} (×{qty})"
            choices.append(app_commands.Choice(name=label[:100], value=item_id))
            if len(choices) >= 25:
                break

        return choices
    finally:
        session.close()


# Clan commands (player guilds)


@bot.tree.command(name="clan-create", description="Create a player clan in this server.")
async def clan_create_cmd(interaction: discord.Interaction, name: str):
    session = get_session()
    try:
        logger.info("CMD /clan-create begin %s name=%r", interaction_ctx(interaction), name)
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.clan_id is not None:
            await interaction.response.send_message(
                "You are already in a clan. Use `/clan-leave` first.", ephemeral=True
            )
            return

        name = name.strip()
        if not name:
            await interaction.response.send_message("Clan name cannot be empty.", ephemeral=True)
            return

        existing = get_clan_by_name_lookup(session, guild_id, name)
        if existing is not None:
            await interaction.response.send_message("A clan with that name already exists.", ephemeral=True)
            return

        clan = Clan(
            guild_id=guild_id,
            name=name,
            created_by_discord_id=discord_id,
            clan_qi_contributed=0,
            member_count=1,
        )
        session.add(clan)
        session.flush()

        player.clan_id = clan.id
        player.clan_role = "founder"
        player.clan_contribution_qi_total = 0
        session.add(player)
        session.commit()
        logger.info(
            "Clan created guild=%s founder=%s clan_id=%s clan_name=%r member_count=%s",
            guild_id,
            discord_id,
            clan.id,
            name,
            clan.member_count,
        )

        embed = discord.Embed(
            title="New Clan Formed",
            description=f"The clan `{name}` opens its banner. You become its founder.",
            color=discord.Color.dark_gold(),
        )
        attach_guidance(embed, "clan-create", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="clan-join", description="Join an existing player clan by name.")
async def clan_join_cmd(interaction: discord.Interaction, name: str):
    session = get_session()
    try:
        logger.info("CMD /clan-join begin %s name=%r", interaction_ctx(interaction), name)
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.clan_id is not None:
            await interaction.response.send_message(
                "You are already in a clan. Use `/clan-leave` first.", ephemeral=True
            )
            return

        name = name.strip()
        if not name:
            await interaction.response.send_message("Clan name cannot be empty.", ephemeral=True)
            return

        clan = get_clan_by_name_lookup(session, guild_id, name)
        if clan is None:
            await interaction.response.send_message("That clan does not exist.", ephemeral=True)
            return

        ok, reason = can_join_clan(session, player, clan)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        player.clan_id = clan.id
        player.clan_role = "member"
        player.clan_contribution_qi_total = 0
        clan.member_count += 1
        consume_clan_invitation(session, guild_id, discord_id, clan.id)

        session.add(player)
        session.add(clan)
        session.commit()
        logger.info(
            "Clan joined guild=%s user=%s clan_id=%s clan_name=%r new_member_count=%s",
            guild_id,
            discord_id,
            clan.id,
            clan.name,
            clan.member_count,
        )

        embed = discord.Embed(
            title="You Join a Clan",
            description=(
                f"You rally under `{clan.name}`. "
                "Your cultivation will contribute qi to the clan total."
            ),
            color=discord.Color.green(),
        )
        attach_guidance(embed, "clan-join", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="clan-leave", description="Leave your current player clan.")
async def clan_leave_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        logger.info("CMD /clan-leave begin %s", interaction_ctx(interaction))
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.clan_id is None:
            await interaction.response.send_message("You are not in a clan.", ephemeral=True)
            return

        clan = session.get(Clan, player.clan_id)
        if clan is not None:
            clan.member_count = max(0, clan.member_count - 1)
            session.add(clan)

        player.clan_id = None
        player.clan_role = "member"
        player.clan_contribution_qi_total = 0

        session.add(player)
        session.commit()
        logger.info("Clan left guild=%s user=%s", guild_id, discord_id)

        embed = discord.Embed(
            title="You Leave the Clan",
            description="You lower the banner. The path ahead is yours alone.",
            color=discord.Color.orange(),
        )
        attach_guidance(embed, "clan-leave", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="clan", description="View your player clan details.")
async def clan_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /clan begin %s", interaction_ctx(interaction))
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.clan_id is None:
            await interaction.response.send_message(
                "You are not in a clan. Use `/clan-create` or `/clan-join`.", ephemeral=True
            )
            return

        clan = session.get(Clan, player.clan_id)
        if clan is None:
            await interaction.response.send_message("Your clan record was not found.", ephemeral=True)
            return

        members = get_clan_top_contributors(session, guild_id, clan.id)
        lines = [f"{p.dao_name}: {p.clan_contribution_qi_total} qi" for p in members]

        embed = discord.Embed(
            title=f"Clan: {clan.name}",
            description=(
                f"Members: {clan.member_count}\n"
                f"Total contributed qi: {clan.clan_qi_contributed}\n"
                f"Join policy: **{'invite only' if clan.invite_only else 'open'}**"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Top Contributors", value="\n".join(lines) if lines else "None yet.", inline=False)
        attach_guidance(embed, "clan", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="clan-invite", description="Invite a cultivator to your clan (founder only).")
@app_commands.describe(member="The player to invite to your clan.")
async def clan_invite_cmd(interaction: discord.Interaction, member: discord.Member):
    session = get_session()
    try:
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.clan_id is None or player.clan_role != "founder":
            await interaction.response.send_message(
                "Only a **clan founder** can send invitations.", ephemeral=True
            )
            return

        clan = session.get(Clan, player.clan_id)
        if clan is None:
            await interaction.response.send_message("Your clan record was not found.", ephemeral=True)
            return

        if member.bot:
            await interaction.response.send_message("You cannot invite bots.", ephemeral=True)
            return

        invitee_id = str(member.id)
        ok, msg = create_clan_invitation(
            session,
            clan=clan,
            invitee_discord_id=invitee_id,
            invited_by_discord_id=discord_id,
        )
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        session.commit()
        embed = discord.Embed(
            title="Clan Invitation Sent",
            description=f"{msg}\nThey may join with **`/clan-join {clan.name}`**.",
            color=discord.Color.green(),
        )
        attach_guidance(embed, "clan-invite", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="clan-invites", description="View pending clan invitations for you.")
async def clan_invites_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        rows = list_clan_invitations_for_player(session, guild_id, discord_id)
        if not rows:
            await interaction.response.send_message(
                "You have no pending clan invitations.", ephemeral=True
            )
            return

        lines = [f"• **{clan.name}** — `/clan-join {clan.name}`" for _, clan in rows]
        embed = discord.Embed(
            title="Pending Clan Invitations",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        attach_guidance(embed, "clan-invites", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="clan-invite-only", description="Toggle whether your clan requires invitations (founder).")
@app_commands.describe(enabled="True = invite only; False = open join.")
async def clan_invite_only_cmd(interaction: discord.Interaction, enabled: bool):
    session = get_session()
    try:
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.clan_id is None or player.clan_role != "founder":
            await interaction.response.send_message(
                "Only a **clan founder** can change join policy.", ephemeral=True
            )
            return

        clan = session.get(Clan, player.clan_id)
        if clan is None:
            await interaction.response.send_message("Your clan record was not found.", ephemeral=True)
            return

        msg = set_clan_invite_only(session, clan, enabled)
        session.commit()
        await interaction.response.send_message(msg, ephemeral=True)
    finally:
        session.close()


# Martial sect commands (fixed in-world factions)


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


@bot.tree.command(name="sect-list", description="View martial sects you may join.")
async def sect_list_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
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


@bot.tree.command(name="sect", description="View your martial sect membership.")
async def sect_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
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


@bot.tree.command(name="sect-join", description="Join a martial sect (Wudang, Shaolin, Tang, etc.).")
@app_commands.describe(sect="Sect id from /sect-list (e.g. wudang, shaolin)")
@app_commands.autocomplete(sect=game_sect_autocomplete)
async def game_sect_join_cmd(interaction: discord.Interaction, sect: str):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        sect_id = sect.strip().lower()
        ok, msg = join_game_sect(session, player, sect_id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
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


@bot.tree.command(name="sect-leave", description="Leave your martial sect.")
async def game_sect_leave_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        ok, msg, _ = leave_game_sect(session, player)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
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


async def sect_shop_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        from .game_sects import list_sect_shop_entries
        from .inventory import get_item_name

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


@bot.tree.command(name="sect-task", description="View your daily martial sect assignment and progress.")
async def sect_task_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.game_sect_id is None:
            await interaction.response.send_message(
                "You belong to no martial sect. Use **`/sect-list`** and **`/sect-join`**.",
                ephemeral=True,
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
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="sect-shop", description="Browse your martial sect's merit shop (Common–Uncommon manuals).")
async def sect_shop_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.game_sect_id is None:
            await interaction.response.send_message(
                "Join a martial sect first (`/sect-join`).", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Sect Merit Shop",
            description=format_sect_shop_listing(player),
            color=discord.Color.gold(),
        )
        attach_guidance(embed, "sect-shop", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


@bot.tree.command(name="sect-buy", description="Buy a manual from your sect shop with merit.")
@app_commands.describe(item="Manual from /sect-shop (autocomplete).")
@app_commands.autocomplete(item=sect_shop_item_autocomplete)
async def sect_buy_cmd(interaction: discord.Interaction, item: str):
    session = get_session()
    try:
        cfg = get_config()
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        ok, msg = buy_from_sect_shop(session, player, item.strip())
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
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
    init_db()
    load_all_content()
    load_item_catalog()

    print("Bot ready. Starting event loop...")
    await bot.start(cfg.discord_token)


if __name__ == "__main__":
    asyncio.run(main())

