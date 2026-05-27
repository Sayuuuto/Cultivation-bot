from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .character import compute_adventure_defense, compute_adventure_power, get_character_modifiers
from .ui.formatting import RARE_EVENT_FLAIR
from .combat.engine import create_combat_state, opponent_from_monster
from .combat.monsters import get_monster
from .combat.session import create_active_combat, get_active_combat
from .combat_stats import compute_combat_stats
from .content import AreaDef, DropEntry, RareEventDef, get_area
from .effects import consume_effect_charge
from .inventory import add_item, get_item_name
from .models import ActiveAdventure, AdventureRun, Player


STANCES = {
    "cautious": {"success": 0.08, "drop_mult": 0.85},
    "balanced": {"success": 0.0, "drop_mult": 1.0},
    "reckless": {"success": -0.05, "drop_mult": 1.25},
}

SEGMENTS_PER_RUN = 2

PITY_BOOST_PER_SEGMENT = 0.05
PITY_BOOST_CAP = 0.25
PITY_HINT_THRESHOLD = 3

RARE_EVENT_REWARDS: dict[str, dict[str, int | str | float]] = {
    "hidden_herb_patch": {"green_dew_herb": 3, "moonlotus": 1},
    "wandering_elder": {
        "effect": "qi_gathering",
        "charges": 1,
        "manual_pool": "elder_mortal",
        "manual_chance": 1.0,
    },
    "ancient_cache": {"affix_stone": 1, "ancient_dust": 2, "blank_scroll": 1},
    "ambush": {"bandit_token": 2, "spirit_stones": 15, "technique_fragment": 1},
    "abandoned_cart": {"spirit_iron_shard": 2, "ember_moss": 2, "technique_fragment": 1},
    "hidden_moonwell": {"moonlotus": 2, "moonwell_tonic": 1, "script_shard": 1},
    "inheritance_fragment": {
        "root_reforging_pill": 1,
        "manual_pool": "inheritance_earth",
        "manual_chance": 1.0,
    },
}

ENCOUNTERS_PATH = Path(__file__).resolve().parent.parent / "config" / "adventure_encounters.json"
_encounters: dict[str, list[dict]] | None = None


@dataclass
class AdventureChoice:
    id: str
    label: str
    success_bonus: float
    drop_mult: float
    fail_chance: float
    karma_delta: int = 0
    manual_pool: str | None = None
    manual_chance: float = 1.0
    spirit_stones: int = 0


@dataclass
class AdventureEncounter:
    id: str
    prompt: str
    encounter_type: str
    choices: tuple[AdventureChoice, ...] = ()
    monster_id: str | None = None


@dataclass
class AdventureResult:
    success: bool
    outcome: str
    area_name: str
    stance: str
    segments_cleared: int
    drops: dict[str, int] = field(default_factory=dict)
    rare_events: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    qi_delta: int = 0
    stones_delta: int = 0
    failed_run: bool = False


@dataclass
class PendingAdventure:
    active_id: int
    area_name: str
    segment: int
    segments_total: int
    prompt: str
    choices: tuple[AdventureChoice, ...] = ()
    messages: list[str] = field(default_factory=list)
    encounter_type: str = "choice"
    combat_id: int | None = None
    monster_name: str | None = None
    player_hp: int | None = None
    player_max_hp: int | None = None
    opponent_hp: int | None = None
    opponent_max_hp: int | None = None


def _load_encounters() -> dict[str, list[dict]]:
    global _encounters
    if _encounters is None:
        with ENCOUNTERS_PATH.open(encoding="utf-8") as f:
            _encounters = json.load(f)
    return _encounters


def get_encounters_for_area(area_id: str) -> list[AdventureEncounter]:
    raw = _load_encounters().get(area_id, [])
    encounters: list[AdventureEncounter] = []
    for entry in raw:
        encounter_type = entry.get("type", "choice")
        if encounter_type == "combat":
            encounters.append(
                AdventureEncounter(
                    id=entry["id"],
                    prompt=entry["prompt"],
                    encounter_type="combat",
                    monster_id=entry.get("monster_id"),
                )
            )
            continue
        choices = tuple(
            AdventureChoice(
                id=c["id"],
                label=c["label"],
                success_bonus=float(c.get("success_bonus", 0)),
                drop_mult=float(c.get("drop_mult", 1.0)),
                fail_chance=float(c.get("fail_chance", 0.1)),
                karma_delta=int(c.get("karma_delta", 0)),
                manual_pool=c.get("manual_pool"),
                manual_chance=float(c.get("manual_chance", 1.0)),
                spirit_stones=int(c.get("spirit_stones", 0)),
            )
            for c in entry.get("choices", [])
        )
        encounters.append(
            AdventureEncounter(
                id=entry["id"],
                prompt=entry["prompt"],
                encounter_type=encounter_type,
                choices=choices,
            )
        )
    return encounters


def _default_encounter(segment: int) -> AdventureEncounter:
    return AdventureEncounter(
        id=f"generic_{segment}",
        prompt="The path narrows. How do you proceed?",
        encounter_type="choice",
        choices=(
            AdventureChoice("steady", "Press forward steadily", 0.05, 1.0, 0.08),
            AdventureChoice("scout", "Scout from cover", 0.1, 0.85, 0.05),
            AdventureChoice("rush", "Rush through", -0.05, 1.2, 0.16),
        ),
    )


def _encounter_by_id(area_id: str, encounter_id: str, segment: int) -> AdventureEncounter:
    encounters = get_encounters_for_area(area_id)
    encounter = next((e for e in encounters if e.id == encounter_id), None)
    if encounter is None:
        from .novice_trial import NOVICE_SEGMENT2_ENCOUNTER, SAGE_TRIAL_ENCOUNTER

        if encounter_id == SAGE_TRIAL_ENCOUNTER.id:
            return SAGE_TRIAL_ENCOUNTER
        if encounter_id == NOVICE_SEGMENT2_ENCOUNTER.id:
            return NOVICE_SEGMENT2_ENCOUNTER
    if encounter is None:
        return _default_encounter(segment)
    return encounter


def _pick_encounter(rng: random.Random, area_id: str, segment: int, player: Player | None = None) -> AdventureEncounter:
    if player is not None:
        from .novice_trial import is_first_adventure, pick_novice_encounter

        if is_first_adventure(player):
            novice = pick_novice_encounter(segment)
            if novice is not None:
                return novice
    pool = get_encounters_for_area(area_id)
    if not pool:
        return _default_encounter(segment)
    return rng.choice(pool)


def _clamp_chance(value: float) -> float:
    return max(0.12, min(0.95, value))


def _roll_drop(rng: random.Random, drops: tuple[DropEntry, ...], qty_mult: float, luck: float) -> tuple[str, int] | None:
    if not drops:
        return None
    pool: list[DropEntry] = []
    for drop in drops:
        weight = max(1, int(drop.weight * (1.0 + luck * 0.2)))
        pool.extend([drop] * weight)
    chosen = rng.choice(pool)
    qty = rng.randint(chosen.min_qty, chosen.max_qty)
    qty = max(1, int(qty * qty_mult))
    return chosen.item_id, qty


def _pick_rare_event(rng: random.Random, events: tuple[RareEventDef, ...]) -> RareEventDef | None:
    if not events:
        return None
    total = sum(e.weight for e in events)
    roll = rng.randint(1, total)
    acc = 0
    for event in events:
        acc += event.weight
        if roll <= acc:
            return event
    return events[-1]


def _apply_rare_event(
    session: Session,
    player: Player,
    event: RareEventDef,
    area: AreaDef,
    drops: dict[str, int],
    messages: list[str],
    rng: random.Random | None = None,
) -> None:
    from .manuals import RARE_EVENT_META_KEYS, apply_rare_event_manual_reward

    rng = rng or random.Random()
    emoji, title = RARE_EVENT_FLAIR.get(event.id, ("✨", event.id.replace("_", " ").title()))
    messages.append(f"{emoji} **{title}** — {event.message}")
    rewards = RARE_EVENT_REWARDS.get(event.id, {})
    if "effect" in rewards:
        from .effects import add_effect

        add_effect(session, player.id, str(rewards["effect"]), charges=int(rewards.get("charges", 1)))
        messages.append("🌟 A fleeting blessing settles upon your meridians.")

    if "spirit_stones" in rewards:
        stones = int(rewards["spirit_stones"])
        player.spirit_stones += stones
        messages.append(f"💎 You gain **{stones}** spirit stones from the encounter.")

    for key, val in rewards.items():
        if key in RARE_EVENT_META_KEYS:
            continue
        if key == "spirit_stones":
            continue
        from .inventory import get_item_def

        if get_item_def(key) is not None or key in {d.item_id for d in area.drops}:
            drops[key] = drops.get(key, 0) + int(val)

    apply_rare_event_manual_reward(session, player, rewards, drops, messages, rng)


def _encounter_icon(encounter_type: str) -> str:
    if encounter_type == "combat":
        return "⚔️"
    return "📜"


def _pity_bonus(state: dict) -> float:
    segments = int(state.get("segments_since_rare", 0))
    return min(segments * PITY_BOOST_PER_SEGMENT, PITY_BOOST_CAP)


def _maybe_pity_hint(state: dict, messages: list[str]) -> None:
    segments = int(state.get("segments_since_rare", 0))
    if segments >= PITY_HINT_THRESHOLD:
        messages.append("_Fate stirs… something rare may be near._")


def _build_pending_from_encounter(
    active: ActiveAdventure,
    area: AreaDef,
    encounter: AdventureEncounter,
    state: dict,
    *,
    combat_id: int | None = None,
    combat_state=None,
) -> PendingAdventure:
    pending = PendingAdventure(
        active_id=active.id,
        area_name=area.name,
        segment=active.segment,
        segments_total=SEGMENTS_PER_RUN,
        prompt=encounter.prompt,
        choices=encounter.choices,
        messages=list(state.get("messages", [])),
        encounter_type=encounter.encounter_type,
        combat_id=combat_id,
    )
    if encounter.encounter_type == "combat" and combat_state is not None:
        pending.monster_name = combat_state.opponent_name
        pending.player_hp = combat_state.player.hp
        pending.player_max_hp = combat_state.player.max_hp
        pending.opponent_hp = combat_state.opponent.hp
        pending.opponent_max_hp = combat_state.opponent.max_hp
    elif encounter.monster_id:
        monster = get_monster(encounter.monster_id)
        if monster:
            pending.monster_name = monster.name
    return pending


def _start_combat_encounter(
    session: Session,
    player: Player,
    area_id: str,
    encounter: AdventureEncounter,
    state: dict,
) -> tuple[int | None, str | None]:
    if not encounter.monster_id:
        return None, "This combat encounter has no foe configured."
    monster = get_monster(encounter.monster_id)
    if monster is None:
        return None, "The foe vanished before the fight began."

    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    opponent = opponent_from_monster(
        monster.monster_id,
        monster.name,
        monster.hp,
        monster.attack,
        monster.defense,
        monster.speed,
        traits=list(monster.traits),
    )
    combat_state = create_combat_state(
        stats,
        opponent,
        context="adventure",
        context_meta={"area_id": area_id, "encounter_id": encounter.id, "monster_id": monster.monster_id},
    )
    active_combat = create_active_combat(
        session,
        player,
        combat_state,
        context="adventure",
        context_key=area_id,
    )
    state["pending_combat"] = True
    state["combat_encounter_id"] = encounter.id
    state.setdefault("messages", []).append(
        f"{_encounter_icon('combat')} **Combat!** {encounter.prompt}"
    )
    return active_combat.id, None


def _resolve_combat_segment(
    session: Session,
    player: Player,
    area: AreaDef,
    stance: str,
    state: dict,
    rng: random.Random,
    *,
    victory: bool,
) -> tuple[bool, bool]:
    """Returns (segment_success, run_failed)."""
    stance_mod = STANCES[stance]
    mod = get_character_modifiers(session, player)

    if victory:
        state["segments_cleared"] = int(state.get("segments_cleared", 0)) + 1
        drop_mult = stance_mod["drop_mult"]
        rolled = _roll_drop(rng, area.drops, drop_mult, mod.drop_luck)
        drops: dict[str, int] = state.setdefault("drops", {})
        if rolled:
            item_id, qty = rolled
            drops[item_id] = drops.get(item_id, 0) + qty
        state["messages"].append("Victory in combat — you claim spoils from the foe.")
    else:
        penalty = max(5, 8 + area.min_realm * 3)
        state["qi_penalty"] = int(state.get("qi_penalty", 0)) + penalty
        state["messages"].append(f"You were driven back from combat ({penalty} qi lost).")
        return False, True

    segments_since_rare = int(state.get("segments_since_rare", 0))
    pity_bonus = _pity_bonus(state)
    rare_roll = min(0.95, area.rare_event_chance + pity_bonus) * mod.rare_event_mult
    if stance == "reckless":
        rare_roll *= 1.1
    _maybe_pity_hint(state, state["messages"])
    if rng.random() < rare_roll:
        event = _pick_rare_event(rng, area.rare_events)
        if event:
            rare_events: list[str] = state.setdefault("rare_events", [])
            rare_events.append(event.id)
            _apply_rare_event(
                session, player, event, area, state.setdefault("drops", {}), state["messages"], rng
            )
            state["segments_since_rare"] = 0
    else:
        state["segments_since_rare"] = segments_since_rare + 1

    state.pop("pending_combat", None)
    state.pop("combat_encounter_id", None)
    return True, False


def _load_state(active: ActiveAdventure) -> dict:
    try:
        return json.loads(active.state_json)
    except json.JSONDecodeError:
        return {}


def _save_state(active: ActiveAdventure, state: dict) -> None:
    active.state_json = json.dumps(state)
    active.updated_at = datetime.now(timezone.utc)


def get_active_adventure(session: Session, player_id: int) -> ActiveAdventure | None:
    stmt = select(ActiveAdventure).where(ActiveAdventure.player_id == player_id)
    return session.execute(stmt).scalar_one_or_none()


def abandon_adventure(session: Session, player_id: int) -> tuple[bool, str]:
    active = get_active_adventure(session, player_id)
    if active is None:
        return False, "You have no adventure in progress."
    from .combat.session import delete_active_combat

    delete_active_combat(session, player_id)
    session.delete(active)
    return True, "You withdraw from the wilds. The path can wait."


def _validate_area(player: Player, area_id: str, stance: str) -> tuple[AreaDef | None, str | None]:
    if stance.lower() not in STANCES:
        return None, f"Invalid stance. Choose: {', '.join(STANCES)}."
    area = get_area(area_id)
    if area is None:
        return None, "That area is unknown."
    if player.realm_index < area.min_realm:
        return None, f"You are not ready for {area.name}. {area.recommended_text} recommended."
    return area, None


def start_adventure_session(
    session: Session,
    player: Player,
    area_id: str,
    stance: str,
    rng: random.Random | None = None,
) -> tuple[PendingAdventure | None, str | None]:
    rng = rng or random.Random()
    if get_active_adventure(session, player.id) is not None:
        return None, "You already have an adventure in progress. Use `/adventure continue` or `/adventure abandon`."

    area, err = _validate_area(player, area_id, stance)
    if err:
        return None, err
    assert area is not None

    stance = stance.lower()
    from .novice_trial import is_first_adventure

    encounter = _pick_encounter(rng, area_id, 1, player)
    state = {
        "drops": {},
        "messages": [f"You enter **{area.name}** with a {stance} stance."],
        "rare_events": [],
        "segments_cleared": 0,
        "segments_since_rare": 0,
        "qi_penalty": 0,
        "failed_run": False,
    }
    if is_first_adventure(player):
        state["novice_adventure"] = True
        state["messages"].append(
            "_The sect elders whisper: your first journey teaches karma — "
            "no choice here can end the run._"
        )

    active = ActiveAdventure(
        player_id=player.id,
        area_id=area_id,
        stance=stance,
        segment=1,
        encounter_id=encounter.id,
        state_json=json.dumps(state),
    )
    session.add(active)
    session.flush()

    combat_id = None
    combat_state = None
    if encounter.encounter_type == "combat":
        combat_id, err = _start_combat_encounter(session, player, area_id, encounter, state)
        if err:
            session.delete(active)
            return None, err
        active.state_json = json.dumps(state)
        if combat_id is not None:
            from .combat.session import load_combat_state as load_combat

            active_combat = get_active_combat(session, player.id)
            if active_combat is not None:
                combat_state = load_combat(active_combat)

    return (
        _build_pending_from_encounter(
            active, area, encounter, state, combat_id=combat_id, combat_state=combat_state
        ),
        None,
    )


def resume_adventure_session(
    session: Session,
    player: Player,
) -> tuple[PendingAdventure | None, str | None]:
    active = get_active_adventure(session, player.id)
    if active is None:
        return None, "No adventure in progress. Start one with `/adventure`."

    area = get_area(active.area_id)
    if area is None:
        session.delete(active)
        return None, "Your adventure area no longer exists."

    encounters = get_encounters_for_area(active.area_id)
    encounter = _encounter_by_id(active.area_id, active.encounter_id, active.segment)

    state = _load_state(active)
    combat_id = None
    combat_state = None
    if state.get("pending_combat"):
        active_combat = get_active_combat(session, player.id)
        if active_combat is not None:
            from .combat.session import load_combat_state as load_combat

            combat_id = active_combat.id
            combat_state = load_combat(active_combat)

    return (
        _build_pending_from_encounter(
            active, area, encounter, state, combat_id=combat_id, combat_state=combat_state
        ),
        None,
    )


def _apply_choice_karma(
    player: Player,
    choice: AdventureChoice,
    state: dict,
) -> None:
    from .karma import clamp_karma

    if not choice.karma_delta:
        return
    before = player.karma
    player.karma = clamp_karma(player.karma + choice.karma_delta)
    delta = player.karma - before
    if delta > 0:
        state["messages"].append(f"☯️ Your karma rises (**+{delta}** → **{player.karma}**).")
    elif delta < 0:
        state["messages"].append(f"☯️ Your karma falls (**{delta}** → **{player.karma}**).")
    if state.get("novice_adventure") and not state.get("karma_tutorial_shown"):
        state["karma_tutorial_shown"] = True
        state["messages"].append(
            "_Karma shapes breakthrough flavor and manual drops — righteous or demonic arts await those who commit._"
        )


def _apply_choice_rewards(
    session: Session,
    player: Player,
    choice: AdventureChoice,
    state: dict,
    rng: random.Random,
) -> None:
    from .manuals import roll_manual_pool_reward

    if choice.spirit_stones > 0:
        player.spirit_stones += choice.spirit_stones
        state["messages"].append(f"💎 You gain **{choice.spirit_stones}** spirit stones.")

    if choice.manual_pool:
        note = roll_manual_pool_reward(
            session,
            player.id,
            choice.manual_pool,
            rng,
            state.setdefault("drops", {}),
            chance=choice.manual_chance,
        )
        if note:
            state["messages"].append(note)


def _resolve_segment(
    session: Session,
    player: Player,
    area: AreaDef,
    stance: str,
    choice: AdventureChoice,
    state: dict,
    rng: random.Random,
    allow_catastrophic: bool = True,
) -> tuple[bool, bool]:
    """Returns (segment_success, run_failed)."""
    mod = get_character_modifiers(session, player)
    stance_mod = STANCES[stance]
    defense = compute_adventure_defense(mod)
    power = compute_adventure_power(mod, player)

    if allow_catastrophic and rng.random() < choice.fail_chance:
        penalty = max(5, 8 + area.min_realm * 3)
        state["qi_penalty"] = int(state.get("qi_penalty", 0)) + penalty
        state["messages"].append(
            f"Your choice — **{choice.label}** — backfires. You retreat, qi churning ({penalty} lost)."
        )
        return False, True

    _apply_choice_karma(player, choice, state)

    success_chance = _clamp_chance(
        area.base_success
        + stance_mod["success"]
        + mod.adventure_success
        + choice.success_bonus
        + min(0.12, power / 200.0)
    )
    success_chance = min(0.95, success_chance * defense)
    from .novice_trial import novice_adventure_success_floor

    floor = novice_adventure_success_floor(state)
    if floor > 0:
        success_chance = max(success_chance, floor)

    drop_mult = stance_mod["drop_mult"] * choice.drop_mult

    if rng.random() <= success_chance:
        state["segments_cleared"] = int(state.get("segments_cleared", 0)) + 1
        rolled = _roll_drop(rng, area.drops, drop_mult, mod.drop_luck)
        drops: dict[str, int] = state.setdefault("drops", {})
        if rolled:
            item_id, qty = rolled
            drops[item_id] = drops.get(item_id, 0) + qty
        state["messages"].append(f"**{choice.label}** pays off — you gather spoils.")
        _apply_choice_rewards(session, player, choice, state, rng)
    else:
        penalty = max(3, 5 + area.min_realm * 2)
        state["qi_penalty"] = int(state.get("qi_penalty", 0)) + penalty
        state["messages"].append(
            f"**{choice.label}** falters. You are forced back ({penalty} qi lost)."
        )

    segments_since_rare = int(state.get("segments_since_rare", 0))
    pity_bonus = _pity_bonus(state)
    rare_roll = min(0.95, area.rare_event_chance + pity_bonus) * mod.rare_event_mult
    if stance == "reckless":
        rare_roll *= 1.1
    _maybe_pity_hint(state, state["messages"])
    if rng.random() < rare_roll:
        event = _pick_rare_event(rng, area.rare_events)
        if event:
            rare_events: list[str] = state.setdefault("rare_events", [])
            rare_events.append(event.id)
            _apply_rare_event(
                session, player, event, area, state.setdefault("drops", {}), state["messages"], rng
            )
            state["segments_since_rare"] = 0
    else:
        state["segments_since_rare"] = segments_since_rare + 1

    return bool(state["segments_cleared"] > 0), False


def apply_adventure_choice(
    session: Session,
    player: Player,
    active_id: int,
    choice_id: str,
    rng: random.Random | None = None,
) -> tuple[PendingAdventure | AdventureResult | None, str | None]:
    rng = rng or random.Random()
    active = session.get(ActiveAdventure, active_id)
    if active is None or active.player_id != player.id:
        return None, "That adventure is no longer active."

    area = get_area(active.area_id)
    if area is None:
        session.delete(active)
        return None, "That area vanished from the map."

    encounter = _encounter_by_id(active.area_id, active.encounter_id, active.segment)

    if encounter.encounter_type == "combat":
        return None, "This segment is a combat encounter — use the combat buttons."

    choice = next((c for c in encounter.choices if c.id == choice_id), None)
    if choice is None:
        return None, "That choice is not available."

    state = _load_state(active)
    allow_catastrophic = not state.get("novice_adventure", False)
    _, run_failed = _resolve_segment(
        session, player, area, active.stance, choice, state, rng, allow_catastrophic=allow_catastrophic
    )

    if run_failed:
        state["failed_run"] = True

    current_segment = active.segment
    if run_failed or current_segment >= SEGMENTS_PER_RUN:
        return _finalize_adventure(session, player, area, active.stance, state, active), None

    next_segment = current_segment + 1
    next_encounter = _pick_encounter(rng, active.area_id, next_segment, player)
    active.segment = next_segment
    active.encounter_id = next_encounter.id
    _save_state(active, state)
    session.add(active)

    combat_id = None
    combat_state = None
    if next_encounter.encounter_type == "combat":
        combat_id, err = _start_combat_encounter(session, player, active.area_id, next_encounter, state)
        if err:
            return None, err
        _save_state(active, state)
        if combat_id is not None:
            from .combat.session import load_combat_state as load_combat

            active_combat = get_active_combat(session, player.id)
            if active_combat is not None:
                combat_state = load_combat(active_combat)

    return (
        _build_pending_from_encounter(
            active, area, next_encounter, state, combat_id=combat_id, combat_state=combat_state
        ),
        None,
    )


def apply_adventure_combat_outcome(
    session: Session,
    player: Player,
    active_id: int,
    *,
    victory: bool,
    fled: bool = False,
    rng: random.Random | None = None,
) -> tuple[PendingAdventure | AdventureResult | None, str | None]:
    rng = rng or random.Random()
    active = session.get(ActiveAdventure, active_id)
    if active is None or active.player_id != player.id:
        return None, "That adventure is no longer active."

    area = get_area(active.area_id)
    if area is None:
        session.delete(active)
        return None, "That area vanished from the map."

    state = _load_state(active)
    if fled:
        state["failed_run"] = True
        state["messages"].append("You fled the combat encounter.")
        return _finalize_adventure(session, player, area, active.stance, state, active), None

    _, run_failed = _resolve_combat_segment(
        session, player, area, active.stance, state, rng, victory=victory
    )
    if run_failed:
        state["failed_run"] = True

    current_segment = active.segment
    if run_failed or current_segment >= SEGMENTS_PER_RUN:
        _save_state(active, state)
        return _finalize_adventure(session, player, area, active.stance, state, active), None

    next_segment = current_segment + 1
    next_encounter = _pick_encounter(rng, active.area_id, next_segment, player)
    active.segment = next_segment
    active.encounter_id = next_encounter.id
    _save_state(active, state)
    session.add(active)

    combat_id = None
    combat_state = None
    if next_encounter.encounter_type == "combat":
        combat_id, err = _start_combat_encounter(session, player, active.area_id, next_encounter, state)
        if err:
            return None, err
        _save_state(active, state)
        if combat_id is not None:
            from .combat.session import load_combat_state as load_combat

            active_combat = get_active_combat(session, player.id)
            if active_combat is not None:
                combat_state = load_combat(active_combat)

    return (
        _build_pending_from_encounter(
            active, area, next_encounter, state, combat_id=combat_id, combat_state=combat_state
        ),
        None,
    )


def _finalize_adventure(
    session: Session,
    player: Player,
    area: AreaDef,
    stance: str,
    state: dict,
    active: ActiveAdventure | None = None,
) -> AdventureResult:
    mod = get_character_modifiers(session, player)
    consume_effect_charge(session, player.id, "swiftwind")
    if "tempering" in mod.active_effects:
        consume_effect_charge(session, player.id, "tempering")

    drops: dict[str, int] = state.get("drops", {})
    from .manuals import normalize_manual_drops

    drops = normalize_manual_drops(session, player.id, drops)
    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)

    qi_penalty = int(state.get("qi_penalty", 0))
    player.qi = max(0, player.qi - qi_penalty)

    segments_cleared = int(state.get("segments_cleared", 0))
    failed_run = bool(state.get("failed_run", False))
    if failed_run and segments_cleared == 0:
        outcome = "fail"
    elif segments_cleared == SEGMENTS_PER_RUN:
        outcome = "success"
    elif segments_cleared > 0:
        outcome = "partial"
    else:
        outcome = "fail"

    rare_events = list(state.get("rare_events", []))
    rewards_json = json.dumps({"drops": drops, "rare_events": rare_events, "qi_penalty": qi_penalty})
    session.add(
        AdventureRun(
            player_id=player.id,
            area_id=area.area_id,
            stance=stance,
            outcome=outcome,
            rewards_json=rewards_json,
        )
    )
    if active is not None:
        session.delete(active)

    messages = list(state.get("messages", []))
    drop_lines = [f"{get_item_name(k)} ×{v}" for k, v in sorted(drops.items())]
    if drop_lines:
        messages.append("Loot: " + ", ".join(drop_lines))
    if qi_penalty:
        messages.append(f"You lose {qi_penalty} qi from the journey.")

    return AdventureResult(
        success=segments_cleared > 0 and not failed_run,
        outcome=outcome,
        area_name=area.name,
        stance=stance,
        segments_cleared=segments_cleared,
        drops=drops,
        rare_events=rare_events,
        messages=messages,
        qi_delta=-qi_penalty,
        failed_run=failed_run,
    )


def run_adventure(
    session: Session,
    player: Player,
    area_id: str,
    stance: str,
    rng: random.Random | None = None,
) -> AdventureResult:
    """Auto-resolve adventure by picking balanced-ish choices (used in tests)."""
    rng = rng or random.Random()
    area, err = _validate_area(player, area_id, stance)
    if err:
        invalid = "underleveled" if area is not None else "invalid"
        return AdventureResult(
            success=False,
            outcome=invalid if area else "invalid",
            area_name=area.name if area else "",
            stance=stance,
            segments_cleared=0,
            messages=[err],
        )

    assert area is not None
    stance = stance.lower()
    state: dict = {
        "drops": {},
        "messages": [f"You enter **{area.name}** with a {stance} stance."],
        "rare_events": [],
        "segments_cleared": 0,
        "segments_since_rare": 0,
        "qi_penalty": 0,
        "failed_run": False,
    }

    for segment in range(1, SEGMENTS_PER_RUN + 1):
        encounter = _pick_encounter(rng, area_id, segment)
        if not encounter.choices:
            encounter = _default_encounter(segment)
        choice = min(encounter.choices, key=lambda c: c.fail_chance)
        _, run_failed = _resolve_segment(
            session, player, area, stance, choice, state, rng, allow_catastrophic=False
        )
        if run_failed:
            state["failed_run"] = True
            break

    return _finalize_adventure(session, player, area, stance, state, active=None)
