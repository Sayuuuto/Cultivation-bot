from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .combat.effects import CombatantState, StatusInstance, has_status
from .combat.engine import (
    CombatState,
    TurnResult,
    attempt_pvp_yield,
    auto_finish_pvp_combat,
    execute_pvp_turn,
)
from .combat.loadout import ensure_starter_techniques, get_equipped_passive
from .combat.rules import load_combat_rules
from .combat_stats import compute_combat_stats
from .models import Player

PVP_MAX_TURNS = load_combat_rules().max_turns


@dataclass
class DuelFighterState:
    player_id: int
    discord_id: str
    dao_name: str
    defense: int
    agility: int
    combatant: CombatantState
    technique_cooldowns: dict[str, int] = field(default_factory=dict)
    passive_cooldowns: dict[str, int] = field(default_factory=dict)
    triggered_once: set[str] = field(default_factory=set)
    shield_hp: int = 0
    shield_turns: int = 0
    consecutive_hits: int = 0
    consecutive_bonus_per_hit: float = 0.05
    damage_boost_pct: float = 0.0
    damage_boost_turns: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    actions_taken: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "discord_id": self.discord_id,
            "dao_name": self.dao_name,
            "defense": self.defense,
            "agility": self.agility,
            "combatant": _combatant_to_dict(self.combatant),
            "technique_cooldowns": self.technique_cooldowns,
            "passive_cooldowns": self.passive_cooldowns,
            "triggered_once": sorted(self.triggered_once),
            "shield_hp": self.shield_hp,
            "shield_turns": self.shield_turns,
            "consecutive_hits": self.consecutive_hits,
            "consecutive_bonus_per_hit": self.consecutive_bonus_per_hit,
            "damage_boost_pct": self.damage_boost_pct,
            "damage_boost_turns": self.damage_boost_turns,
            "damage_dealt": self.damage_dealt,
            "damage_taken": self.damage_taken,
            "actions_taken": self.actions_taken,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DuelFighterState:
        return cls(
            player_id=int(data["player_id"]),
            discord_id=str(data["discord_id"]),
            dao_name=str(data["dao_name"]),
            defense=int(data["defense"]),
            agility=int(data["agility"]),
            combatant=_combatant_from_dict(data["combatant"]),
            technique_cooldowns={str(k): int(v) for k, v in data.get("technique_cooldowns", {}).items()},
            passive_cooldowns={str(k): int(v) for k, v in data.get("passive_cooldowns", {}).items()},
            triggered_once=set(data.get("triggered_once", [])),
            shield_hp=int(data.get("shield_hp", 0)),
            shield_turns=int(data.get("shield_turns", 0)),
            consecutive_hits=int(data.get("consecutive_hits", 0)),
            consecutive_bonus_per_hit=float(data.get("consecutive_bonus_per_hit", 0.05)),
            damage_boost_pct=float(data.get("damage_boost_pct", 0.0)),
            damage_boost_turns=int(data.get("damage_boost_turns", 0)),
            damage_dealt=int(data.get("damage_dealt", 0)),
            damage_taken=int(data.get("damage_taken", 0)),
            actions_taken=int(data.get("actions_taken", 0)),
        )


@dataclass
class PvpCombatState:
    match_id: int
    turn: int
    fighters: dict[str, DuelFighterState]
    turn_order: list[str]
    current_actor_id: str
    log: list[str]
    finished: bool = False
    winner_discord_id: str | None = None
    surrendered: bool = False
    initiative_rolls: dict[str, int] = field(default_factory=dict)

    def actor(self) -> DuelFighterState:
        return self.fighters[self.current_actor_id]

    def opponent_of(self, discord_id: str) -> DuelFighterState:
        other_id = self.turn_order[1] if self.turn_order[0] == discord_id else self.turn_order[0]
        return self.fighters[other_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "turn": self.turn,
            "fighters": {key: fighter.to_dict() for key, fighter in self.fighters.items()},
            "turn_order": list(self.turn_order),
            "current_actor_id": self.current_actor_id,
            "log": self.log[-40:],
            "finished": self.finished,
            "winner_discord_id": self.winner_discord_id,
            "surrendered": self.surrendered,
            "initiative_rolls": dict(self.initiative_rolls),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PvpCombatState:
        fighters = {key: DuelFighterState.from_dict(raw) for key, raw in data["fighters"].items()}
        return cls(
            match_id=int(data["match_id"]),
            turn=int(data["turn"]),
            fighters=fighters,
            turn_order=list(data["turn_order"]),
            current_actor_id=str(data["current_actor_id"]),
            log=list(data.get("log", [])),
            finished=bool(data.get("finished", False)),
            winner_discord_id=data.get("winner_discord_id"),
            surrendered=bool(data.get("surrendered", False)),
            initiative_rolls={str(k): int(v) for k, v in data.get("initiative_rolls", {}).items()},
        )


@dataclass(frozen=True)
class PvpActionResult:
    ok: bool
    message: str
    state: PvpCombatState


def _combatant_to_dict(combatant: CombatantState) -> dict[str, Any]:
    return {
        "hp": combatant.hp,
        "max_hp": combatant.max_hp,
        "statuses": [
            {"status_id": s.status_id, "stacks": s.stacks, "turns_remaining": s.turns_remaining}
            for s in combatant.statuses
        ],
        "sealed": combatant.sealed,
        "feared": combatant.feared,
        "dodge_next": combatant.dodge_next,
    }


def _combatant_from_dict(data: dict[str, Any]) -> CombatantState:
    combatant = CombatantState(
        hp=int(data["hp"]),
        max_hp=int(data["max_hp"]),
        statuses=[
            StatusInstance(
                status_id=str(s["status_id"]),
                stacks=int(s.get("stacks", 1)),
                turns_remaining=int(s.get("turns_remaining", 1)),
            )
            for s in data.get("statuses", [])
        ],
        sealed=bool(data.get("sealed", False)),
        feared=bool(data.get("feared", False)),
        dodge_next=bool(data.get("dodge_next", False)),
    )
    combatant.sealed = combatant.sealed or has_status(combatant, "seal")
    combatant.feared = combatant.feared or has_status(combatant, "fear")
    return combatant


def _clone_combatant(combatant: CombatantState) -> CombatantState:
    return _combatant_from_dict(_combatant_to_dict(combatant))


def _fighter_from_player(session: Session, player: Player) -> DuelFighterState:
    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    ensure_starter_techniques(session, player.id)
    return DuelFighterState(
        player_id=player.id,
        discord_id=player.discord_id,
        dao_name=player.dao_name,
        defense=max(1, stats.defense),
        agility=max(1, stats.agility),
        combatant=CombatantState(hp=stats.max_hp, max_hp=stats.max_hp),
    )


def roll_initiative(
    fighter_a: DuelFighterState,
    fighter_b: DuelFighterState,
    rng: random.Random,
) -> tuple[str, str, dict[str, int]]:
    roll_a = fighter_a.agility + rng.randint(1, 10)
    roll_b = fighter_b.agility + rng.randint(1, 10)
    rolls = {fighter_a.discord_id: roll_a, fighter_b.discord_id: roll_b}
    if roll_a == roll_b:
        first_id = rng.choice([fighter_a.discord_id, fighter_b.discord_id])
    elif roll_a > roll_b:
        first_id = fighter_a.discord_id
    else:
        first_id = fighter_b.discord_id
    second_id = fighter_b.discord_id if first_id == fighter_a.discord_id else fighter_a.discord_id
    return first_id, second_id, rolls


def create_pvp_combat_state(
    session: Session,
    match_id: int,
    challenger: Player,
    opponent: Player,
    rng: random.Random,
) -> PvpCombatState:
    challenger_state = _fighter_from_player(session, challenger)
    opponent_state = _fighter_from_player(session, opponent)
    first_id, second_id, rolls = roll_initiative(challenger_state, opponent_state, rng)
    fighters = {
        challenger_state.discord_id: challenger_state,
        opponent_state.discord_id: opponent_state,
    }
    first = fighters[first_id]
    second = fighters[second_id]
    log = [
        (
            f"**{first.dao_name}** strikes first (initiative **{rolls[first_id]}** vs "
            f"**{rolls[second_id]}**)."
        ),
        f"**{first.dao_name}** — choose a technique to open the duel.",
    ]
    return PvpCombatState(
        match_id=match_id,
        turn=1,
        fighters=fighters,
        turn_order=[first_id, second_id],
        current_actor_id=first_id,
        log=log,
        initiative_rolls=rolls,
    )


def serialize_pvp_state(state: PvpCombatState) -> str:
    return json.dumps(state.to_dict())


def deserialize_pvp_state(raw: str) -> PvpCombatState:
    return PvpCombatState.from_dict(json.loads(raw))


def _build_combat_slice(
    duel: PvpCombatState,
    actor_id: str,
) -> CombatState:
    actor = duel.fighters[actor_id]
    defender = duel.opponent_of(actor_id)
    return CombatState(
        turn=duel.turn,
        player=_clone_combatant(actor.combatant),
        opponent=_clone_combatant(defender.combatant),
        opponent_id=defender.discord_id,
        opponent_name=defender.dao_name,
        opponent_attack=max(1, defender.defense),
        opponent_defense=defender.defense,
        opponent_speed=defender.agility,
        technique_cooldowns=dict(actor.technique_cooldowns),
        log=duel.log,
        finished=duel.finished,
        victory=False,
        fled=False,
        context="duel",
        context_meta={"match_id": duel.match_id},
        consecutive_hits=actor.consecutive_hits,
        consecutive_bonus_per_hit=actor.consecutive_bonus_per_hit,
        passive_cooldowns=dict(actor.passive_cooldowns),
        triggered_once=set(actor.triggered_once),
        player_shield=actor.shield_hp,
        shield_turns=actor.shield_turns,
        opponent_shield=defender.shield_hp,
        damage_boost_pct=actor.damage_boost_pct,
        damage_boost_turns=actor.damage_boost_turns,
        opponent_traits=[],
        player_label=actor.dao_name,
    )


def _sync_fighter_from_combat(actor: DuelFighterState, defender: DuelFighterState, cs: CombatState) -> None:
    actor.combatant = _clone_combatant(cs.player)
    defender.combatant = _clone_combatant(cs.opponent)
    actor.technique_cooldowns = dict(cs.technique_cooldowns)
    actor.passive_cooldowns = dict(cs.passive_cooldowns)
    actor.triggered_once = set(cs.triggered_once)
    actor.shield_hp = cs.player_shield
    actor.shield_turns = cs.shield_turns
    defender.shield_hp = cs.opponent_shield
    actor.consecutive_hits = cs.consecutive_hits
    actor.consecutive_bonus_per_hit = cs.consecutive_bonus_per_hit
    actor.damage_boost_pct = cs.damage_boost_pct
    actor.damage_boost_turns = cs.damage_boost_turns


def _track_damage(actor: DuelFighterState, defender: DuelFighterState, before_def_hp: int, before_act_hp: int) -> None:
    dealt = max(0, before_def_hp - defender.combatant.hp)
    taken = max(0, before_act_hp - actor.combatant.hp)
    actor.damage_dealt += dealt
    actor.damage_taken += taken
    defender.damage_taken += dealt


def _resolve_winner(duel: PvpCombatState, actor_id: str, cs: CombatState) -> None:
    if cs.fled:
        duel.finished = True
        duel.surrendered = True
        duel.winner_discord_id = duel.opponent_of(actor_id).discord_id
        return
    if not cs.finished:
        return
    duel.finished = True
    if cs.victory:
        duel.winner_discord_id = actor_id
    else:
        duel.winner_discord_id = duel.opponent_of(actor_id).discord_id


def _resolve_turn_cap(duel: PvpCombatState) -> None:
    if duel.finished or duel.turn <= PVP_MAX_TURNS:
        return
    ranked = sorted(
        duel.fighters.values(),
        key=lambda fighter: (fighter.combatant.hp / fighter.combatant.max_hp, fighter.damage_dealt),
        reverse=True,
    )
    duel.finished = True
    duel.winner_discord_id = ranked[0].discord_id
    duel.log.append(
        f"The arena judge calls time — **{ranked[0].dao_name}** wins on remaining vitality."
    )


def _advance_turn(duel: PvpCombatState) -> None:
    actor_id = duel.current_actor_id
    other_id = duel.turn_order[1] if duel.turn_order[0] == actor_id else duel.turn_order[0]
    duel.current_actor_id = other_id
    duel.log.append(f"**{duel.fighters[other_id].dao_name}** — choose a technique.")


def _run_combat_action(
    session: Session,
    duel: PvpCombatState,
    actor_id: str,
    *,
    action: str,
    technique_id: str | None,
    rng: random.Random,
) -> TurnResult:
    actor = duel.fighters[actor_id]
    defender = duel.opponent_of(actor_id)
    player = session.get(Player, actor.player_id)
    if player is None:
        raise ValueError("Duelist missing from database.")

    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    passive = get_equipped_passive(session, player.id)
    cs = _build_combat_slice(duel, actor_id)
    before_def_hp = defender.combatant.hp
    before_act_hp = actor.combatant.hp

    if action == "flee":
        result = attempt_pvp_yield(cs)
    elif action == "finish":
        result = auto_finish_pvp_combat(cs, stats, mod, rng)
    elif action in {"technique", "strike"}:
        result = execute_pvp_turn(
            cs,
            stats,
            mod,
            passive,
            action,
            technique_id=technique_id,
            rng=rng,
        )
        if result.error:
            return result
    else:
        return TurnResult(state=cs, messages=["Invalid action."], error="Invalid action.")

    duel.turn = cs.turn
    _sync_fighter_from_combat(actor, defender, cs)
    _track_damage(actor, defender, before_def_hp, before_act_hp)
    actor.actions_taken += 1
    _resolve_winner(duel, actor_id, cs)
    return result


def process_pvp_action(
    session: Session,
    state: PvpCombatState,
    actor_discord_id: str,
    action: str,
    rng: random.Random,
    *,
    technique_id: str | None = None,
) -> PvpActionResult:
    if state.finished:
        return PvpActionResult(False, "This duel is already over.", state)
    if actor_discord_id != state.current_actor_id:
        return PvpActionResult(False, "It is not your turn.", state)

    result = _run_combat_action(
        session,
        state,
        actor_discord_id,
        action=action,
        technique_id=technique_id,
        rng=rng,
    )
    if result.error:
        return PvpActionResult(False, result.error, state)

    if not state.finished:
        _resolve_turn_cap(state)
    if not state.finished:
        _advance_turn(state)
        _resolve_turn_cap(state)

    if state.finished and state.winner_discord_id:
        winner = state.fighters[state.winner_discord_id]
        state.log.append(f"**{winner.dao_name}** stands victorious.")

    return PvpActionResult(True, "Action resolved.", state)


def combat_slice_for_actor(duel: PvpCombatState, actor_id: str | None = None) -> CombatState:
    return _build_combat_slice(duel, actor_id or duel.current_actor_id)


def build_match_summary(state: PvpCombatState) -> str:
    lines = []
    for fighter in state.fighters.values():
        hp_pct = int(round(100 * fighter.combatant.hp / fighter.combatant.max_hp)) if fighter.combatant.max_hp else 0
        lines.append(
            f"**{fighter.dao_name}** — HP **{fighter.combatant.hp}/{fighter.combatant.max_hp}** ({hp_pct}%) · "
            f"dealt **{fighter.damage_dealt}** · took **{fighter.damage_taken}** · "
            f"actions **{fighter.actions_taken}**"
        )
    return "\n".join(lines)
