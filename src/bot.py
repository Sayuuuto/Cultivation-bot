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
from .content import get_areas, get_dungeons, get_recipes, load_all_content
from .character import get_character_modifiers
from .areas_info import build_areas_embed
from .adventure import (
    STANCES,
    AdventureResult,
    PendingAdventure,
    abandon_adventure,
    apply_adventure_choice,
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
from .stats import format_stats_summary
from .shop import build_shop_embed, buy_from_shop, list_shop_listings, load_shop_catalog, resolve_shop_id
from .cultivation_preview import add_cultivation_preview_fields, format_breakthrough_chance_line
from .crafting import craft_recipe, list_pill_recipes
from .dungeon import run_dungeon
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
    moral_breakthrough_setback_text,
    moral_breakthrough_modifiers,
    compute_breakthrough_preview,
    player_strength_for_pvp,
)
from .models import PendingDuel, Player, Sect
from .inventory import format_inventory_embed, get_player_inventory, load_item_catalog

load_all_content()
load_item_catalog()
load_shop_catalog()

AREA_CHOICES = [
    app_commands.Choice(name=data.name, value=area_id)
    for area_id, data in get_areas().items()
]
STANCE_CHOICES = [app_commands.Choice(name=s.title(), value=s) for s in STANCES]
PILL_RECIPE_CHOICES = [
    app_commands.Choice(name=r.name, value=r.recipe_id)
    for r in list_pill_recipes()
]
DUNGEON_CHOICES = [
    app_commands.Choice(name=d.name, value=dungeon_id)
    for dungeon_id, d in get_dungeons().items()
]
KEY_RECIPE_CHOICES = [
    app_commands.Choice(name=r.name, value=r.recipe_id)
    for r in get_recipes().values()
    if r.recipe_type == "key"
]
EQUIP_SLOT_CHOICES = [
    app_commands.Choice(name="Weapon", value="weapon"),
    app_commands.Choice(name="Armor", value="armor"),
    app_commands.Choice(name="Accessory", value="accessory"),
    app_commands.Choice(name="Talisman", value="talisman"),
]
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


def build_adventure_embed_from_pending(pending: PendingAdventure) -> discord.Embed:
    recent = pending.messages[-3:] if pending.messages else []
    description = "\n".join(recent) if recent else pending.prompt
    embed = discord.Embed(
        title=f"Adventure — {pending.area_name}",
        description=description,
        color=discord.Color.dark_green(),
    )
    embed.add_field(
        name=f"Segment {pending.segment}/{pending.segments_total}",
        value=pending.prompt,
        inline=False,
    )
    embed.set_footer(text="Choose wisely — risky options can fail the run or boost loot.")
    return embed


def build_adventure_embed_from_result(res: AdventureResult, qi: int) -> discord.Embed:
    color = discord.Color.green() if res.outcome == "success" else discord.Color.orange()
    if res.failed_run:
        color = discord.Color.red()
    embed = discord.Embed(
        title=f"Adventure — {res.area_name}",
        description="\n".join(res.messages),
        color=color,
    )
    embed.add_field(name="Outcome", value=res.outcome.title(), inline=True)
    embed.add_field(name="Segments", value=f"{res.segments_cleared}/{2}", inline=True)
    embed.add_field(name="Qi", value=str(qi), inline=True)
    return embed


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
                    embed = build_adventure_embed_from_pending(result)
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
                player.last_adventure_at = now
                player.last_active_at = now
                consume_haste_for_activity(session, player.id, "adventure")
                schedule_player_reminders(session, player, cfg, "adventure", now=now)
                session.add(player)
                session.commit()

                embed = build_adventure_embed_from_result(result, player.qi)
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


def get_or_create_sect(session: Session, guild_id: str, name: str) -> Sect | None:
    stmt = select(Sect).where(Sect.guild_id == guild_id, Sect.name == name)
    return session.execute(stmt).scalar_one_or_none()


def ensure_loaded_player(player: Player) -> None:
    # No-op; placeholder for future initialization.
    _ = player


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
        super().__init__(label="Cultivate", style=discord.ButtonStyle.primary)
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

            sect = None
            if player.sect_id is not None:
                sect = session.get(Sect, player.sect_id)

            res: CultivateResult = cultivate(player, sect, self.cfg, rng=self.rng, mod=mod)
            if "qi_gathering" in mod.active_effects:
                consume_effect_charge(session, player.id, "qi_gathering")
            consume_haste_for_activity(session, player.id, "cultivate")
            player.last_cultivate_at = now
            schedule_player_reminders(session, player, self.cfg, "cultivate", now=now)

            session.add(player)
            if sect is not None:
                session.add(sect)
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

            embed = discord.Embed(title="Cultivation Complete", description=res.message, color=discord.Color.green())
            embed.add_field(name="Qi", value=str(res.new_qi), inline=True)
            if passive_before > 0:
                embed.add_field(
                    name="Qi breakdown",
                    value=(
                        f"**+{passive_before + res.qi_gain}** total "
                        f"(**+{res.qi_gain}** active · **+{passive_before}** passive)"
                    ),
                    inline=False,
                )
            embed.add_field(name="Spirit Stones", value=str(player.spirit_stones), inline=True)
            embed.add_field(name="Stamina", value=f"{player.stamina}/100", inline=True)
            embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)

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
            embed = discord.Embed(title="Breakthrough", description=res.message, color=color)
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
MORAL_CHOICES = [
    app_commands.Choice(name="Righteous", value="righteous"),
    app_commands.Choice(name="Demonic", value="demonic"),
    app_commands.Choice(name="Neutral", value="neutral"),
]


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
                print(f"Slash commands synced to guild ({len(synced)} commands, {command_count} in tree).")
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


@bot.tree.command(name="start", description="Begin your cultivation path.")
@app_commands.choices(origin=ORIGIN_CHOICES, moral_path=MORAL_CHOICES)
@app_commands.describe(
    dao_name="Your dao name (as you wish the world to remember).",
    origin="Your origin/background.",
    moral_path="Your moral path (affects breakthrough outcomes).",
)
async def start_cmd(
    interaction: discord.Interaction,
    dao_name: str,
    origin: app_commands.Choice[str],
    moral_path: app_commands.Choice[str],
):
    cfg = get_config()
    session = get_session()
    try:
        logger.info(
            "CMD /start begin %s dao=%r origin=%r moral=%r",
            interaction_ctx(interaction),
            dao_name,
            origin.value,
            moral_path.value,
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
            moral_path=moral_path.value,
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
            sect_id=None,
            sect_role="member",
            sect_contribution_qi_total=0,
        )
        session.add(player)
        session.commit()
        logger.debug(
            "Created player id=%s guild_id=%s discord_id=%s dao=%r root=%r moral=%r",
            player.id,
            guild_id,
            discord_id,
            dao_name,
            root,
            player.moral_path,
        )

        embed = discord.Embed(
            title="Your Cultivation Begins",
            description=(
                f"{get_welcome_intro()}\n\n"
                f"You name yourself **{dao_name}**. The past recedes: **{origin.value}**.\n"
                f"Your moral path: **{moral_path.value}**.\n"
                f"Your spirit root is revealed: **{root}**."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)
        embed.add_field(name="Qi", value=str(player.qi), inline=True)
        embed.add_field(name="Spirit Stones", value=str(player.spirit_stones), inline=True)
        embed.add_field(name="Stamina", value=f"{player.stamina}/100", inline=True)
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
@app_commands.describe(
    confirm="Set to true to confirm the reset.",
    dao_name="New dao name.",
    origin="New origin/background.",
    moral_path="New moral path.",
)
async def reset_cmd(
    interaction: discord.Interaction,
    confirm: bool,
    dao_name: str,
    origin: app_commands.Choice[str],
    moral_path: app_commands.Choice[str],
):
    if not confirm:
        await interaction.response.send_message("Set `confirm=true` to proceed.", ephemeral=True)
        return

    cfg = get_config()
    session = get_session()
    try:
        logger.info(
            "CMD /reset begin %s dao=%r origin=%r moral=%r",
            interaction_ctx(interaction),
            dao_name,
            origin.value,
            moral_path.value,
        )
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        now = utcnow()
        # If in a sect, update counts in MVP minimally (no complex roster rebuild).
        if player.sect_id is not None:
            sect = session.get(Sect, player.sect_id)
            if sect is not None:
                sect.member_count = max(0, sect.member_count - 1)
                session.add(sect)
            player.sect_id = None
            player.sect_role = "member"
            player.sect_contribution_qi_total = 0

        # Reset progression.
        player.dao_name = dao_name
        player.origin = origin.value
        player.moral_path = moral_path.value
        player.realm_index = 0
        player.substage = 0
        player.qi = 0
        player.spirit_stones = 0
        player.stamina = 100
        player.stamina_last_updated_at = now
        player.last_cultivate_at = None
        player.last_daily_at = None
        player.last_daily_streak_claimed_at = None
        player.last_pvp_at = None
        player.daily_streak = 0
        player.last_active_at = now
        player.spirit_root = random.choice(SPIRIT_ROOTS)
        player.spirit_root_reroll_free_used = False
        player.spirit_root_last_reroll_at = None

        session.add(player)
        session.commit()
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
                f"Moral path: `{moral_path.value}`."
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

        embed = discord.Embed(title=f"{player.dao_name} - Profile", color=discord.Color.blue())
        embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)
        embed.add_field(name="Spirit Stones", value=str(player.spirit_stones), inline=True)
        embed.add_field(name="Stamina", value=f"{player.stamina}/100", inline=True)
        embed.add_field(name="Spirit Root", value=player.spirit_root, inline=False)
        embed.add_field(name="Moral Path", value=player.moral_path.title(), inline=False)
        embed.add_field(name="Daily Streak", value=str(player.daily_streak), inline=True)
        if offline_qi > 0:
            embed.add_field(
                name="Applied just now",
                value=f"**+{offline_qi} passive Qi** was added to your pool from time away.",
                inline=False,
            )
        add_cultivation_preview_fields(embed, player, mod, cfg, now)

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
    listings = list_shop_listings()
    current_lower = current.lower()
    choices: list[app_commands.Choice[str]] = []
    for listing in listings:
        if current_lower and current_lower not in listing.name.lower() and current_lower not in listing.shop_id.lower():
            continue
        label = f"{listing.name} ({listing.price} stones)"
        choices.append(app_commands.Choice(name=label[:100], value=listing.shop_id))
        if len(choices) >= 25:
            break
    return choices


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
        ok, message = buy_from_shop(session, player, shop_id, int(quantity))
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


@bot.tree.command(name="inventory", description="View your materials, pills, and keys.")
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
        title, description = format_inventory_embed(player, stacks)

        embed = discord.Embed(title=title, description=description, color=discord.Color.dark_teal())
        embed.set_footer(text=f"{len(stacks)} item type(s) in storage")
        attach_guidance(embed, "inventory", player, session, cfg, utcnow())
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
        count = await post_tutorial(target, pin_intro=pin_intro)
        await interaction.followup.send(
            f"Tutorial posted to {target.mention} — **{count}** messages (intro + guide pages).",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"I can't send messages in {target.mention}. Check **Send Messages**, **Embed Links**, and **Pin Messages**.",
            ephemeral=True,
        )
    except Exception as exc:
        logger.exception("post-tutorial failed %s", interaction_ctx(interaction))
        await interaction.followup.send(f"Failed to post tutorial: {exc}", ephemeral=True)


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

        sect = None
        if player.sect_id is not None:
            sect = session.get(Sect, player.sect_id)

        rng = rng_for(guild_id, discord_id)
        res: CultivateResult = cultivate(player, sect, cfg, rng=rng, mod=mod)
        if "qi_gathering" in mod.active_effects:
            consume_effect_charge(session, player.id, "qi_gathering")
        consume_haste_for_activity(session, player.id, "cultivate")
        player.last_cultivate_at = now
        schedule_player_reminders(session, player, cfg, "cultivate", now=now)

        session.add(player)
        if sect is not None:
            session.add(sect)
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

        embed = discord.Embed(title="Cultivation Complete", description=res.message, color=discord.Color.green())
        embed.add_field(name="Qi", value=str(player.qi), inline=True)
        if passive_before > 0:
            embed.add_field(
                name="Qi breakdown",
                value=(
                    f"**+{passive_before + res.qi_gain}** total "
                    f"(**+{res.qi_gain}** active · **+{passive_before}** passive)"
                ),
                inline=False,
            )
        embed.add_field(name="Spirit Stones", value=str(player.spirit_stones), inline=True)
        embed.add_field(name="Stamina", value=f"{player.stamina}/100", inline=True)
        embed.add_field(name="Realm", value=realm_display(player.realm_index, player.substage), inline=False)

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
        rng = rng_for(guild_id, discord_id)
        res: BreakthroughResult = breakthrough(player, cfg, rng=rng, mod=mod)
        if "clarity" in mod.active_effects and res.success:
            consume_effect_charge(session, player.id, "clarity")

        session.add(player)
        session.commit()

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
        embed = discord.Embed(title="Breakthrough", description=res.message, color=color)
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
        attach_guidance(embed, "breakthrough", player, session, cfg, now)
        await interaction.response.send_message(embed=embed, ephemeral=True)
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
        schedule_player_reminders(session, player, cfg, "daily", now=now)

        session.add(player)
        session.commit()

        embed = discord.Embed(
            title="Daily Stipend",
            description=(
                f"You accept the day's offerings.\n"
                f"+{stones} spirit stones, +{qi} qi."
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
@app_commands.choices(area=AREA_CHOICES)
async def areas_cmd(
    interaction: discord.Interaction,
    area: app_commands.Choice[str] | None = None,
):
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)

        area_id = area.value if area is not None else None
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


@bot.tree.command(name="adventure", description="Start an interactive adventure with choices.")
@app_commands.choices(area=AREA_CHOICES, stance=STANCE_CHOICES)
@app_commands.describe(
    area="Where to explore.",
    stance="How boldly you press forward. Risky choices can boost loot or fail the run.",
)
async def adventure_cmd(
    interaction: discord.Interaction,
    area: app_commands.Choice[str],
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
        pending, err = start_adventure_session(session, player, area.value, stance.value, rng=rng)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        assert pending is not None
        session.commit()

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
@app_commands.choices(slot=EQUIP_SLOT_CHOICES)
async def forge_cmd(interaction: discord.Interaction, slot: app_commands.Choice[str]):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        rng = rng_for(guild_id, discord_id)
        res = forge_equipment(session, player.id, slot.value, rng=rng)
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
        summary = format_stats_summary(session, player.id)
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


craft_group = app_commands.Group(name="craft", description="Craft pills and dungeon keys.")


@craft_group.command(name="pill", description="Craft a pill from materials.")
@app_commands.choices(recipe=PILL_RECIPE_CHOICES)
@app_commands.describe(recipe="Which pill to brew.", amount="How many to attempt (1-10).")
async def craft_pill_cmd(
    interaction: discord.Interaction,
    recipe: app_commands.Choice[str],
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

        rng = rng_for(guild_id, discord_id)
        res = craft_recipe(session, player, recipe.value, amount=amount, rng=rng)
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
@app_commands.choices(recipe=KEY_RECIPE_CHOICES)
async def craft_key_cmd(interaction: discord.Interaction, recipe: app_commands.Choice[str]):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        rng = rng_for(guild_id, discord_id)
        res = craft_recipe(session, player, recipe.value, amount=1, rng=rng)
        session.add(player)
        session.commit()

        color = discord.Color.green() if res.success else discord.Color.red()
        embed = discord.Embed(title="Key Forging", description=res.message, color=color)
        attach_guidance(embed, "craft_key", player, session, get_config(), utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    finally:
        session.close()


bot.tree.add_command(craft_group)


@bot.tree.command(name="dungeon", description="Enter a key-gated dungeon.")
@app_commands.describe(name="Which dungeon to enter.", mode="Solo for now.")
@app_commands.choices(name=DUNGEON_CHOICES)
async def dungeon_cmd(
    interaction: discord.Interaction,
    name: app_commands.Choice[str],
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
        res = run_dungeon(session, player, name.value, mode=mode, rng=rng)
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
@app_commands.choices(slot=EQUIP_SLOT_CHOICES)
async def equip_cmd(interaction: discord.Interaction, slot: app_commands.Choice[str]):
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        rng = rng_for(guild_id, discord_id)
        ok, message, _affix = apply_affix_stone(session, player.id, slot.value, rng=rng)
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


# Sect commands (minimal MVP)


@bot.tree.command(name="sect-create", description="Create a sect (MVP).")
async def sect_create_cmd(interaction: discord.Interaction, name: str):
    session = get_session()
    try:
        logger.info("CMD /sect-create begin %s name=%r", interaction_ctx(interaction), name)
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

        if player.sect_id is not None:
            await interaction.response.send_message("You are already in a sect. Use `/sect-leave` first.", ephemeral=True)
            return

        name = name.strip()
        if not name:
            await interaction.response.send_message("Sect name cannot be empty.", ephemeral=True)
            return

        existing = get_or_create_sect(session, guild_id, name)
        if existing is not None:
            await interaction.response.send_message("A sect with that name already exists.", ephemeral=True)
            return

        sect = Sect(
            guild_id=guild_id,
            name=name,
            created_by_discord_id=discord_id,
            sect_qi_contributed=0,
            member_count=1,
        )
        session.add(sect)
        session.flush()  # get sect.id

        player.sect_id = sect.id
        player.sect_role = "founder"
        player.sect_contribution_qi_total = 0
        session.add(player)
        session.commit()
        logger.info(
            "Sect created guild=%s founder=%s sect_id=%s sect_name=%r member_count=%s",
            guild_id,
            discord_id,
            sect.id,
            name,
            sect.member_count,
        )

        embed = discord.Embed(
            title="New Sect Formed",
            description=f"The sect `{name}` opens its gate. You become its founder.",
            color=discord.Color.dark_gold(),
        )
        attach_guidance(embed, "sect-create", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="sect-join", description="Join an existing sect (MVP).")
async def sect_join_cmd(interaction: discord.Interaction, name: str):
    session = get_session()
    try:
        logger.info("CMD /sect-join begin %s name=%r", interaction_ctx(interaction), name)
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

        if player.sect_id is not None:
            await interaction.response.send_message("You are already in a sect. Use `/sect-leave` first.", ephemeral=True)
            return

        name = name.strip()
        if not name:
            await interaction.response.send_message("Sect name cannot be empty.", ephemeral=True)
            return

        sect = get_or_create_sect(session, guild_id, name)
        if sect is None:
            await interaction.response.send_message("That sect does not exist.", ephemeral=True)
            return

        # Set membership.
        player.sect_id = sect.id
        player.sect_role = "member"
        player.sect_contribution_qi_total = 0
        sect.member_count += 1

        session.add(player)
        session.add(sect)
        session.commit()
        logger.info(
            "Sect joined guild=%s user=%s sect_id=%s sect_name=%r new_member_count=%s",
            guild_id,
            discord_id,
            sect.id,
            sect.name,
            sect.member_count,
        )

        embed = discord.Embed(
            title="You Join a Sect",
            description=f"You enter `{sect.name}`. Your cultivation will contribute to the sect’s qi.",
            color=discord.Color.green(),
        )
        attach_guidance(embed, "sect-join", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="sect-leave", description="Leave your current sect (MVP).")
async def sect_leave_cmd(interaction: discord.Interaction):
    session = get_session()
    try:
        logger.info("CMD /sect-leave begin %s", interaction_ctx(interaction))
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

        if player.sect_id is None:
            await interaction.response.send_message("You are not in a sect.", ephemeral=True)
            return

        sect = session.get(Sect, player.sect_id)
        if sect is not None:
            sect.member_count = max(0, sect.member_count - 1)
            session.add(sect)

        player.sect_id = None
        player.sect_role = "member"
        player.sect_contribution_qi_total = 0

        session.add(player)
        session.commit()
        logger.info("Sect left guild=%s user=%s", guild_id, discord_id)

        embed = discord.Embed(
            title="You Leave the Sect",
            description="You close the gate behind you. The world does not wait.",
            color=discord.Color.orange(),
        )
        attach_guidance(embed, "sect-leave", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


@bot.tree.command(name="sect", description="View sect details (MVP).")
async def sect_cmd(interaction: discord.Interaction):
    cfg = get_config()
    session = get_session()
    try:
        logger.info("CMD /sect begin %s", interaction_ctx(interaction))
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=True)
            return

        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=True)
            return

        if player.sect_id is None:
            await interaction.response.send_message("You are not in a sect. Use `/sect-create` or `/sect-join`.", ephemeral=True)
            return

        sect = session.get(Sect, player.sect_id)
        if sect is None:
            await interaction.response.send_message("Your sect record was not found. This is an error.", ephemeral=True)
            return

        # Top contributors (cheap query for MVP).
        stmt = (
            select(Player)
            .where(Player.guild_id == guild_id, Player.sect_id == sect.id)
            .order_by(Player.sect_contribution_qi_total.desc())
            .limit(5)
        )
        members = list(session.execute(stmt).scalars().all())
        lines = [f"{p.dao_name}: {p.sect_contribution_qi_total} qi" for p in members]

        embed = discord.Embed(
            title=f"Sect: {sect.name}",
            description=f"Members: {sect.member_count}\nTotal contributed Qi: {sect.sect_qi_contributed}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Top Contributors", value="\n".join(lines) if lines else "None yet.", inline=False)
        attach_guidance(embed, "sect", player, session, cfg, utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=False)
    finally:
        session.close()


async def sync_commands() -> None:
    cfg = get_config()
    # Syncing is guild-scoped for fast iteration.
    if cfg.guild_id:
        await bot.tree.sync(guild=discord.Object(id=int(cfg.guild_id)))
    else:
        await bot.tree.sync()


def make_origin_choice() -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=o, value=o) for o in ORIGINS]


def make_moral_choice() -> list[app_commands.Choice[str]]:
    morals = [("righteous", "righteous"), ("demonic", "demonic"), ("neutral", "neutral")]
    return [app_commands.Choice(name=label.title(), value=val) for label, val in morals]


async def main():
    cfg = get_config()
    init_db()
    load_all_content()
    load_item_catalog()

    print("Bot ready. Starting event loop...")
    await bot.start(cfg.discord_token)


if __name__ == "__main__":
    asyncio.run(main())

