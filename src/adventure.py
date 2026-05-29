from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .area_risk import (
    adventure_realm_modifiers,
    realm_gap,
    underleveled_drop_bonus,
    underleveled_entry_message,
)
from .character import compute_adventure_defense, compute_adventure_power, get_character_modifiers
from .ui.formatting import RARE_EVENT_FLAIR
from .combat.engine import create_combat_state, opponent_from_monster
from .combat.monsters import get_monster
from .combat.session import create_active_combat, get_active_combat
from .combat_stats import compute_combat_stats, scale_monster_stats
from .content import AreaDef, DropEntry, RareEventDef, get_area, resolve_area_id
from .effects import consume_effect_charge
from .inventory import add_item, get_item_name
from .models import ActiveAdventure, AdventureRun, Player


STANCES = {
    "cautious": {"success": 0.08, "drop_mult": 0.85},
    "balanced": {"success": 0.0, "drop_mult": 1.0},
    "reckless": {"success": -0.05, "drop_mult": 1.25},
}
DEFAULT_STANCE = "balanced"

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
    "market_day": {"village_herb": 4, "green_dew_herb": 2, "spirit_stones": 8},
    "lost_child": {"village_herb": 2, "technique_fragment": 1},
    "deep_grove": {"pine_resin": 3, "refined_beast_core": 1},
    "sinking_road": {"bog_iron": 2, "hollow_pearl": 1, "swamp_moss": 2},
    "cursed_shrine": {"interactive": 1},
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
    reputation_delta: int = 0
    manual_pool: str | None = None
    manual_chance: float = 1.0
    spirit_stones: int = 0
    sect_invitation: str | None = None
    route_tag: str | None = None
    route_label: str | None = None
    route_tone: str | None = None
    next_encounter_id: str | None = None


@dataclass
class AdventureEncounter:
    id: str
    prompt: str
    encounter_type: str
    choices: tuple[AdventureChoice, ...] = ()
    monster_id: str | None = None
    is_boss: bool = False
    route_tags: tuple[str, ...] = ()
    segment_role: str = "standard"


def cursed_shrine_choices() -> tuple[AdventureChoice, ...]:
    return (
        AdventureChoice(
            "accept",
            "Accept the shrine's dark boon",
            success_bonus=0.0,
            drop_mult=1.0,
            fail_chance=0.0,
            karma_delta=-8,
            reputation_delta=-10,
        ),
        AdventureChoice(
            "decline",
            "Decline and withdraw",
            success_bonus=0.0,
            drop_mult=1.0,
            fail_chance=0.0,
            karma_delta=3,
            reputation_delta=5,
        ),
    )


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
    target_segments: int = SEGMENTS_PER_RUN
    route_label: str | None = None
    route_steps: list[str] = field(default_factory=list)


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
    route_label: str | None = None
    route_tag: str | None = None


def _load_encounters() -> dict[str, list[dict]]:
    global _encounters
    if _encounters is None:
        with ENCOUNTERS_PATH.open(encoding="utf-8") as f:
            _encounters = json.load(f)
    return _encounters


def _generic_realm_encounters(area: AreaDef | None) -> list[AdventureEncounter]:
    area_name = area.name if area is not None else "the wilds"
    return [
        AdventureEncounter(
            id="realm_route_choice",
            prompt=(
                f"The qi in **{area_name}** divides into two trails: one follows signs of "
                "travelers, the other sinks toward untamed spirit pressure."
            ),
            encounter_type="route_choice",
            choices=(
                AdventureChoice(
                    "traveler_road",
                    "Follow the marked traveler road",
                    0.04,
                    0.95,
                    0.06,
                    karma_delta=3,
                    route_tag="traveler_road",
                    route_label="Traveler Road",
                ),
                AdventureChoice(
                    "wild_pressure",
                    "Enter the deeper spirit pressure",
                    -0.03,
                    1.18,
                    0.14,
                    karma_delta=-3,
                    route_tag="wild_pressure",
                    route_label="Deeper Spirit Pressure",
                ),
            ),
        ),
        AdventureEncounter(
            id="realm_traveler_cache",
            prompt=f"A damaged supply cache lies beside the road through **{area_name}**.",
            encounter_type="choice",
            route_tags=("traveler_road",),
            choices=(
                AdventureChoice("return", "Mark it for its owner", 0.05, 0.9, 0.05, karma_delta=6),
                AdventureChoice("claim", "Claim the useful supplies", -0.02, 1.2, 0.12, karma_delta=-4),
            ),
        ),
        AdventureEncounter(
            id="realm_pressure_crossing",
            prompt=f"Heavy qi gathers into a crossing that tests your footing in **{area_name}**.",
            encounter_type="choice",
            route_tags=("wild_pressure",),
            choices=(
                AdventureChoice("steady", "Advance with measured breath", 0.05, 0.95, 0.06, karma_delta=3),
                AdventureChoice("force", "Force your way through", -0.04, 1.25, 0.16, karma_delta=-4),
            ),
        ),
        AdventureEncounter(
            id="realm_final_find",
            prompt=f"The route through **{area_name}** opens onto a qi-rich cache.",
            encounter_type="choice",
            route_tags=("traveler_road", "wild_pressure"),
            segment_role="climax",
            choices=(
                AdventureChoice("share", "Take a fair share and leave the rest", 0.06, 1.0, 0.05, karma_delta=5),
                AdventureChoice("strip", "Strip the cache bare", -0.02, 1.3, 0.12, karma_delta=-8),
            ),
        ),
    ]


def get_encounters_for_area(area_id: str) -> list[AdventureEncounter]:
    canonical_id = resolve_area_id(area_id) or area_id
    raw_map = _load_encounters()
    raw = raw_map.get(area_id) or raw_map.get(canonical_id)
    if raw is None and canonical_id == "mortal_grove":
        raw = raw_map.get("bamboo_grove")
    elif raw is None and canonical_id == "qi_refining_cliffs":
        raw = raw_map.get("ashen_cliff")
    elif raw is None and canonical_id == "foundation_ruins":
        raw = raw_map.get("moonwell_ruins")
    elif raw is None and canonical_id == "core_formation_swamp":
        raw = raw_map.get("cursed_swamp")
    if raw is None:
        return _generic_realm_encounters(get_area(canonical_id))
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
                    is_boss=bool(entry.get("is_boss", False)),
                    route_tags=tuple(str(tag) for tag in entry.get("route_tags", [])),
                    segment_role=str(entry.get("segment_role", "standard")),
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
                reputation_delta=int(c.get("reputation_delta", 0)),
                manual_pool=c.get("manual_pool"),
                manual_chance=float(c.get("manual_chance", 1.0)),
                spirit_stones=int(c.get("spirit_stones", 0)),
                sect_invitation=c.get("sect_invitation"),
                route_tag=c.get("route_tag"),
                route_label=c.get("route_label"),
                route_tone=c.get("route_tone"),
                next_encounter_id=c.get("next_encounter_id"),
            )
            for c in entry.get("choices", [])
        )
        encounters.append(
            AdventureEncounter(
                id=entry["id"],
                prompt=entry["prompt"],
                encounter_type=encounter_type,
                choices=choices,
                route_tags=tuple(str(tag) for tag in entry.get("route_tags", [])),
                segment_role=str(entry.get("segment_role", "standard")),
            )
        )
    return encounters


def is_moral_choice_encounter(encounter: AdventureEncounter) -> bool:
    """Choice events where at least one path clearly helps and one clearly harms others."""
    if encounter.encounter_type != "choice" or not encounter.choices:
        return False
    deltas = [c.karma_delta for c in encounter.choices]
    return any(d > 0 for d in deltas) and any(d < 0 for d in deltas)


def _default_encounter(segment: int) -> AdventureEncounter:
    return AdventureEncounter(
        id=f"generic_{segment}",
        prompt="A wounded traveler waves from the roadside, clutching a torn satchel.",
        encounter_type="choice",
        choices=(
            AdventureChoice(
                "aid",
                "Carry them to the nearest village healer",
                0.05,
                0.9,
                0.08,
                karma_delta=10,
            ),
            AdventureChoice(
                "rob",
                "Take their satchel and leave them in the dust",
                -0.05,
                1.35,
                0.18,
                karma_delta=-16,
                spirit_stones=10,
            ),
            AdventureChoice(
                "pass",
                "Walk past without meeting their eyes",
                0.0,
                0.95,
                0.05,
                karma_delta=-4,
            ),
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


def _remember_encounter(state: dict, encounter_id: str) -> None:
    seen: list[str] = state.setdefault("encounter_ids_seen", [])
    if encounter_id not in seen:
        seen.append(encounter_id)


def _pick_encounter(
    rng: random.Random,
    area_id: str,
    segment: int,
    player: Player | None = None,
    *,
    state: dict | None = None,
) -> AdventureEncounter:
    if player is not None:
        from .novice_trial import is_first_adventure, pick_novice_encounter

        if is_first_adventure(player):
            novice = pick_novice_encounter(segment)
            if novice is not None:
                return novice
    pool = get_encounters_for_area(area_id)
    if not pool:
        return _default_encounter(segment)

    forced_id = (state or {}).pop("forced_next_encounter_id", None)
    if forced_id:
        return _encounter_by_id(area_id, str(forced_id), segment)

    if segment == 1:
        route_openers = [e for e in pool if e.encounter_type == "route_choice"]
        if route_openers:
            return rng.choice(route_openers)

    route_tag = str((state or {}).get("route_tag") or "")
    target_segments = int((state or {}).get("target_segments", SEGMENTS_PER_RUN))
    seen_ids = set((state or {}).get("encounter_ids_seen", []))
    if route_tag and segment > 1:
        route_pool = [
            e
            for e in pool
            if route_tag in e.route_tags and e.encounter_type != "route_choice"
        ]
        if route_pool:
            if segment >= target_segments:
                climax = [e for e in route_pool if e.segment_role == "climax" and e.id not in seen_ids]
                if climax:
                    return rng.choice(climax)
            non_climax = [
                e
                for e in route_pool
                if e.segment_role != "climax" and e.id not in seen_ids
            ]
            if non_climax:
                return rng.choice(non_climax)

    general_pool = [e for e in pool if e.encounter_type != "route_choice"]
    unseen_general = [e for e in general_pool if e.id not in seen_ids]
    if unseen_general:
        general_pool = unseen_general
    if not general_pool:
        return _default_encounter(segment)

    moral = [e for e in general_pool if is_moral_choice_encounter(e)]
    other_choices = [
        e for e in general_pool if e.encounter_type == "choice" and e not in moral
    ]
    combat = [e for e in general_pool if e.encounter_type == "combat"]

    need_karma = bool(state) and not state.get("karma_touched") and segment >= target_segments
    if need_karma and moral:
        return rng.choice(moral)
    if moral and rng.random() < 0.82:
        return rng.choice(moral)
    non_combat = moral + other_choices
    if non_combat and rng.random() < 0.78:
        return rng.choice(non_combat)
    if combat:
        return rng.choice(combat)
    if moral:
        return rng.choice(moral)
    return rng.choice(general_pool)


def _clamp_chance(value: float, *, min_chance: float = 0.12, max_chance: float = 0.95) -> float:
    return max(min_chance, min(max_chance, value))


def _new_adventure_state(
    player: Player,
    area: AreaDef,
    stance: str,
    *,
    target_segments: int = SEGMENTS_PER_RUN,
) -> dict:
    gap = realm_gap(player, area)
    messages = [f"You enter **{area.name}** and follow the signs of your current realm."]
    if gap > 0:
        messages.append(underleveled_entry_message(area, gap))
    return {
        "drops": {},
        "messages": messages,
        "rare_events": [],
        "segments_cleared": 0,
        "target_segments": target_segments,
        "route_tag": None,
        "route_label": None,
        "route_steps": [],
        "encounter_ids_seen": [],
        "segments_since_rare": 0,
        "failed_run": False,
        "realm_gap": gap,
    }


def _roll_drop(
    rng: random.Random,
    drops: tuple[DropEntry, ...],
    qty_mult: float,
    luck: float,
    drop_luck: float,
    *,
    player_realm_index: int,
    area_min_realm: int,
) -> tuple[str, int] | None:
    from .loot import LootDropEntry, roll_weighted_loot_pool

    if not drops:
        return None
    table = tuple(
        LootDropEntry(item_id=d.item_id, rarity=d.rarity, min_qty=d.min_qty, max_qty=d.max_qty)
        for d in drops
    )
    return roll_weighted_loot_pool(
        table,
        rng,
        luck=luck,
        drop_luck=drop_luck,
        player_realm_index=player_realm_index,
        area_min_realm=area_min_realm,
        qty_mult=qty_mult,
    )


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
    state: dict | None = None,
) -> bool:
    """Apply a rare event. Returns True if the adventure should pause for player input."""
    from .manuals import RARE_EVENT_META_KEYS, apply_rare_event_manual_reward

    rng = rng or random.Random()
    emoji, title = RARE_EVENT_FLAIR.get(event.id, ("✨", event.id.replace("_", " ").title()))
    messages.append(f"{emoji} **{title}** — {event.message}")
    rewards = RARE_EVENT_REWARDS.get(event.id, {})

    if rewards.get("interactive"):
        if state is not None:
            state["pending_shrine"] = True
            messages.append("_The shrine waits for your decision…_")
        return True

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
    return False


def _build_shrine_pending(active: ActiveAdventure, area: AreaDef, state: dict) -> PendingAdventure:
    return PendingAdventure(
        active_id=active.id,
        area_name=area.name,
        segment=active.segment,
        segments_total=int(state.get("target_segments", SEGMENTS_PER_RUN)),
        prompt="A cursed shrine hums with forbidden qi. Do you accept its bargain?",
        choices=cursed_shrine_choices(),
        messages=list(state.get("messages", [])),
        encounter_type="shrine",
        route_label=state.get("route_label"),
        route_tag=state.get("route_tag"),
    )


def _encounter_icon(encounter_type: str) -> str:
    if encounter_type == "shrine":
        return "⛩️"
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
        segments_total=int(state.get("target_segments", SEGMENTS_PER_RUN)),
        prompt=encounter.prompt,
        choices=encounter.choices,
        messages=list(state.get("messages", [])),
        encounter_type=encounter.encounter_type,
        combat_id=combat_id,
        route_label=state.get("route_label"),
        route_tag=state.get("route_tag"),
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
    area = get_area(area_id)
    area_realm = area.min_realm if area is not None else 0
    scaled = scale_monster_stats(
        monster.hp,
        monster.attack,
        monster.defense,
        realm_index=area_realm,
        combat_tier=monster.combat_tier,
    )

    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    opponent = opponent_from_monster(
        monster.monster_id,
        monster.name,
        scaled["hp"],
        scaled["attack"],
        scaled["defense"],
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
    state["combat_monster_id"] = monster.monster_id
    boss_note = " **Boss fight!**" if encounter.is_boss else ""
    state.setdefault("messages", []).append(
        f"{_encounter_icon('combat')} **Combat!**{boss_note} {encounter.prompt}"
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
        from .combat_stats import compute_combat_stats
        from .loot import roll_creature_loot

        state["segments_cleared"] = int(state.get("segments_cleared", 0)) + 1
        drop_mult = stance_mod["drop_mult"]
        stats = compute_combat_stats(player, session, mod)
        drops: dict[str, int] = state.setdefault("drops", {})
        monster_id = state.get("combat_monster_id")
        monster = get_monster(str(monster_id)) if monster_id else None
        if monster is not None and monster.drops:
            tier = monster.combat_tier if monster.combat_tier in {"normal", "elite", "boss"} else "normal"
            loot = roll_creature_loot(
                monster.drops,
                rng,
                combat_tier=tier,
                luck=stats.luck,
                drop_luck=mod.drop_luck,
                player_realm_index=player.realm_index,
                area_min_realm=area.min_realm,
                qty_mult=drop_mult,
            )
            for item_id, qty in loot.items():
                drops[item_id] = drops.get(item_id, 0) + qty
        else:
            rolled = _roll_drop(
                rng,
                area.drops,
                drop_mult,
                stats.luck,
                mod.drop_luck,
                player_realm_index=player.realm_index,
                area_min_realm=area.min_realm,
            )
            if rolled:
                item_id, qty = rolled
                drops[item_id] = drops.get(item_id, 0) + qty
        state["messages"].append("Victory in combat — you claim spoils from the foe.")
        state.setdefault("route_steps", []).append("Won a spirit-beast fight")
        state.pop("combat_monster_id", None)
        _apply_combat_stance_karma(player, stance, state)
    else:
        state["messages"].append(
            "You were driven back from combat — wounds ache, but your cultivation core holds steady."
        )
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
            paused = _apply_rare_event(
                session, player, event, area, state.setdefault("drops", {}), state["messages"], rng, state
            )
            state["segments_since_rare"] = 0
            if paused:
                return True, False
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
    return area, None


def start_adventure_session(
    session: Session,
    player: Player,
    area_id: str,
    stance: str,
    rng: random.Random | None = None,
) -> tuple[PendingAdventure | None, str | None]:
    rng = rng or random.Random()
    requested_area_id = area_id
    area_id = resolve_area_id(area_id) or area_id
    encounter_area_id = requested_area_id if requested_area_id in _load_encounters() else area_id
    if get_active_adventure(session, player.id) is not None:
        return None, "You already have an adventure in progress. Use `/adventure continue` or `/adventure abandon`."

    area, err = _validate_area(player, area_id, stance)
    if err:
        return None, err
    assert area is not None

    stance = stance.lower() if stance else DEFAULT_STANCE
    from .novice_trial import heal_stuck_novice_adventure, is_first_adventure

    if heal_stuck_novice_adventure(player):
        state_hint = (
            "_The sect reopens your first journey — seek the **Sage of the Bamboo Path** "
            "when you follow **`/adventure`**._"
        )
    else:
        state_hint = ""

    target_segments = rng.randint(3, 5)
    state = _new_adventure_state(player, area, stance, target_segments=target_segments)
    if state_hint:
        state["messages"].append(state_hint)
    encounter = _pick_encounter(rng, encounter_area_id, 1, player, state=state)
    _remember_encounter(state, encounter.id)
    if is_first_adventure(player):
        state["novice_adventure"] = True
        state["messages"].append(
            "_The sect elders whisper: your first journey teaches karma — "
            "no choice here can end the run._"
        )

    active = ActiveAdventure(
        player_id=player.id,
        area_id=encounter_area_id,
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
        combat_id, err = _start_combat_encounter(session, player, encounter_area_id, encounter, state)
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

    area_id = active.area_id if active.area_id in _load_encounters() else (resolve_area_id(active.area_id) or active.area_id)
    encounters = get_encounters_for_area(area_id)
    encounter = _encounter_by_id(area_id, active.encounter_id, active.segment)

    state = _load_state(active)
    combat_id = None
    combat_state = None
    if state.get("pending_combat"):
        active_combat = get_active_combat(session, player.id)
        if active_combat is not None:
            from .combat.session import load_combat_state as load_combat

            combat_id = active_combat.id
            combat_state = load_combat(active_combat)

    if state.get("pending_shrine"):
        return _build_shrine_pending(active, area, state), None

    return (
        _build_pending_from_encounter(
            active, area, encounter, state, combat_id=combat_id, combat_state=combat_state
        ),
        None,
    )


COMBAT_STANCE_KARMA: dict[str, int] = {
    "cautious": 3,
    "balanced": -2,
    "reckless": -7,
}


def _apply_combat_stance_karma(player: Player, stance: str, state: dict) -> None:
    delta = COMBAT_STANCE_KARMA.get(stance, -2)
    if stance == "cautious":
        state["messages"].append("You sheathe your blade once the foe falls — mercy where you can grant it.")
    elif stance == "reckless":
        state["messages"].append("You leave no survivor and take every scrap — the path runs red behind you.")
    else:
        state["messages"].append("You end the threat with cold efficiency — no mercy, no cruelty beyond need.")
    _apply_choice_karma(
        player,
        AdventureChoice("combat_resolve", "Combat aftermath", 0.0, 1.0, 0.0, karma_delta=delta),
        state,
    )


def _apply_choice_karma(
    player: Player,
    choice: AdventureChoice,
    state: dict,
) -> None:
    from .karma import clamp_karma

    if not choice.karma_delta:
        return
    state["karma_touched"] = True
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


def _apply_choice_reputation(
    player: Player,
    choice: AdventureChoice,
    state: dict,
) -> None:
    from .reputation import clamp_reputation, reputation_tier_label

    if not choice.reputation_delta:
        return
    before = player.reputation
    player.reputation = clamp_reputation(player.reputation + choice.reputation_delta)
    delta = player.reputation - before
    if delta > 0:
        state["messages"].append(
            f"🏛️ Your reputation rises (**+{delta}** → **{reputation_tier_label(player.reputation)}**)."
        )
    elif delta < 0:
        state["messages"].append(
            f"🏛️ Your reputation falls (**{delta}** → **{reputation_tier_label(player.reputation)}**)."
        )


def _apply_shrine_choice(
    session: Session,
    player: Player,
    choice: AdventureChoice,
    state: dict,
) -> None:
    from .effects import add_effect

    _apply_choice_karma(player, choice, state)
    _apply_choice_reputation(player, choice, state)
    if choice.id == "accept":
        add_effect(session, player.id, "shrine_boon", charges=2)
        add_effect(session, player.id, "shrine_curse", charges=2)
        state["messages"].append(
            "⛩️ Dark qi floods your meridians — **Shrine Boon** and **Shrine Curse** take hold."
        )
    else:
        state["messages"].append("⛩️ You turn away. The shrine's whisper fades into the mist.")
    state.pop("pending_shrine", None)


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

    from .game_sects import try_grant_sect_invitation_from_adventure

    invite_msg = try_grant_sect_invitation_from_adventure(
        session, player, choice.sect_invitation, source="adventure"
    )
    if invite_msg:
        state["messages"].append(invite_msg)


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
        state["messages"].append(
            f"Your choice — **{choice.label}** — backfires. You retreat battered; your stored qi remains untouched."
        )
        state.setdefault("route_steps", []).append(f"{choice.label} (setback)")
        return False, True

    _apply_choice_karma(player, choice, state)
    _apply_choice_reputation(player, choice, state)
    state.setdefault("route_steps", []).append(choice.label)

    gap = int(state.get("realm_gap", realm_gap(player, area)))
    penalty, min_chance = adventure_realm_modifiers(gap)
    success_chance = _clamp_chance(
        area.base_success
        + stance_mod["success"]
        + mod.adventure_success
        + choice.success_bonus
        + min(0.12, power / 200.0)
        - penalty,
        min_chance=min_chance,
    )
    success_chance = min(0.95, success_chance * defense)
    from .novice_trial import novice_adventure_success_floor

    floor = novice_adventure_success_floor(state)
    if floor > 0:
        success_chance = max(success_chance, floor)

    drop_mult = stance_mod["drop_mult"] * choice.drop_mult * underleveled_drop_bonus(gap)

    if rng.random() <= success_chance:
        from .combat_stats import compute_combat_stats

        state["segments_cleared"] = int(state.get("segments_cleared", 0)) + 1
        stats = compute_combat_stats(player, session, mod)
        rolled = _roll_drop(
            rng,
            area.drops,
            drop_mult,
            stats.luck,
            mod.drop_luck,
            player_realm_index=player.realm_index,
            area_min_realm=area.min_realm,
        )
        drops: dict[str, int] = state.setdefault("drops", {})
        if rolled:
            item_id, qty = rolled
            drops[item_id] = drops.get(item_id, 0) + qty
        state["messages"].append(f"**{choice.label}** pays off — you gather spoils.")
        _apply_choice_rewards(session, player, choice, state, rng)
        if choice.next_encounter_id:
            state["forced_next_encounter_id"] = choice.next_encounter_id
    else:
        state["messages"].append(
            f"**{choice.label}** falters. You are forced back — the setback costs you time, not qi."
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
            paused = _apply_rare_event(
                session, player, event, area, state.setdefault("drops", {}), state["messages"], rng, state
            )
            state["segments_since_rare"] = 0
            if paused:
                return True, False
    else:
        state["segments_since_rare"] = segments_since_rare + 1

    return bool(state["segments_cleared"] > 0), False


def _advance_adventure_after_segment(
    session: Session,
    player: Player,
    active: ActiveAdventure,
    area: AreaDef,
    state: dict,
    rng: random.Random,
    *,
    run_failed: bool,
) -> tuple[PendingAdventure | AdventureResult | None, str | None]:
    if run_failed:
        state["failed_run"] = True

    current_segment = active.segment
    target_segments = int(state.get("target_segments", SEGMENTS_PER_RUN))
    if run_failed or current_segment >= target_segments:
        return _finalize_adventure(session, player, area, active.stance, state, active), None

    if state.get("pending_shrine"):
        _save_state(active, state)
        session.add(active)
        return _build_shrine_pending(active, area, state), None

    next_segment = current_segment + 1
    area_id = active.area_id if active.area_id in _load_encounters() else (resolve_area_id(active.area_id) or active.area_id)
    next_encounter = _pick_encounter(rng, area_id, next_segment, player, state=state)
    _remember_encounter(state, next_encounter.id)
    active.segment = next_segment
    active.encounter_id = next_encounter.id
    _save_state(active, state)
    session.add(active)

    combat_id = None
    combat_state = None
    if next_encounter.encounter_type == "combat":
        combat_id, err = _start_combat_encounter(session, player, area_id, next_encounter, state)
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

    state = _load_state(active)

    if state.get("pending_shrine"):
        choice = next((c for c in cursed_shrine_choices() if c.id == choice_id), None)
        if choice is None:
            return None, "That choice is not available."
        _apply_shrine_choice(session, player, choice, state)
        _save_state(active, state)
        session.add(player)
        return _advance_adventure_after_segment(
            session, player, active, area, state, rng, run_failed=False
        )

    area_id = active.area_id if active.area_id in _load_encounters() else (resolve_area_id(active.area_id) or active.area_id)
    encounter = _encounter_by_id(area_id, active.encounter_id, active.segment)

    if encounter.encounter_type == "route_choice":
        choice = next((c for c in encounter.choices if c.id == choice_id), None)
        if choice is None:
            return None, "That choice is not available."
        state["route_tag"] = choice.route_tag or choice.id
        state["route_label"] = choice.route_label or choice.label
        if choice.route_tone:
            state["route_tone"] = choice.route_tone
        route_steps: list[str] = state.setdefault("route_steps", [])
        route_steps.append(choice.label)
        state["segments_cleared"] = int(state.get("segments_cleared", 0)) + 1
        state["messages"].append(f"Your route bends toward **{state['route_label']}**.")
        _apply_choice_karma(player, choice, state)
        _apply_choice_reputation(player, choice, state)
        _save_state(active, state)
        session.add(player)
        return _advance_adventure_after_segment(
            session, player, active, area, state, rng, run_failed=False
        )

    if encounter.encounter_type == "combat":
        return None, "This segment is a combat encounter — use the combat buttons."

    choice = next((c for c in encounter.choices if c.id == choice_id), None)
    if choice is None:
        return None, "That choice is not available."

    allow_catastrophic = not state.get("novice_adventure", False)
    _, run_failed = _resolve_segment(
        session, player, area, active.stance, choice, state, rng, allow_catastrophic=allow_catastrophic
    )

    _save_state(active, state)
    session.add(player)
    return _advance_adventure_after_segment(
        session, player, active, area, state, rng, run_failed=run_failed
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
        state["messages"].append("You fled the combat encounter — lives may still hang in the balance.")
        _apply_choice_karma(
            player,
            AdventureChoice("flee", "Flee combat", 0.0, 1.0, 0.0, karma_delta=-5),
            state,
        )
        session.add(player)
        return _finalize_adventure(session, player, area, active.stance, state, active), None

    _, run_failed = _resolve_combat_segment(
        session, player, area, active.stance, state, rng, victory=victory
    )
    _save_state(active, state)
    session.add(player)
    return _advance_adventure_after_segment(
        session, player, active, area, state, rng, run_failed=run_failed
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

    target_segments = int(state.get("target_segments", SEGMENTS_PER_RUN))
    segments_cleared = int(state.get("segments_cleared", 0))
    failed_run = bool(state.get("failed_run", False))
    if failed_run and segments_cleared == 0:
        outcome = "fail"
    elif segments_cleared >= target_segments:
        outcome = "success"
    elif segments_cleared > 0:
        outcome = "partial"
    else:
        outcome = "fail"

    rare_events = list(state.get("rare_events", []))
    rewards_json = json.dumps({"drops": drops, "rare_events": rare_events})
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
    adventure_success = outcome == "success"
    if adventure_success:
        from .game_sects import on_sect_activity

        merit_msgs = on_sect_activity(
            session, player, "adventure", adventure_success=True
        )
        messages.extend(merit_msgs)

    return AdventureResult(
        success=segments_cleared > 0 and not failed_run,
        outcome=outcome,
        area_name=area.name,
        stance=stance,
        segments_cleared=segments_cleared,
        drops=drops,
        rare_events=rare_events,
        messages=messages,
        qi_delta=0,
        failed_run=failed_run,
        target_segments=target_segments,
        route_label=state.get("route_label"),
        route_steps=list(state.get("route_steps", [])),
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
        return AdventureResult(
            success=False,
            outcome="invalid",
            area_name="",
            stance=stance,
            segments_cleared=0,
            messages=[err],
        )

    assert area is not None
    stance = stance.lower()
    target_segments = rng.randint(3, 5)
    state = _new_adventure_state(player, area, stance, target_segments=target_segments)

    for segment in range(1, target_segments + 1):
        encounter = _pick_encounter(rng, area_id, segment, player, state=state)
        _remember_encounter(state, encounter.id)
        if not encounter.choices:
            encounter = _default_encounter(segment)
        choice = min(encounter.choices, key=lambda c: c.fail_chance)
        if encounter.encounter_type == "route_choice":
            state["route_tag"] = choice.route_tag or choice.id
            state["route_label"] = choice.route_label or choice.label
            if choice.route_tone:
                state["route_tone"] = choice.route_tone
            state.setdefault("route_steps", []).append(choice.label)
            state["segments_cleared"] = int(state.get("segments_cleared", 0)) + 1
            state["messages"].append(f"Your route bends toward **{state['route_label']}**.")
            continue
        _, run_failed = _resolve_segment(
            session, player, area, stance, choice, state, rng, allow_catastrophic=False
        )
        if run_failed:
            state["failed_run"] = True
            break

    return _finalize_adventure(session, player, area, stance, state, active=None)
