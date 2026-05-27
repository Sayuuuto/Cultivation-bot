from __future__ import annotations

import random
from dataclasses import dataclass

from src.auto_combat import BeastTemplate
from src.combat.catalog import TechniqueDef, get_technique, load_technique_catalog
from src.combat.effects import (
    CombatantState,
    apply_status,
    get_status_instance,
    has_status,
    is_stunned,
    status_stacks,
    status_turns_remaining,
    tick_statuses,
)
from src.combat.engine import (
    CombatState,
    create_combat_state,
    execute_pvp_turn,
    execute_turn,
    opponent_from_beast,
    opponent_from_monster,
)
from src.combat.rules import load_combat_rules
from src.combat.triggers import _compute_base_damage, opponent_trait_turn, resolve_technique
from src.combat_stats import PlayerCombatStats
from src.combat.targeting import technique_hits_all_enemies


class AlwaysProcRng(random.Random):
    """random() -> 0.0 so status chances and non-crit paths trigger."""

    def random(self) -> float:
        return 0.0


class NeverCritRng(random.Random):
    """Avoid crits; still allows damage variance in opponent attacks."""

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed)
        self._seq = 0.99

    def random(self) -> float:
        self._seq = 0.4 if self._seq > 0.5 else 0.99
        return self._seq


def standard_stats(**overrides) -> PlayerCombatStats:
    base = dict(
        hp=200,
        max_hp=200,
        internal_strength=40,
        external_strength=40,
        agility=25,
        spiritual_sense=20,
        defense=12,
        comprehension=10,
        luck=10,
        crit_chance=0.0,
        dodge=0.0,
    )
    base.update(overrides)
    return PlayerCombatStats(**base)


def tanky_beast(**overrides) -> BeastTemplate:
    if "max_hp" in overrides:
        overrides["hp"] = overrides.pop("max_hp")
    base = dict(beast_id="test_beast", name="Test Beast", hp=500, attack=8, defense=6)
    base.update(overrides)
    return BeastTemplate(**base)


def fresh_hunt_state(
    stats: PlayerCombatStats | None = None,
    beast: BeastTemplate | None = None,
    *,
    traits: list[str] | None = None,
) -> CombatState:
    stats = stats or standard_stats()
    beast = beast or tanky_beast()
    opp = opponent_from_beast(beast)
    if traits:
        opp = opponent_from_monster(
            beast.beast_id,
            beast.name,
            beast.hp,
            beast.attack,
            beast.defense,
            opp.speed,
            traits=traits,
        )
    return create_combat_state(stats, opp, context="hunt")


def active_technique_ids() -> list[str]:
    return sorted(
        tid
        for tid, tech in load_technique_catalog().items()
        if tech.slot_type == "active"
    )


def force_apply(target: CombatantState, status_id: str, times: int = 1) -> None:
    for _ in range(times):
        apply_status(target, status_id)


@dataclass
class DamageProbe:
    expected: int
    actual: int
    technique_id: str
    seed: int


def probe_technique_damage(
    technique_id: str,
    *,
    seed: int = 0,
    defense: int = 6,
    stats: PlayerCombatStats | None = None,
    setup: callable | None = None,
) -> DamageProbe:
    stats = stats or standard_stats()
    tech = get_technique(technique_id)
    assert tech is not None
    state = fresh_hunt_state(stats, tanky_beast(defense=defense))
    if setup:
        setup(state)
    rng = NeverCritRng(seed)
    opp_hp_before = state.opponent.hp
    err = resolve_technique(state, stats, None, technique_id, rng)
    assert err is None, err
    actual = opp_hp_before - state.opponent.hp
    expected = _compute_base_damage(tech, stats, defense, None, crit=False)
    if tech.technique_id == "rending_flurry":
        expected = max(1, int(expected * 0.55)) * 2
    return DamageProbe(expected=expected, actual=actual, technique_id=technique_id, seed=seed)


def run_stunned_player_turns(state: CombatState, stats: PlayerCombatStats, turns: int) -> list[bool]:
    """Returns whether each player phase was skipped due to stun."""
    skipped: list[bool] = []
    for _ in range(turns):
        before_log = len(state.log)
        execute_turn(
            state,
            stats,
            None,
            None,
            "technique",
            technique_id="basic_strike",
            rng=NeverCritRng(turns),
        )
        new_lines = state.log[before_log:]
        skipped.append(any("stunned" in line.lower() and "cannot act" in line.lower() for line in new_lines))
        if state.finished:
            break
    return skipped


def invalidate_combat_caches() -> None:
    load_combat_rules.cache_clear()
    load_technique_catalog.cache_clear()
