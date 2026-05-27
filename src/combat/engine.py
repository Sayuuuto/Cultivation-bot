from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..auto_combat import BeastTemplate, resolve_auto_combat
from ..combat_stats import PlayerCombatStats
from ..modifiers import CharacterModifiers
from .catalog import TechniqueDef
from .effects import (
    CombatantState,
    has_status,
    is_stunned,
    status_instances_from_json,
    status_instances_to_json,
    tick_statuses,
)
from .rules import load_combat_rules
from .triggers import (
    check_fatal_survival,
    opponent_trait_turn,
    process_passive_hp_threshold,
    process_passive_on_cc,
    process_passive_turn_end,
    resolve_technique,
    tick_combat_extras,
)


@dataclass
class OpponentTemplate:
    opponent_id: str
    name: str
    hp: int
    attack: int
    defense: int
    speed: int = 10
    traits: list[str] = field(default_factory=list)


@dataclass
class CombatState:
    turn: int
    player: CombatantState
    opponent: CombatantState
    opponent_id: str
    opponent_name: str
    opponent_attack: int
    opponent_defense: int
    opponent_speed: int
    technique_cooldowns: dict[str, int] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    finished: bool = False
    victory: bool = False
    fled: bool = False
    context: str = "hunt"
    context_meta: dict = field(default_factory=dict)
    consecutive_hits: int = 0
    consecutive_bonus_per_hit: float = 0.05
    passive_cooldowns: dict[str, int] = field(default_factory=dict)
    triggered_once: set[str] = field(default_factory=set)
    player_shield: int = 0
    shield_turns: int = 0
    opponent_shield: int = 0
    damage_boost_pct: float = 0.0
    damage_boost_turns: int = 0
    opponent_traits: list[str] = field(default_factory=list)
    opponent_trait_cd: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "player_hp": self.player.hp,
            "player_max_hp": self.player.max_hp,
            "player_statuses": status_instances_to_json(self.player.statuses),
            "player_dodge_next": self.player.dodge_next,
            "opponent_hp": self.opponent.hp,
            "opponent_max_hp": self.opponent.max_hp,
            "opponent_statuses": status_instances_to_json(self.opponent.statuses),
            "opponent_id": self.opponent_id,
            "opponent_name": self.opponent_name,
            "opponent_attack": self.opponent_attack,
            "opponent_defense": self.opponent_defense,
            "opponent_speed": self.opponent_speed,
            "technique_cooldowns": self.technique_cooldowns,
            "log": self.log[-30:],
            "finished": self.finished,
            "victory": self.victory,
            "fled": self.fled,
            "context": self.context,
            "context_meta": self.context_meta,
            "consecutive_hits": self.consecutive_hits,
            "consecutive_bonus_per_hit": self.consecutive_bonus_per_hit,
            "passive_cooldowns": self.passive_cooldowns,
            "triggered_once": list(self.triggered_once),
            "player_shield": self.player_shield,
            "shield_turns": self.shield_turns,
            "opponent_shield": self.opponent_shield,
            "damage_boost_pct": self.damage_boost_pct,
            "damage_boost_turns": self.damage_boost_turns,
            "opponent_traits": self.opponent_traits,
            "opponent_trait_cd": self.opponent_trait_cd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CombatState:
        player = CombatantState(
            hp=int(data["player_hp"]),
            max_hp=int(data["player_max_hp"]),
            statuses=status_instances_from_json(data.get("player_statuses", [])),
            dodge_next=bool(data.get("player_dodge_next", False)),
        )
        player.sealed = has_status(player, "seal")
        player.feared = has_status(player, "fear")
        opponent = CombatantState(
            hp=int(data["opponent_hp"]),
            max_hp=int(data["opponent_max_hp"]),
            statuses=status_instances_from_json(data.get("opponent_statuses", [])),
        )
        return cls(
            turn=int(data.get("turn", 1)),
            player=player,
            opponent=opponent,
            opponent_id=str(data["opponent_id"]),
            opponent_name=str(data["opponent_name"]),
            opponent_attack=int(data["opponent_attack"]),
            opponent_defense=int(data["opponent_defense"]),
            opponent_speed=int(data.get("opponent_speed", 10)),
            technique_cooldowns={str(k): int(v) for k, v in data.get("technique_cooldowns", {}).items()},
            log=list(data.get("log", [])),
            finished=bool(data.get("finished", False)),
            victory=bool(data.get("victory", False)),
            fled=bool(data.get("fled", False)),
            context=str(data.get("context", "hunt")),
            context_meta=dict(data.get("context_meta", {})),
            consecutive_hits=int(data.get("consecutive_hits", 0)),
            consecutive_bonus_per_hit=float(data.get("consecutive_bonus_per_hit", 0.05)),
            passive_cooldowns={str(k): int(v) for k, v in data.get("passive_cooldowns", {}).items()},
            triggered_once=set(data.get("triggered_once", [])),
            player_shield=int(data.get("player_shield", 0)),
            shield_turns=int(data.get("shield_turns", 0)),
            opponent_shield=int(data.get("opponent_shield", 0)),
            damage_boost_pct=float(data.get("damage_boost_pct", 0.0)),
            damage_boost_turns=int(data.get("damage_boost_turns", 0)),
            opponent_traits=list(data.get("opponent_traits", [])),
            opponent_trait_cd={str(k): int(v) for k, v in data.get("opponent_trait_cd", {}).items()},
        )


@dataclass
class TurnResult:
    state: CombatState
    messages: list[str]
    error: str | None = None


def opponent_from_beast(beast: BeastTemplate) -> OpponentTemplate:
    rules = load_combat_rules()
    speed = max(5, int(beast.attack * rules.beast_speed_from_attack_ratio))
    traits = list(getattr(beast, "traits", []) or [])
    return OpponentTemplate(
        opponent_id=beast.beast_id,
        name=beast.name,
        hp=beast.hp,
        attack=beast.attack,
        defense=beast.defense,
        speed=speed,
        traits=traits,
    )


def opponent_from_monster(
    monster_id: str,
    name: str,
    hp: int,
    attack: int,
    defense: int,
    speed: int,
    *,
    traits: list[str] | None = None,
) -> OpponentTemplate:
    return OpponentTemplate(
        opponent_id=monster_id,
        name=name,
        hp=hp,
        attack=attack,
        defense=defense,
        speed=speed,
        traits=list(traits or []),
    )


def create_combat_state(
    stats: PlayerCombatStats,
    opponent: OpponentTemplate,
    *,
    context: str = "hunt",
    context_meta: dict | None = None,
) -> CombatState:
    return CombatState(
        turn=1,
        player=CombatantState(hp=stats.hp, max_hp=stats.max_hp),
        opponent=CombatantState(hp=opponent.hp, max_hp=opponent.hp),
        opponent_id=opponent.opponent_id,
        opponent_name=opponent.name,
        opponent_attack=opponent.attack,
        opponent_defense=opponent.defense,
        opponent_speed=opponent.speed,
        opponent_traits=list(opponent.traits),
        context=context,
        context_meta=context_meta or {},
        log=[f"You face **{opponent.name}** (HP {opponent.hp}). Choose your action."],
    )


def _decay_cooldowns(cooldowns: dict[str, int]) -> dict[str, int]:
    return {k: max(0, v - 1) for k, v in cooldowns.items() if v - 1 > 0}


def _check_end(state: CombatState) -> None:
    rules = load_combat_rules()
    if state.player.hp <= 0:
        state.finished = True
        state.victory = False
        state.log.append("You fall — the fight is lost.")
        return
    if state.opponent.hp <= 0:
        state.finished = True
        state.victory = True
        state.log.append(f"**{state.opponent_name}** is defeated!")
        return
    if state.turn > rules.max_turns:
        beast_start = state.opponent.max_hp
        beast_ratio = 1.0 - (state.opponent.hp / beast_start)
        if beast_ratio >= (1.0 - rules.partial_win_beast_hp_fraction):
            state.finished = True
            state.victory = True
            state.log.append(f"**{state.opponent_name}** flees, badly wounded. You claim victory.")
        else:
            state.finished = True
            state.victory = False
            state.log.append(f"**{state.opponent_name}** escapes as the fight drags on.")


def _opponent_damage(
    attack: int,
    stats: PlayerCombatStats,
    mod: CharacterModifiers | None,
    rng: random.Random,
) -> int:
    variance = rng.uniform(0.90, 1.10)
    raw = attack * variance
    defense = stats.defense * (1.0 + (mod.adventure_defense if mod else 0.0))
    return max(1, int(raw - defense * 0.35))


def _opponent_turn(
    state: CombatState,
    stats: PlayerCombatStats,
    mod: CharacterModifiers | None,
    passive: TechniqueDef | None,
    rng: random.Random,
) -> None:
    if is_stunned(state.opponent):
        state.log.append(f"**{state.opponent_name}** is stunned and cannot act.")
        return
    if state.player.dodge_next or rng.random() < stats.dodge:
        state.player.dodge_next = False
        state.log.append(f"**{state.opponent_name}** attacks — you dodge!")
        return
    taken = _opponent_damage(state.opponent_attack, stats, mod, rng)
    from .triggers import _deal_damage_to_player

    _deal_damage_to_player(state, taken)
    if not check_fatal_survival(state, passive):
        state.log.append(
            f"**{state.opponent_name}** hits you for **{taken}** damage. (**{max(0, state.player.hp)}** HP left)"
        )
    process_passive_hp_threshold(state, passive)
    opponent_trait_turn(state, rng)
    if state.player.sealed:
        process_passive_on_cc(state, passive, "seal")
    if is_stunned(state.player):
        process_passive_on_cc(state, passive, "stun")


def execute_turn(
    state: CombatState,
    stats: PlayerCombatStats,
    mod: CharacterModifiers | None,
    passive: TechniqueDef | None,
    action: str,
    *,
    technique_id: str | None = None,
    rng: random.Random | None = None,
) -> TurnResult:
    rng = rng or random.Random()
    if state.finished:
        return TurnResult(state=state, messages=["Combat already ended."], error="Combat already ended.")

    if is_stunned(state.player):
        state.log.append("You are **stunned** and cannot act!")
    elif action == "strike":
        err = resolve_technique(state, stats, passive, "basic_strike", rng)
        if err:
            return TurnResult(state=state, messages=[err], error=err)
    elif action == "technique" and technique_id:
        err = resolve_technique(state, stats, passive, technique_id, rng)
        if err:
            return TurnResult(state=state, messages=[err], error=err)
    else:
        return TurnResult(state=state, messages=["Invalid action."], error="Invalid action.")

    _check_end(state)
    if not state.finished and state.opponent.hp > 0 and state.player.hp > 0:
        _opponent_turn(state, stats, mod, passive, rng)
        _check_end(state)

    if not state.finished:
        for line in tick_statuses(state.player):
            state.log.append(f"(You) {line}")
        for line in tick_statuses(state.opponent):
            state.log.append(f"({state.opponent_name}) {line}")
        process_passive_turn_end(state, passive)
        process_passive_hp_threshold(state, passive)
        tick_combat_extras(state)
        _check_end(state)

    if not state.finished:
        state.technique_cooldowns = _decay_cooldowns(state.technique_cooldowns)
        state.turn += 1

    recent = state.log[-6:]
    return TurnResult(state=state, messages=recent)


def attempt_flee(state: CombatState, stats: PlayerCombatStats, rng: random.Random | None = None) -> TurnResult:
    rng = rng or random.Random()
    rules = load_combat_rules()
    flee_chance = rules.flee_base_chance + stats.agility * 0.002
    flee_chance = min(0.85, flee_chance)
    if rng.random() < flee_chance:
        state.finished = True
        state.fled = True
        state.victory = False
        state.log.append("You slip away into the wilds.")
        return TurnResult(state=state, messages=state.log[-4:])
    state.log.append("You fail to escape — the foe presses the attack!")
    _opponent_turn(state, stats, None, None, rng)
    _check_end(state)
    if not state.finished:
        state.turn += 1
    return TurnResult(state=state, messages=state.log[-6:])


def auto_finish_combat(
    state: CombatState,
    stats: PlayerCombatStats,
    mod: CharacterModifiers | None,
    rng: random.Random | None = None,
) -> TurnResult:
    rng = rng or random.Random()
    beast = BeastTemplate(
        beast_id=state.opponent_id,
        name=state.opponent_name,
        hp=state.opponent.hp,
        attack=state.opponent_attack,
        defense=state.opponent_defense,
    )
    remaining_stats = PlayerCombatStats(
        hp=state.player.hp,
        max_hp=state.player.max_hp,
        internal_strength=stats.internal_strength,
        external_strength=stats.external_strength,
        agility=stats.agility,
        spiritual_sense=stats.spiritual_sense,
        defense=stats.defense,
        comprehension=stats.comprehension,
        luck=stats.luck,
        crit_chance=stats.crit_chance,
        dodge=stats.dodge,
    )
    result = resolve_auto_combat(remaining_stats, beast, mod, rng)
    state.player.hp = result.player_hp_remaining
    state.opponent.hp = result.beast_hp_remaining
    state.finished = True
    state.victory = result.victory
    state.log.extend(result.log_lines)
    state.log.append("_Auto-finished remaining turns._")
    return TurnResult(state=state, messages=state.log[-10:])
