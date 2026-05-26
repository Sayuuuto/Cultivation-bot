from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .models import Player, Sect


REALMS = [
    "Mortal",
    "Qi Refining",
    "Foundation Establishment",
    "Core Formation",
    "Nascent Soul",
    "Spirit Severing",
    "Void Refinement",
    "Immortal Ascension",
    "Heavenly Transcendence",
    "Immortal Monarch",
]

SUBSTAGES = ["early", "mid", "late"]

# Base Qi caps for each realm (0..9). Sub-stages multiply this.
REALM_BASE_QI_CAP = [100, 250, 600, 1400, 3200, 7200, 16000, 36000, 82000, 180000]
SUBSTAGE_MULTIPLIER = [1.0, 1.5, 2.2]  # early/mid/late

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


def realm_index_range(realm_index: int) -> bool:
    return 0 <= realm_index < len(REALMS)


def substage_range(substage: int) -> bool:
    return 0 <= substage < len(SUBSTAGES)


def qi_cap(realm_index: int, substage: int) -> int:
    realm_index = max(0, min(realm_index, len(REALMS) - 1))
    substage = max(0, min(substage, len(SUBSTAGES) - 1))
    return int(REALM_BASE_QI_CAP[realm_index] * SUBSTAGE_MULTIPLIER[substage])


def moral_breakthrough_modifiers(moral_path: str) -> tuple[float, float]:
    """
    Returns (success_bonus, fail_setback_multiplier).
    success_bonus is added to base success chance.
    """
    moral_path = (moral_path or "neutral").lower()
    if moral_path == "righteous":
        return (0.05, 0.85)
    if moral_path == "demonic":
        return (0.04, 1.25)
    return (0.0, 1.0)


def moral_breakthrough_setback_text(moral_path: str) -> str:
    moral_path = (moral_path or "neutral").lower()
    if moral_path == "righteous":
        return "Your dao remains steady despite the backlash."
    if moral_path == "demonic":
        return "The backlash bites deeper, as if the heavens themselves refuse you."
    return "A subtle pain spreads through your dantian."


def stamina_regen_per_hour() -> int:
    # Tied to your pacing preference: casual, not hardcore.
    return 10


def spirit_stones_daily_base() -> int:
    return 50


def spirit_stones_daily_streak_bonus(streak: int) -> int:
    return min(20, streak * 2)


def daily_qi_bonus(realm_index: int) -> int:
    return 10 + realm_index * 2


def cultivate_base_qi_gain(realm_index: int) -> int:
    return 10 + realm_index * 2


def cultivate_stone_chance(realm_index: int) -> float:
    # Small chance to grant 1..3 stones per cultivate.
    return min(0.25, 0.12 + realm_index * 0.01)


def energy_stamina_multiplier(stamina: int, stamina_max: int = 100) -> float:
    frac = 0.0 if stamina_max <= 0 else max(0.0, min(1.0, stamina / stamina_max))
    return 0.7 + 0.6 * frac  # 0.7..1.3


def compute_stamina_regen(stamina_last_updated_at: datetime, now: datetime, stamina_max: int = 100) -> int:
    now = to_utc(now)
    stamina_last_updated_at = to_utc(stamina_last_updated_at)
    if now <= stamina_last_updated_at:
        return 0
    hours = (now - stamina_last_updated_at).total_seconds() / 3600.0
    regen = hours * stamina_regen_per_hour()
    return clamp_int(regen, 0, stamina_max)


def apply_stamina_regen(player: Player, now: datetime) -> None:
    if player.stamina_last_updated_at is None:
        player.stamina_last_updated_at = now
        return
    regen = compute_stamina_regen(player.stamina_last_updated_at, now)
    if regen > 0:
        player.stamina = min(100, player.stamina + regen)
        player.stamina_last_updated_at = now


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
    new_stamina: int
    stamina_cost: int
    leveled_up: bool
    new_realm_index: int
    new_substage: int
    message: str


def moral_cultivation_text(moral_path: str) -> str:
    moral_path = (moral_path or "neutral").lower()
    if moral_path == "righteous":
        return "You draw the spirit with a clean will."
    if moral_path == "demonic":
        return "You pull the spirit with restraint only where necessary."
    return "You draw the qi as if it were always yours."


def cultivate(player: Player, sect: Sect | None, cfg: Config, rng: random.Random | None = None, mod=None) -> CultivateResult:
    """
    Mutates `player` (and `sect` if provided), but returns a rich result for UI.
    """
    rng = rng or random.Random()
    now = utcnow()
    qi_mult = 1.0 if mod is None else getattr(mod, "cultivate_qi_mult", 1.0) * getattr(mod, "qi_gathering_mult", 1.0)
    stamina_eff = 1.0 if mod is None else getattr(mod, "stamina_efficiency", 1.0)
    offline_mult = 1.0 if mod is None else getattr(mod, "offline_cap_mult", 1.0)
    sect_mult = 1.0 if mod is None else getattr(mod, "sect_contribution_mult", 1.0)

    apply_stamina_regen(player, now)

    # Offline progress (partial cap) applied on the first action after being inactive.
    offline_qi = 0
    if player.last_active_at is not None:
        offline_qi = apply_offline_progress(player, now, cfg.offline_cap_minutes, cap_mult=offline_mult)

    player.last_active_at = now

    stamina_cost = max(1, int(8 / stamina_eff))
    effective_stamina = max(0, player.stamina)
    stamina_multiplier = energy_stamina_multiplier(effective_stamina)

    # Consume stamina even if low; casual but consistent.
    player.stamina = max(0, player.stamina - stamina_cost)

    base = cultivate_base_qi_gain(player.realm_index)
    # Luck makes it feel less robotic without becoming pay-to-win.
    luck_roll = rng.uniform(0.85, 1.15)
    qi_gain = int(base * stamina_multiplier * luck_roll * qi_mult) + offline_qi
    qi_gain = max(0, qi_gain)

    player.qi += qi_gain

    # Small chance for spirit stones.
    stones_gain = 0
    if rng.random() < cultivate_stone_chance(player.realm_index):
        stones_gain = rng.randint(1, 3)
        player.spirit_stones += stones_gain

    # Sect contribution: a small percent of gained Qi.
    leveled_up = False
    new_realm_index = player.realm_index
    new_substage = player.substage
    if sect is not None and qi_gain > 0:
        contribution = int(qi_gain * 0.08 * sect_mult)
        if contribution > 0:
            sect.sect_qi_contributed += contribution
            player.sect_contribution_qi_total += contribution
        # No member_count update here; handled by join/leave.

    # Leveling is achieved via breakthrough, not auto-realm change in MVP.
    # So leveled_up stays false.

    msg = (
        f"{moral_cultivation_text(player.moral_path)} "
        f"You harvest {qi_gain} qi and refine it into calm power."
    )
    if stones_gain:
        msg += f" A trace of spirit stones answers your call (+{stones_gain})."

    return CultivateResult(
        qi_gain=qi_gain,
        stones_gain=stones_gain,
        new_qi=player.qi,
        new_stamina=player.stamina,
        stamina_cost=stamina_cost,
        leveled_up=leveled_up,
        new_realm_index=new_realm_index,
        new_substage=new_substage,
        message=msg,
    )


@dataclass(frozen=True)
class BreakthroughResult:
    success: bool
    qi_delta: int
    new_realm_index: int
    new_substage: int
    message: str
    success_chance: float = 0.0


@dataclass(frozen=True)
class BreakthroughPreview:
    success_chance: float
    fail_setback_multiplier: float
    qi_required: int
    can_attempt: bool
    estimated_fail_setback: int
    base_success: float
    moral_bonus: float
    stability_bonus: float
    clarity_bonus: float
    realm_penalty: float


def compute_breakthrough_preview(player: Player, mod=None) -> BreakthroughPreview:
    cap = qi_cap(player.realm_index, player.substage)
    base_success = 0.70
    difficulty_penalty = player.realm_index * 0.015
    moral_bonus, fail_setback_mult = moral_breakthrough_modifiers(player.moral_path)
    stability_bonus = 0.0
    clarity_bonus = 0.0
    if mod is not None:
        stability_bonus = getattr(mod, "breakthrough_stability", 0.0)
        clarity_bonus = getattr(mod, "clarity_breakthrough_bonus", 0.0)
        fail_setback_mult *= getattr(mod, "breakthrough_setback_mult", 1.0)

    total_bonus = moral_bonus + stability_bonus + clarity_bonus
    success_chance = max(0.15, min(0.92, base_success + total_bonus - difficulty_penalty))
    setback_base = player.qi * (0.15 + 0.02 * player.realm_index)
    estimated_setback = int(setback_base * fail_setback_mult)

    return BreakthroughPreview(
        success_chance=success_chance,
        fail_setback_multiplier=fail_setback_mult,
        qi_required=cap,
        can_attempt=player.qi >= cap,
        estimated_fail_setback=estimated_setback,
        base_success=base_success,
        moral_bonus=moral_bonus,
        stability_bonus=stability_bonus,
        clarity_bonus=clarity_bonus,
        realm_penalty=difficulty_penalty,
    )


def breakthrough(player: Player, cfg: Config, rng: random.Random | None = None, mod=None) -> BreakthroughResult:
    rng = rng or random.Random()
    now = utcnow()

    cap = qi_cap(player.realm_index, player.substage)
    if player.qi < cap:
        preview = compute_breakthrough_preview(player, mod)
        return BreakthroughResult(
            success=False,
            qi_delta=0,
            new_realm_index=player.realm_index,
            new_substage=player.substage,
            message=f"Your qi is not sufficient. Need {cap} qi to attempt breakthrough.",
            success_chance=preview.success_chance,
        )

    preview = compute_breakthrough_preview(player, mod)
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
    # Small stamina penalty to keep it feeling real.
    player.stamina = max(0, player.stamina - 3)

    return BreakthroughResult(
        success=False,
        qi_delta=-setback,
        new_realm_index=player.realm_index,
        new_substage=player.substage,
        success_chance=success_chance,
        message=moral_breakthrough_setback_text(player.moral_path) + f" You lose {setback} qi.",
    )


def player_strength_for_pvp(player: Player, mod=None) -> float:
    cap = qi_cap(player.realm_index, player.substage)
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

