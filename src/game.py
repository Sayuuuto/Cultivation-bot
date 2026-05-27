from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .models import Clan, Player


from .realms import (
    REALM_BASE_QI_CAP,
    REALMS,
    SUBSTAGE_MULTIPLIER,
    SUBSTAGES,
    get_realm_name,
    qi_cap,
    realm_index_range,
    substage_range,
)

SPIRIT_ROOTS = [
    "Pure Jade Root",
    "Violet Lightning Root",
    "Flame Ember Root",
    "Frost Spirit Root",
    "Earthweight Root",
    "Moonlit Sword Root",
    "Mercy Lotus Root",
]

ORIGINS = [
    "Mountain Rises",
    "Waterside Vow",
    "Ancient Tomb Awakened",
    "River Dragon’s Gift",
    "Starfall Seclusion",
    "Wandering Immortal’s Footsteps",
]


def clamp_int(n: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(n)))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """
    SQLite commonly returns naive datetimes even when timezone=True is set.
    Normalize to timezone-aware UTC before comparisons/arithmetic.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


from .karma import (
    clamp_karma,
    karma_breakthrough_modifiers,
    karma_breakthrough_setback_text,
    karma_cultivation_text,
)


def spirit_stones_daily_base() -> int:
    return 50


def spirit_stones_daily_streak_bonus(streak: int) -> int:
    return min(20, streak * 2)


def daily_qi_bonus(realm_index: int) -> int:
    return 10 + realm_index * 2


def cultivate_base_qi_gain(realm_index: int) -> int:
    return 10 + realm_index * 2


def passive_qi_per_minute(realm_index: int) -> float:
    """Qi banked per minute while inactive; paid out on the player's next action."""
    return cultivate_base_qi_gain(realm_index) / 15.0


CULTIVATE_LUCK_MIN = 0.85
CULTIVATE_LUCK_MAX = 1.15


def cultivate_stone_chance(realm_index: int) -> float:
    # Small chance to grant 1..3 stones per cultivate.
    return min(0.25, 0.12 + realm_index * 0.01)


def apply_offline_progress(player: Player, now: datetime, offline_cap_minutes: int, cap_mult: float = 1.0) -> int:
    """
    Returns qi gained from offline progress.
    Offline progress is capped to a small amount so players don't feel it becomes pay/clock abuse.
    """
    if player.last_active_at is None:
        return 0

    now = to_utc(now)
    last_active = to_utc(player.last_active_at)
    if now <= last_active:
        return 0

    minutes = int((now - last_active).total_seconds() / 60)
    capped_minutes = min(minutes, int(offline_cap_minutes * cap_mult))
    if capped_minutes <= 0:
        return 0

    avg_cultivate_qi = cultivate_base_qi_gain(player.realm_index)  # representative scale
    # A cultivate is on 15min cooldown by default -> align offline minutes with typical action frequency.
    qi = int(avg_cultivate_qi * capped_minutes / 15)
    return max(0, qi)


@dataclass(frozen=True)
class CultivateResult:
    qi_gain: int
    stones_gain: int
    new_qi: int
    leveled_up: bool
    new_realm_index: int
    new_substage: int
    message: str
    event_id: str | None = None
    event_title: str = ""
    event_emoji: str = ""
    event_message: str = ""
    event_qi_mult: float = 1.0
    bonus_drops: dict[str, int] | None = None
    meridian_note: str = ""


def cultivate(
    player: Player,
    clan: Clan | None,
    cfg: Config,
    rng: random.Random | None = None,
    mod=None,
    *,
    session=None,
    player_id: int | None = None,
) -> CultivateResult:
    """
    Mutates `player` (and `clan` if provided), but returns a rich result for UI.
    """
    rng = rng or random.Random()
    now = utcnow()
    qi_mult = 1.0 if mod is None else getattr(mod, "cultivate_qi_mult", 1.0) * getattr(mod, "qi_gathering_mult", 1.0)
    offline_mult = 1.0 if mod is None else getattr(mod, "offline_cap_mult", 1.0)
    clan_mult = 1.0 if mod is None else getattr(mod, "clan_contribution_mult", 1.0)

    # Offline progress (partial cap) applied on the first action after being inactive.
    offline_qi = 0
    if player.last_active_at is not None:
        offline_qi = apply_offline_progress(player, now, cfg.offline_cap_minutes, cap_mult=offline_mult)

    player.last_active_at = now

    base = cultivate_base_qi_gain(player.realm_index)
    # Luck makes it feel less robotic without becoming pay-to-win.
    luck_roll = rng.uniform(0.85, 1.15)
    from .cultivate_events import roll_cultivate_event
    from .novice_trial import apply_novice_cultivate_boost, on_cultivated, should_force_first_cultivate_event

    qi_gain = int(base * luck_roll * qi_mult) + offline_qi
    qi_gain = apply_novice_cultivate_boost(player, max(0, qi_gain))

    event = roll_cultivate_event(
        rng,
        player_id=player_id,
        session=session,
        karma=player.karma,
        force_event_id="meridian_awakening" if should_force_first_cultivate_event(player) else None,
    )
    event_id: str | None = None
    event_title = ""
    event_emoji = ""
    event_message = ""
    event_qi_mult = 1.0
    bonus_drops: dict[str, int] = {}
    meridian_note = ""

    if event is not None:
        event_id = event.event_id
        event_title = event.title
        event_emoji = event.emoji
        event_message = event.message
        event_qi_mult = event.qi_mult
        qi_gain = max(0, int(qi_gain * event.qi_mult) + event.bonus_qi)
        bonus_drops = dict(event.drops)
        if event.meridian_points > 0:
            from .foundation import grant_meridian_points

            meridian_note = grant_meridian_points(player, event.meridian_points)

    player.qi += qi_gain

    # Small chance for spirit stones (skipped when a rare event already paid out).
    stones_gain = 0
    if event is not None and event.bonus_stones > 0:
        stones_gain = event.bonus_stones
        player.spirit_stones += stones_gain
    elif rng.random() < cultivate_stone_chance(player.realm_index):
        stones_gain = rng.randint(1, 3)
        player.spirit_stones += stones_gain

    # Clan contribution: a small percent of gained Qi.
    leveled_up = False
    new_realm_index = player.realm_index
    new_substage = player.substage
    if clan is not None and qi_gain > 0:
        contribution = int(qi_gain * 0.08 * clan_mult)
        if contribution > 0:
            clan.clan_qi_contributed += contribution
            player.clan_contribution_qi_total += contribution
        # No member_count update here; handled by join/leave.

    # Leveling is achieved via breakthrough, not auto-realm change in MVP.
    # So leveled_up stays false.

    if event is not None:
        msg = f"{event.emoji} **{event.title}** — {event.message}"
    else:
        msg = (
            f"{karma_cultivation_text(player.karma)} "
            f"You harvest {qi_gain} qi and refine it into calm power."
        )
    if stones_gain:
        msg += f" 💎 **+{stones_gain}** spirit stones answer your call."

    from .foundation import roll_cultivate_meridian_insight

    if not meridian_note:
        extra_meridian = roll_cultivate_meridian_insight(player, rng)
        if extra_meridian:
            meridian_note = extra_meridian
    if meridian_note:
        msg += f"\n{meridian_note}"

    trial_msgs = on_cultivated(player)
    if trial_msgs:
        msg += "\n" + "\n".join(trial_msgs)

    if session is not None and player_id is not None:
        from .game_sects import on_sect_activity

        sect_msgs = on_sect_activity(session, player, "cultivate")
        if sect_msgs:
            msg += "\n" + "\n".join(sect_msgs)

    return CultivateResult(
        qi_gain=qi_gain,
        stones_gain=stones_gain,
        new_qi=player.qi,
        leveled_up=leveled_up,
        new_realm_index=new_realm_index,
        new_substage=new_substage,
        message=msg,
        event_id=event_id,
        event_title=event_title,
        event_emoji=event_emoji,
        event_message=event_message,
        event_qi_mult=event_qi_mult,
        bonus_drops=bonus_drops or None,
        meridian_note=meridian_note,
    )


@dataclass(frozen=True)
class BreakthroughResult:
    success: bool
    qi_delta: int
    new_realm_index: int
    new_substage: int
    message: str
    success_chance: float = 0.0


BREAKTHROUGH_SUCCESS_CAP = 0.99
BREAKTHROUGH_SUCCESS_FLOOR = 0.10
QI_OVERFLOW_BONUS_CAP = 0.04
CLARITY_BONUS_PER_CHARGE = 0.14


@dataclass(frozen=True)
class BreakthroughPreview:
    success_chance: float
    fail_setback_multiplier: float
    qi_required: int
    can_attempt: bool
    estimated_fail_setback: int
    base_success: float
    karma_bonus: float
    stability_bonus: float
    clarity_bonus: float
    qi_fill_bonus: float
    realm_penalty: float
    clarity_charges: int = 0


def compute_breakthrough_preview(
    player: Player,
    mod=None,
    *,
    session=None,
    player_id: int | None = None,
) -> BreakthroughPreview:
    from .effects import clarity_breakthrough_bonus
    from .realms import breakthrough_start_success, realm_breakthrough_base_success

    cap = qi_cap(player.realm_index, player.substage, player)
    base_success = realm_breakthrough_base_success(player.realm_index, player.substage)
    realm_anchor = breakthrough_start_success()
    realm_penalty = max(0.0, realm_anchor - base_success)
    karma_bonus, fail_setback_mult = karma_breakthrough_modifiers(player.karma)
    stability_bonus = 0.0
    if mod is not None:
        stability_bonus = getattr(mod, "breakthrough_stability", 0.0)
        fail_setback_mult *= getattr(mod, "breakthrough_setback_mult", 1.0)

    qi_fill_bonus = 0.0
    if cap > 0 and player.qi >= cap:
        overflow_ratio = max(0.0, (player.qi - cap) / cap)
        qi_fill_bonus = min(QI_OVERFLOW_BONUS_CAP, overflow_ratio * 0.08)

    clarity_charges = 0
    clarity_bonus = 0.0
    if session is not None and player_id is not None:
        clarity_charges, clarity_bonus = clarity_breakthrough_bonus(session, player_id)

    total_bonus = karma_bonus + stability_bonus + clarity_bonus + qi_fill_bonus
    success_chance = max(
        BREAKTHROUGH_SUCCESS_FLOOR,
        min(BREAKTHROUGH_SUCCESS_CAP, base_success + total_bonus),
    )
    setback_base = player.qi * (0.15 + 0.02 * player.realm_index)
    estimated_setback = int(setback_base * fail_setback_mult)

    return BreakthroughPreview(
        success_chance=success_chance,
        fail_setback_multiplier=fail_setback_mult,
        qi_required=cap,
        can_attempt=player.qi >= cap,
        estimated_fail_setback=estimated_setback,
        base_success=base_success,
        karma_bonus=karma_bonus,
        stability_bonus=stability_bonus,
        clarity_bonus=clarity_bonus,
        qi_fill_bonus=qi_fill_bonus,
        realm_penalty=realm_penalty,
        clarity_charges=clarity_charges,
    )


def breakthrough(
    player: Player,
    cfg: Config,
    rng: random.Random | None = None,
    mod=None,
    *,
    session=None,
    player_id: int | None = None,
) -> BreakthroughResult:
    rng = rng or random.Random()
    _ = cfg  # reserved for future tuning hooks
    cap = qi_cap(player.realm_index, player.substage, player)
    if player.qi < cap:
        preview = compute_breakthrough_preview(
            player, mod, session=session, player_id=player_id or player.id
        )
        return BreakthroughResult(
            success=False,
            qi_delta=0,
            new_realm_index=player.realm_index,
            new_substage=player.substage,
            message=f"Your qi is not sufficient. Need {cap} qi to attempt breakthrough.",
            success_chance=preview.success_chance,
        )

    preview = compute_breakthrough_preview(
        player, mod, session=session, player_id=player_id or player.id
    )
    success_chance = preview.success_chance
    fail_setback_mult = preview.fail_setback_multiplier

    roll = rng.random()
    if roll <= success_chance:
        # Success: advance substage; late->next realm.
        old_realm, old_substage = player.realm_index, player.substage
        if player.substage < 2:
            player.substage += 1
        else:
            player.realm_index = min(len(REALMS) - 1, player.realm_index + 1)
            player.substage = 0

        # Keep some leftover qi.
        player.qi = int(player.qi * 0.25)

        return BreakthroughResult(
            success=True,
            qi_delta=0,
            new_realm_index=player.realm_index,
            new_substage=player.substage,
            success_chance=success_chance,
            message=(
                f"Breakthrough successful. ({REALMS[old_realm]} / {SUBSTAGES[old_substage]} -> "
                f"{REALMS[player.realm_index]} / {SUBSTAGES[player.substage]}) "
                "The world grows slightly quieter."
            ),
        )

    # Failure: setback, no death.
    setback_base = player.qi * (0.15 + 0.02 * player.realm_index)
    setback = int(setback_base * fail_setback_mult)
    player.qi = max(0, player.qi - setback)

    return BreakthroughResult(
        success=False,
        qi_delta=-setback,
        new_realm_index=player.realm_index,
        new_substage=player.substage,
        success_chance=success_chance,
        message=karma_breakthrough_setback_text(player.karma) + f" You lose {setback} qi.",
    )


def player_strength_for_pvp(player: Player, mod=None) -> float:
    cap = qi_cap(player.realm_index, player.substage, player)
    ratio = 0.0 if cap <= 0 else min(1.0, player.qi / cap)
    power = player.realm_index * 100 + player.substage * 40 + ratio * 100
    if mod is not None:
        power *= 1.0 + getattr(mod, "pvp_power", 0.0)
    return power


@dataclass(frozen=True)
class DuelResult:
    success: bool  # whether challenger won
    winner_discord_id: str
    loser_discord_id: str
    stones_delta_winner: int
    qi_transfer: int
    message: str


def duel(challenger: Player, opponent: Player, cfg: Config, rng: random.Random | None = None, challenger_mod=None, opponent_mod=None) -> DuelResult:
    rng = rng or random.Random()

    strength_a = player_strength_for_pvp(challenger, challenger_mod)
    strength_b = player_strength_for_pvp(opponent, opponent_mod)

    if challenger.realm_index == opponent.realm_index:
        # Symmetric feel when equal realm.
        strength_diff = strength_a - strength_b
    else:
        strength_diff = strength_a - strength_b

    # Chance challenger wins, bounded.
    chance_a = 0.5 + max(-0.35, min(0.35, strength_diff / 400))
    chance_a = max(0.1, min(0.9, chance_a))

    challenger_wins = rng.random() <= chance_a
    if challenger_wins:
        winner = challenger
        loser = opponent
    else:
        winner = opponent
        loser = challenger

    # Forgiving stakes.
    base_stones = 10 + winner.realm_index * 2
    winner_mult = 1.0 if challenger_mod is None else getattr(challenger_mod, "pvp_stones_mult", 1.0)
    if winner is opponent:
        winner_mult = 1.0 if opponent_mod is None else getattr(opponent_mod, "pvp_stones_mult", 1.0)
    stones_gain = int(base_stones * winner_mult)

    winner.spirit_stones += stones_gain

    winner_id = winner.discord_id
    loser_id = loser.discord_id

    title = "win" if challenger_wins else "fall back"
    msg = (
        f"Your dao clashes with another. You {title} this round. "
        f"Winner gains +{stones_gain} spirit stones."
    )

    return DuelResult(
        success=challenger_wins,
        winner_discord_id=winner_id,
        loser_discord_id=loser_id,
        stones_delta_winner=stones_gain,
        qi_transfer=0,
        message=msg,
    )


def compute_daily_rewards(player: Player) -> tuple[int, int]:
    """
    Returns (stones, qi).
    """
    stones = spirit_stones_daily_base() + spirit_stones_daily_streak_bonus(player.daily_streak)
    qi = daily_qi_bonus(player.realm_index)
    return stones, qi

