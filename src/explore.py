from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .combat.engine import CombatState, OpponentTemplate, create_combat_state, opponent_from_monster
from .combat.session import COMBAT_BUSY_MESSAGE, create_active_combat, get_active_combat, load_combat_state
from .combat_stats import PlayerCombatStats, compute_combat_stats, scale_monster_stats
from .config import get_config
from .inventory import add_item, get_item_name
from .manuals import grant_manual_drop, roll_manual_pool_reward
from .models import ExploreSession, Player

EXPLORE_COOLDOWN_HOURS = 24

CONFIG_PATH = Path(__file__).resolve().parent.parent
AREAS_PATH = CONFIG_PATH / "config" / "explore_areas.json"
ENCOUNTERS_PATH = CONFIG_PATH / "config" / "explore_encounters.json"

_areas: dict[str, dict] | None = None
_encounters: dict[str, list[dict]] | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class ExploreArea:
    area_id: str
    name: str
    theme: str
    danger: float
    reward_multiplier: float
    roots: list[str]
    paths: list[str]
    manual_bias: list[str]
    pill_bias: list[str]


@dataclass
class ExploreChoice:
    id: str
    label: str
    type: str
    enemy: dict | None = None
    stats: list[str] | None = None
    dc: int = 50
    heal_pct: float = 0.0
    risk: int = 1
    success_rewards: list[str] | None = None
    fail_damage_pct: float = 0.0
    amount: int = 0
    moral: str = ""


@dataclass
class ExploreEncounter:
    id: str
    type: str
    title: str
    text: str
    choices: list[ExploreChoice]


@dataclass
class RewardEntry:
    item_id: str
    quantity: int
    tier: str = "common"
    msg: str = ""


@dataclass
class ExploreState:
    current_hp: int = 0
    max_hp: int = 0
    rewards: list[dict] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    used_encounter_ids: list[str] = field(default_factory=list)
    encounter_sequence: list[str] = field(default_factory=list)
    combat_damage_taken: int = 0
    steps_cleared: int = 0


@dataclass
class StepResult:
    new_step: int
    total_steps: int
    encounter: ExploreEncounter | None
    finished: bool
    failed: bool
    retired: bool
    messages: list[str]
    hp_delta: int
    rewards_this_step: list[RewardEntry]
    combat_id: int | None = None


# ── Config loading ────────────────────────────────────────────────────────

def load_explore_areas() -> dict[str, ExploreArea]:
    global _areas
    if _areas is None:
        with AREAS_PATH.open(encoding="utf-8") as f:
            _areas = json.load(f)
    return {
        aid: ExploreArea(
            area_id=aid,
            name=data["name"],
            theme=data.get("theme", ""),
            danger=float(data.get("danger", 1.0)),
            reward_multiplier=float(data.get("reward_multiplier", 1.0)),
            roots=list(data.get("roots", [])),
            paths=list(data.get("paths", [])),
            manual_bias=list(data.get("manual_bias", [])),
            pill_bias=list(data.get("pill_bias", [])),
        )
        for aid, data in _areas.items()
    }


def load_explore_encounters(area_id: str) -> list[ExploreEncounter]:
    global _encounters
    if _encounters is None:
        with ENCOUNTERS_PATH.open(encoding="utf-8") as f:
            _encounters = json.load(f)
    raw_list = _encounters.get(area_id, [])
    result: list[ExploreEncounter] = []
    for raw in raw_list:
        choices = [
            ExploreChoice(
                id=c["id"],
                label=c.get("label", ""),
                type=c.get("type", "safe_skip"),
                enemy=c.get("enemy"),
                stats=c.get("stats"),
                dc=int(c.get("dc", 50)),
                heal_pct=float(c.get("heal_pct", 0.0)),
                risk=int(c.get("risk", 1)),
                success_rewards=c.get("success_rewards"),
                fail_damage_pct=float(c.get("fail_damage_pct", 0.0)),
                amount=int(c.get("amount", 0)),
                moral=c.get("moral", ""),
            )
            for c in raw.get("choices", [])
        ]
        result.append(ExploreEncounter(
            id=raw["id"],
            type=raw.get("type", "event"),
            title=raw.get("title", ""),
            text=raw.get("text", ""),
            choices=choices,
        ))
    return result


# ── Affinity ──────────────────────────────────────────────────────────────

def calculate_affinity(player: Player, area: ExploreArea) -> float:
    affinity = 1.0
    root = (player.spirit_root or "").lower()
    path = (player.moral_path or "").lower()
    for r in area.roots:
        if r in root:
            affinity += 0.15
            break
    for p in area.paths:
        if p in path:
            affinity += 0.10
            break
    return min(affinity, 1.35)


# ── Stat check formula ────────────────────────────────────────────────────

def _get_injury_penalty(current_hp: int, max_hp: int) -> int:
    if max_hp <= 0:
        return 0
    ratio = current_hp / max_hp
    if ratio >= 0.75:
        return 0
    if ratio >= 0.50:
        return 5
    if ratio >= 0.25:
        return 12
    return 25


def resolve_stat_check(
    player: Player,
    session: Session,
    state: ExploreState,
    check_stats: list[str],
    dc: int,
    *,
    area: ExploreArea | None = None,
) -> tuple[bool, int]:
    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    stat_values = [getattr(stats, s, 0) for s in check_stats]
    relevant = int(sum(stat_values) / max(len(stat_values), 1))

    realm_bonus = 25 * player.realm_index
    affinity = calculate_affinity(player, area) if area else 1.0
    affinity_bonus = int((affinity - 1.0) * 10)
    injury = _get_injury_penalty(state.current_hp, state.max_hp)
    roll = random.randint(1, 100)
    total = relevant + realm_bonus + affinity_bonus + roll - injury
    return total >= dc, total


# ── Encounter generation ──────────────────────────────────────────────────

def generate_encounter_sequence(
    area: ExploreArea,
    total_steps: int,
) -> list[ExploreEncounter]:
    pool = load_explore_encounters(area.area_id)
    if not pool:
        return []

    selected: list[ExploreEncounter] = []
    used_ids: set[str] = set()
    combat_streak = 0
    dao_used = False
    has_rest = False

    for step in range(total_steps):
        eligible = []
        for e in pool:
            if e.id in used_ids:
                continue
            if e.type == "combat" and combat_streak >= 2:
                continue
            if e.type == "dao_event" and dao_used:
                continue
            if e.type == "rest" and has_rest and step < total_steps - 1:
                continue
            if step == total_steps - 1 and e.type not in ("combat", "boss", "treasure"):
                continue
            eligible.append(e)

        if not eligible:
            eligible = [e for e in pool if e.id not in used_ids]
        if not eligible:
            break

        chosen = random.choice(eligible)
        selected.append(chosen)
        used_ids.add(chosen.id)

        if chosen.type == "combat":
            combat_streak += 1
        else:
            combat_streak = 0
        if chosen.type == "dao_event":
            dao_used = True
        if chosen.type == "rest":
            has_rest = True

    return selected


# ── Reward rolling ────────────────────────────────────────────────────────

REWARD_TABLES: dict[str, list[tuple[str, int, str]]] = {
    "treasure_roll_low": [("spirit_stones", 8, "common"), ("green_dew_herb", 1, "common")],
    "treasure_roll_medium": [("spirit_stones", 18, "common"), ("technique_fragment", 1, "uncommon")],
    "treasure_roll_high": [("spirit_stones", 35, "common"), ("technique_fragment", 2, "uncommon"), ("refined_beast_core", 1, "rare")],
    "stones_roll_low": [("spirit_stones", 5, "common")],
    "stones_roll_medium": [("spirit_stones", 15, "common")],
    "stones_roll_high": [("spirit_stones", 30, "common")],
    "herb_roll_low": [("green_dew_herb", 1, "common")],
    "herb_roll_medium": [("green_dew_herb", 2, "common"), ("village_herb", 1, "common")],
    "herb_roll_high": [("green_dew_herb", 3, "uncommon"), ("moonlotus", 1, "rare")],
    "pill_roll_low": [("qi_gathering_pill", 1, "uncommon")],
    "pill_roll_medium": [("qi_gathering_pill", 2, "uncommon")],
    "pill_roll_high": [("qi_gathering_pill", 1, "uncommon"), ("root_reforging_pill", 1, "rare")],
    "manual_roll_low": [("manual_pool:explore_low", 1, "rare")],
    "manual_roll_medium": [("manual_pool:explore_medium", 1, "rare")],
    "manual_roll_high": [("manual_pool:explore_high", 1, "legendary")],
    "beast_core": [("refined_beast_core", 1, "uncommon")],
    "qi_burst_low": [("qi_burst", 1, "common")],
    "qi_burst_medium": [("qi_burst", 2, "uncommon")],
    "qi_burst_high": [("qi_burst", 3, "rare")],
    "safe_skip": [],
}


def roll_reward_id(
    reward_id: str,
    session: Session,
    player: Player,
    area: ExploreArea | None,
    rng: random.Random,
) -> list[RewardEntry]:
    if reward_id.startswith("manual_pool:"):
        pool_id = reward_id.split(":", 1)[1]
        drops: dict[str, int] = {}
        msg = roll_manual_pool_reward(session, player.id, pool_id, rng, drops)
        if msg is not None:
            for item_id, qty in drops.items():
                return [RewardEntry(item_id=item_id, quantity=qty, tier="rare", msg=msg)]
        return []

    entries = REWARD_TABLES.get(reward_id, [])
    results: list[RewardEntry] = []
    for item_id, qty, tier in entries:
        if item_id == "qi_burst":
            continue
        results.append(RewardEntry(item_id=item_id, quantity=qty, tier=tier))
    return results


def roll_step_rewards(
    reward_ids: list[str] | None,
    session: Session,
    player: Player,
    area: ExploreArea | None,
    rng: random.Random,
    affinity: float = 1.0,
) -> list[RewardEntry]:
    if not reward_ids:
        return []
    results: list[RewardEntry] = []
    for rid in reward_ids:
        if rid in ("safe_skip",):
            continue
        if rid == "reputation":
            continue
        if rid == "karma_loss":
            continue
        if rid == "insight":
            continue
        if rid == "discount":
            continue
        if rid == "curse":
            continue
        if rid == "legendary_chance":
            continue
        rolled = roll_reward_id(rid, session, player, area, rng)
        mult = affinity if area else 1.0
        for r in rolled:
            qty = max(1, int(r.quantity * mult))
            results.append(RewardEntry(item_id=r.item_id, quantity=qty, tier=r.tier, msg=r.msg))
    return results


def roll_completion_rewards(
    session: Session,
    player: Player,
    area: ExploreArea | None,
    steps_cleared: int,
    total_steps: int,
    affinity: float,
    rng: random.Random,
) -> list[RewardEntry]:
    results: list[RewardEntry] = []
    progress_ratio = steps_cleared / max(total_steps, 1)
    if progress_ratio < 0.5:
        return results
    bonus_stones = int(20 * area.reward_multiplier * affinity) if area else 20
    results.append(RewardEntry(item_id="spirit_stones", quantity=bonus_stones, tier="common"))
    if rng.random() < 0.3 * affinity:
        results.append(RewardEntry(item_id="qi_gathering_pill", quantity=1, tier="uncommon"))
    if rng.random() < 0.15 * affinity:
        roll_manual_pool_reward(session, player.id, "explore_high", rng, {"manual_drop": 1})
        results.append(RewardEntry(item_id="manual_drop", quantity=1, tier="rare"))
    return results


def roll_qi_burst(affinity: float, rng: random.Random) -> int:
    chance = 0.12 * affinity
    if rng.random() < chance:
        return rng.randint(1, 3) * 10
    return 0


# ── Session lifecycle ─────────────────────────────────────────────────────

def get_active_explore(
    db_session: Session,
    guild_id: str,
    discord_id: str,
) -> ExploreSession | None:
    from sqlalchemy import select
    stmt = (
        select(ExploreSession)
        .where(ExploreSession.guild_id == guild_id)
        .where(ExploreSession.discord_id == discord_id)
        .where(ExploreSession.completed == False)
        .where(ExploreSession.failed == False)
        .where(ExploreSession.retired == False)
        .where(ExploreSession.expires_at > _utcnow())
        .order_by(ExploreSession.created_at.desc())
    )
    return db_session.execute(stmt).scalar_one_or_none()


def _serialize_state(state: ExploreState) -> str:
    return json.dumps({
        "current_hp": state.current_hp,
        "max_hp": state.max_hp,
        "rewards": state.rewards,
        "flags": state.flags,
        "used_encounter_ids": state.used_encounter_ids,
        "encounter_sequence": state.encounter_sequence,
        "combat_damage_taken": state.combat_damage_taken,
        "steps_cleared": state.steps_cleared,
    })


def _deserialize_state(row: ExploreSession) -> ExploreState:
    raw = json.loads(row.state_json)
    return ExploreState(
        current_hp=int(raw.get("current_hp", 0)),
        max_hp=int(raw.get("max_hp", 0)),
        rewards=list(raw.get("rewards", [])),
        flags=dict(raw.get("flags", {})),
        used_encounter_ids=list(raw.get("used_encounter_ids", [])),
        encounter_sequence=list(raw.get("encounter_sequence", [])),
        combat_damage_taken=int(raw.get("combat_damage_taken", 0)),
        steps_cleared=int(raw.get("steps_cleared", 0)),
    )


def check_explore_cooldown(player: Player) -> tuple[bool, str]:
    if player.last_explore_at is None:
        return False, ""
    elapsed = _utcnow() - _as_utc(player.last_explore_at)
    remaining = timedelta(hours=EXPLORE_COOLDOWN_HOURS) - elapsed
    if remaining.total_seconds() <= 0:
        return False, ""
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    return True, f"Cooldown: {hours}h {minutes}m remaining."


def start_explore(
    db_session: Session,
    player: Player,
    guild_id: str,
    area_id: str,
) -> tuple[ExploreSession | None, str]:
    areas = load_explore_areas()
    if area_id not in areas:
        return None, f"Unknown area: {area_id}"

    area = areas[area_id]
    on_cooldown, msg = check_explore_cooldown(player)
    if on_cooldown:
        return None, msg

    existing = get_active_explore(db_session, guild_id, player.discord_id)
    if existing is not None:
        return None, "You already have an active expedition. Finish or retreat first."

    mod = get_character_modifiers(db_session, player)
    stats = compute_combat_stats(player, db_session, mod)
    max_hp = stats.max_hp

    if player.current_hp is None or player.current_hp <= 0:
        player.current_hp = max_hp
    player.current_hp = min(max_hp, max(1, player.current_hp))

    total_steps = random.randint(10, 15)
    if area.danger >= 1.4:
        total_steps = random.randint(12, 15)

    sequence = generate_encounter_sequence(area, total_steps)
    if not sequence:
        return None, f"No encounters found for {area.name}. Check config."

    state = ExploreState(
        current_hp=player.current_hp,
        max_hp=max_hp,
        encounter_sequence=[e.id for e in sequence],
    )

    expires_at = _utcnow() + timedelta(hours=EXPLORE_COOLDOWN_HOURS * 2)

    explore_row = ExploreSession(
        guild_id=guild_id,
        discord_id=player.discord_id,
        area_id=area_id,
        current_step=0,
        total_steps=total_steps,
        state_json=_serialize_state(state),
        expires_at=expires_at,
    )
    db_session.add(explore_row)
    db_session.flush()

    player.last_explore_at = _utcnow()
    db_session.add(player)
    db_session.flush()

    return explore_row, ""


def resolve_choice(
    db_session: Session,
    player: Player,
    explore_session: ExploreSession,
    choice_id: str,
) -> tuple[StepResult | None, str]:
    state = _deserialize_state(explore_session)
    sequence = _load_encounter_sequence(explore_session, state)
    areas = load_explore_areas()
    area = areas.get(explore_session.area_id)

    step_idx = explore_session.current_step
    if step_idx >= len(sequence):
        return None, "Expedition already complete."

    encounter = sequence[step_idx]
    choice = next((c for c in encounter.choices if c.id == choice_id), None)
    if choice is None:
        return None, f"Unknown choice: {choice_id}"

    messages: list[str] = []
    rewards_this_step: list[RewardEntry] = []
    hp_delta = 0
    combat_id = None
    rng = random.Random()

    affinity = calculate_affinity(player, area) if area else 1.0

    if choice.type == "combat":
        combat_id_res = _start_explore_combat(db_session, player, explore_session, area, choice)
        if combat_id_res is None:
            return None, "Failed to start combat."
        combat_id = combat_id_res
        hp_delta = 0

    elif choice.type == "stat_check":
        success, roll_total = resolve_stat_check(
            player, db_session, state, choice.stats or ["luck"],
            choice.dc, area=area,
        )
        if success:
            rewards_this_step = roll_step_rewards(
                choice.success_rewards, db_session, player, area, rng, affinity,
            )
            messages.append(f"Stat check passed! (roll: {roll_total} vs DC {choice.dc})")
        else:
            dmg = int(state.max_hp * choice.fail_damage_pct)
            state.current_hp = max(1, state.current_hp - dmg)
            hp_delta = -dmg
            messages.append(f"Stat check failed. (roll: {roll_total} vs DC {choice.dc}) — Lost {dmg} HP.")

    elif choice.type == "treasure":
        rewards_this_step = roll_step_rewards(
            choice.success_rewards, db_session, player, area, rng, affinity,
        )
        messages.append("You search and find rewards!")

    elif choice.type == "rest":
        heal = int(state.max_hp * choice.heal_pct) if choice.heal_pct > 0 else int(state.max_hp * 0.30)
        old_hp = state.current_hp
        state.current_hp = min(state.max_hp, state.current_hp + heal)
        hp_delta = state.current_hp - old_hp
        messages.append(f"You rest and recover **{hp_delta}** HP.")

    elif choice.type == "dao_event":
        qi_gain = roll_qi_burst(affinity, rng)
        if qi_gain > 0:
            messages.append(f"Qi resonates! You gain **{qi_gain}** cultivation qi.")
        else:
            messages.append("The dao lingers but you cannot grasp it this time.")
        rewards_this_step = roll_step_rewards(
            choice.success_rewards, db_session, player, area, rng, affinity,
        )

    elif choice.type == "moral":
        if choice.moral == "righteous":
            player.reputation = min(100, player.reputation + 1)
            messages.append("You walk the righteous path. Reputation +1.")
        elif choice.moral == "demonic":
            player.karma = max(-100, player.karma - 2)
            messages.append("You walk the demonic path. Karma -2.")
        rewards_this_step = roll_step_rewards(
            choice.success_rewards, db_session, player, area, rng, affinity,
        )

    elif choice.type == "pay_stones":
        if player.spirit_stones >= choice.amount:
            player.spirit_stones -= choice.amount
            messages.append(f"You pay **{choice.amount}** spirit stones.")
        else:
            state.current_hp = max(1, state.current_hp - int(state.max_hp * 0.10))
            hp_delta = -int(state.max_hp * 0.10)
            messages.append(f"Not enough stones! You take **{abs(hp_delta)}** damage instead.")

    elif choice.type == "risk_reward":
        roll = rng.randint(1, 10)
        if roll > 7 - min(choice.risk, 6):
            rewards_this_step = roll_step_rewards(
                choice.success_rewards, db_session, player, area, rng, affinity,
            )
            messages.append("Risky gamble paid off!")
        else:
            dmg = int(state.max_hp * choice.fail_damage_pct) if choice.fail_damage_pct > 0 else int(state.max_hp * 0.15)
            state.current_hp = max(1, state.current_hp - dmg)
            hp_delta = -dmg
            messages.append(f"The risk backfired. Lost **{dmg}** HP.")

    elif choice.type == "trap":
        success, roll_total = resolve_stat_check(
            player, db_session, state, choice.stats or ["agility"],
            choice.dc or 55, area=area,
        )
        if success:
            messages.append(f"You avoid the trap! (roll: {roll_total})")
        else:
            dmg = int(state.max_hp * choice.fail_damage_pct)
            state.current_hp = max(1, state.current_hp - dmg)
            hp_delta = -dmg
            messages.append(f"Triggered the trap! Lost **{dmg}** HP. (roll: {roll_total})")
        rewards_this_step = roll_step_rewards(
            choice.success_rewards, db_session, player, area, rng, affinity,
        )

    elif choice.type == "safe_skip":
        messages.append("You pass by without incident.")

    elif choice.type == "boss":
        combat_id_res = _start_explore_combat(db_session, player, explore_session, area, choice)
        if combat_id_res is None:
            return None, "Failed to start boss combat."
        combat_id = combat_id_res

    for reward in rewards_this_step:
        state.rewards.append({
            "item_id": reward.item_id,
            "quantity": reward.quantity,
            "tier": reward.tier,
            "msg": reward.msg,
        })

    state.steps_cleared += 1
    new_step = step_idx + 1
    finished = new_step >= explore_session.total_steps or state.current_hp <= 0
    failed = state.current_hp <= 0
    retired = False

    explore_session.current_step = new_step
    explore_session.state_json = _serialize_state(state)
    explore_session.completed = finished and not failed and not retired
    explore_session.failed = failed
    db_session.add(explore_session)
    player.current_hp = state.current_hp
    db_session.add(player)

    next_encounter = sequence[new_step] if new_step < len(sequence) else None

    return StepResult(
        new_step=new_step,
        total_steps=explore_session.total_steps,
        encounter=next_encounter,
        finished=finished,
        failed=failed,
        retired=retired,
        messages=messages,
        hp_delta=hp_delta,
        rewards_this_step=rewards_this_step,
        combat_id=combat_id,
    ), ""


def _load_encounter_sequence(
    explore_session: ExploreSession,
    state: ExploreState,
) -> list[ExploreEncounter]:
    pool = load_explore_encounters(explore_session.area_id)
    pool_map = {e.id: e for e in pool}
    return [pool_map[eid] for eid in state.encounter_sequence if eid in pool_map]


def _start_explore_combat(
    db_session: Session,
    player: Player,
    explore_session: ExploreSession,
    area: ExploreArea | None,
    choice: ExploreChoice,
) -> int | None:
    mod = get_character_modifiers(db_session, player)
    stats = compute_combat_stats(player, db_session, mod)
    enemy = choice.enemy
    if enemy is None:
        return None

    realm_mult = 1.0 + player.realm_index * 0.25
    if area:
        realm_mult *= area.danger

    hp = int(enemy.get("hp_mult", 1.0) * 80 * realm_mult)
    atk = int(enemy.get("atk_mult", 1.0) * 15 * realm_mult)
    defense = int(enemy.get("def_mult", 1.0) * 8 * realm_mult)
    spd = int(enemy.get("spd_mult", 1.0) * 10)
    traits = list(enemy.get("traits", []))

    opponent = OpponentTemplate(
        opponent_id=f"explore_{choice.id}",
        name=enemy.get("name", "Unknown Enemy"),
        hp=max(1, hp),
        attack=max(1, atk),
        defense=max(1, defense),
        speed=max(5, spd),
        traits=traits,
    )

    combat_state = create_combat_state(
        stats,
        opponent,
        context="explore",
        context_meta={"explore_session_id": explore_session.id},
    )

    active = create_active_combat(
        db_session, player, combat_state,
        context="explore",
        context_key=str(explore_session.id),
    )
    return active.id


def handle_explore_combat_end(
    db_session: Session,
    player: Player,
    combat_state: CombatState,
) -> tuple[StepResult | None, str]:
    explore_id = combat_state.context_meta.get("explore_session_id")
    if explore_id is None:
        return None, "No associated explore session."

    explore_row = db_session.get(ExploreSession, explore_id)
    if explore_row is None:
        return None, "Explore session not found."

    state = _deserialize_state(explore_row)
    hp_before = state.current_hp

    if combat_state.fled:
        state.current_hp = max(1, state.current_hp - int(state.max_hp * 0.20))
        messages = ["You fled the fight, losing some ground."]
    elif combat_state.victory:
        state.current_hp = combat_state.player.hp
        messages = ["Victory! Combat rewards secured."]
    else:
        state.current_hp = max(0, state.current_hp - int(state.max_hp * 0.30))
        messages = ["You were defeated in combat."]

    hp_delta = state.current_hp - hp_before
    reward_entries: list[RewardEntry] = []
    if combat_state.victory:
        rng = random.Random()
        aff = 1.0
        areas = load_explore_areas()
        area = areas.get(explore_row.area_id)
        if area:
            aff = calculate_affinity(player, area)
        reward_entries = roll_step_rewards(
            ["stones_roll_medium", "treasure_roll_low"],
            db_session, player, area, rng, aff,
        )
        for r in reward_entries:
            state.rewards.append({
                "item_id": r.item_id,
                "quantity": r.quantity,
                "tier": r.tier,
                "msg": r.msg,
            })

    if state.current_hp <= 0:
        explore_row.failed = True
        _apply_death_reward_loss(state)
        messages.append("Expedition failed! You lost most unsecured rewards.")
    else:
        state.steps_cleared += 1
        explore_row.current_step += 1
        if explore_row.current_step >= explore_row.total_steps:
            explore_row.completed = True
            messages.append("Expedition complete!")

    explore_row.state_json = _serialize_state(state)
    player.current_hp = state.current_hp
    db_session.add(explore_row)
    db_session.add(player)

    seq = _load_encounter_sequence(explore_row, state)
    next_enc = seq[explore_row.current_step] if explore_row.current_step < len(seq) else None

    return StepResult(
        new_step=explore_row.current_step,
        total_steps=explore_row.total_steps,
        encounter=next_enc,
        finished=explore_row.completed or explore_row.failed,
        failed=explore_row.failed,
        retired=False,
        messages=messages,
        hp_delta=hp_delta,
        rewards_this_step=reward_entries,
    ), ""


def _apply_death_reward_loss(state: ExploreState) -> None:
    kept: list[dict] = []
    for r in state.rewards:
        if r.get("tier") in ("common", "minor"):
            kept.append(r)
    keep_count = max(1, int(len(kept) * 0.4))
    state.rewards = kept[:keep_count]


def retreat(
    db_session: Session,
    player: Player,
    explore_session: ExploreSession,
) -> StepResult:
    state = _deserialize_state(explore_session)
    explore_session.retired = True
    explore_session.completed = True
    db_session.add(explore_session)
    player.current_hp = state.current_hp
    db_session.add(player)
    return StepResult(
        new_step=explore_session.current_step,
        total_steps=explore_session.total_steps,
        encounter=None,
        finished=True,
        failed=False,
        retired=True,
        messages=["You retreat from the expedition, securing your current finds."],
        hp_delta=0,
        rewards_this_step=[],
    )


def grant_explore_rewards(
    db_session: Session,
    player: Player,
    explore_session: ExploreSession,
    state: ExploreState,
) -> list[str]:
    msgs: list[str] = []
    for reward in state.rewards:
        item_id = reward.get("item_id", "")
        qty = int(reward.get("quantity", 0))
        if qty <= 0:
            continue
        if item_id == "qi_burst":
            player.qi += qty * 10
            msgs.append(f"🌊 **+{qty * 10}** cultivation qi from qi burst.")
        elif item_id == "spirit_stones":
            player.spirit_stones += qty
            msgs.append(f"💎 **+{qty}** spirit stones.")
        elif item_id == "manual_drop":
            msgs.append(reward.get("msg", "You obtained a manual."))
        elif item_id.startswith("manual_pool:"):
            msgs.append(reward.get("msg", "You obtained a manual."))
        else:
            added = add_item(db_session, player.id, item_id, qty)
            name = get_item_name(item_id)
            msgs.append(f"📦 **+{qty}× {name}**")
    return msgs


def finalize_explore(
    db_session: Session,
    player: Player,
    explore_session: ExploreSession,
) -> tuple[list[str], bool]:
    state = _deserialize_state(explore_session)
    areas = load_explore_areas()
    area = areas.get(explore_session.area_id)
    affinity = calculate_affinity(player, area) if area else 1.0
    rng = random.Random()

    completion_rewards: list[RewardEntry] = []
    if explore_session.completed and not explore_session.failed and not explore_session.retired:
        completion_rewards = roll_completion_rewards(
            db_session, player, area, state.steps_cleared,
            explore_session.total_steps, affinity, rng,
        )
        for cr in completion_rewards:
            state.rewards.append({
                "item_id": cr.item_id,
                "quantity": cr.quantity,
                "tier": cr.tier,
                "msg": cr.msg,
            })

    qi_burst = roll_qi_burst(affinity, rng)
    if qi_burst > 0:
        state.rewards.append({
            "item_id": "qi_burst",
            "quantity": qi_burst // 10,
            "tier": "rare",
            "msg": "",
        })

    explore_row_id = explore_session.id
    msgs = grant_explore_rewards(db_session, player, explore_session, state)
    db_session.add(player)
    db_session.flush()

    delete_active_combat_for_explore(db_session, player.id)

    return msgs, bool(completion_rewards or qi_burst)


def delete_active_combat_for_explore(db_session: Session, player_id: int) -> None:
    active = get_active_combat(db_session, player_id)
    if active is not None:
        db_session.delete(active)


def build_hp_bar(current: int, max_hp: int, length: int = 12) -> str:
    if max_hp <= 0:
        return ""
    ratio = max(0, min(1.0, current / max_hp))
    filled = int(ratio * length)
    bar = "\U0001f7e9" * filled + "\u2b1c" * (length - filled)
    return f"{bar} **{current}/{max_hp}**"


def build_explore_step_embed(
    area_name: str,
    step: int,
    total_steps: int,
    encounter_title: str,
    encounter_text: str,
    current_hp: int,
    max_hp: int,
    affinity: float = 1.0,
    danger: float = 1.0,
) -> discord.Embed:
    emb = discord.Embed(
        title=f"\U0001f332 {area_name} \u2014 Step {step}/{total_steps}",
        description=encounter_text,
        color=discord.Color.dark_green(),
    )
    hp_line = build_hp_bar(current_hp, max_hp)
    if hp_line:
        emb.add_field(name="HP", value=hp_line, inline=False)

    danger_label = "Low"
    if danger >= 1.4:
        danger_label = "Extreme"
    elif danger >= 1.2:
        danger_label = "High"
    elif danger >= 1.0:
        danger_label = "Medium"

    emb.add_field(name="Danger", value=danger_label, inline=True)
    if affinity != 1.0:
        bonus = int((affinity - 1.0) * 100)
        emb.add_field(name="Affinity", value=f"+{bonus}%", inline=True)

    return emb


def build_explore_result_embed(
    area_name: str,
    result: StepResult,
    rewards: list[dict],
) -> discord.Embed:
    title_emoji = "\u2705" if result.finished and not result.failed else "\U0001f480" if result.failed else "\U0001f3f3\ufe0f"
    title = f"{title_emoji} Expedition {'Complete' if not result.failed and not result.retired else 'Failed' if result.failed else 'Retreated'}"
    emb = discord.Embed(
        title=title,
        description=f"Steps cleared: {result.new_step}/{result.total_steps}",
        color=discord.Color.green() if not result.failed else discord.Color.red(),
    )
    if result.messages:
        emb.add_field(name="Outcome", value="\n".join(result.messages), inline=False)
    summary = build_explore_reward_summary(rewards)
    if summary:
        emb.add_field(name="Rewards", value=summary, inline=False)
    return emb


def build_explore_reward_summary(rewards: list[dict]) -> str:
    if not rewards:
        return "No rewards secured."
    lines: list[str] = []
    by_tier: dict[str, list[dict]] = {}
    for r in rewards:
        tier = r.get("tier", "common")
        by_tier.setdefault(tier, []).append(r)

    for tier_label in ("legendary", "rare", "uncommon", "common", "minor"):
        items = by_tier.get(tier_label, [])
        if not items:
            continue
        prefix = {"legendary": "✦", "rare": "★", "uncommon": "●", "common": "•", "minor": "·"}.get(tier_label, "•")
        for r in items:
            qty = int(r.get("quantity", 0))
            name = r.get("item_id", "?")
            if name == "qi_burst":
                name = f"Cultivation Qi ({qty * 10})"
                qty = 0
            elif name == "spirit_stones":
                name = "Spirit Stones"
            elif name == "manual_drop":
                name = "Manual"
                msg = r.get("msg", "")
                if msg:
                    lines.append(f"{prefix} {msg}")
                    continue
            if qty > 0:
                lines.append(f"{prefix} **{qty}× {name}**")
            else:
                lines.append(f"{prefix} {name}")
    return "\n".join(lines)
