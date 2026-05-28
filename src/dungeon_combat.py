from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .combat.catalog import get_technique
from .combat.targeting import technique_hits_all_enemies
from .combat.effects import (
    CombatantState,
    has_status,
    spread_burn,
    status_instances_from_json,
    status_instances_to_json,
    tick_statuses,
    turn_skip_message,
)
from .combat.engine import CombatState, execute_pvp_turn
from .combat.triggers import resolve_technique
from .combat.loadout import get_equipped_active_techniques, get_equipped_passive
from .combat.rules import load_combat_rules
from .combat_stats import compute_combat_stats
from .cooperative_dungeons import (
    CoopRoomDef,
    CooperativeDungeonDef,
    get_cooperative_dungeon,
    get_enemy_templates,
    scaled_enemy_stats,
)
from .dungeon_party import PartyMember
from .models import Player


def _combatant_to_dict(c: CombatantState) -> dict:
    return {
        "hp": c.hp,
        "max_hp": c.max_hp,
        "statuses": status_instances_to_json(c.statuses),
        "dodge_next": c.dodge_next,
        "sealed": c.sealed,
        "feared": c.feared,
    }


def _combatant_from_dict(data: dict) -> CombatantState:
    c = CombatantState(
        hp=int(data["hp"]),
        max_hp=int(data["max_hp"]),
        statuses=status_instances_from_json(data.get("statuses", [])),
        dodge_next=bool(data.get("dodge_next", False)),
    )
    c.sealed = has_status(c, "seal") or bool(data.get("sealed", False))
    c.feared = has_status(c, "fear") or bool(data.get("feared", False))
    return c


@dataclass
class DungeonFighter:
    fighter_id: str
    name: str
    is_enemy: bool
    player_id: int | None
    discord_id: str | None
    attack: int
    defense: int
    agility: int
    combatant: CombatantState
    technique_cooldowns: dict[str, int] = field(default_factory=dict)
    enemy_template_id: str | None = None
    combat_tier: str = "normal"

    def alive(self) -> bool:
        return self.combatant.hp > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fighter_id": self.fighter_id,
            "name": self.name,
            "is_enemy": self.is_enemy,
            "player_id": self.player_id,
            "discord_id": self.discord_id,
            "attack": self.attack,
            "defense": self.defense,
            "agility": self.agility,
            "combatant": _combatant_to_dict(self.combatant),
            "technique_cooldowns": self.technique_cooldowns,
            "enemy_template_id": self.enemy_template_id,
            "combat_tier": self.combat_tier,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DungeonFighter:
        return cls(
            fighter_id=str(data["fighter_id"]),
            name=str(data["name"]),
            is_enemy=bool(data["is_enemy"]),
            player_id=int(data["player_id"]) if data.get("player_id") is not None else None,
            discord_id=str(data["discord_id"]) if data.get("discord_id") else None,
            attack=int(data["attack"]),
            defense=int(data["defense"]),
            agility=int(data["agility"]),
            combatant=_combatant_from_dict(data["combatant"]),
            technique_cooldowns={str(k): int(v) for k, v in data.get("technique_cooldowns", {}).items()},
            enemy_template_id=data.get("enemy_template_id"),
            combat_tier=str(data.get("combat_tier", "normal")),
        )


@dataclass
class DungeonCombatState:
    party_id: int
    dungeon_id: str
    room_index: int
    room_label: str
    round_num: int
    fighters: dict[str, DungeonFighter]
    turn_order: list[str]
    turn_index: int
    log: list[str]
    finished: bool = False
    victory: bool = False
    room_cleared: bool = False
    run_complete: bool = False
    pending_technique: str | None = None
    pending_loot: dict[str, int] = field(default_factory=dict)
    looted_enemy_ids: set[str] = field(default_factory=set)
    log_cursor: int = 0

    @property
    def current_actor_id(self) -> str:
        return self.turn_order[self.turn_index] if self.turn_order else ""

    def current_actor(self) -> DungeonFighter | None:
        fid = self.current_actor_id
        return self.fighters.get(fid)

    def living_enemies(self) -> list[DungeonFighter]:
        return [f for f in self.fighters.values() if f.is_enemy and f.alive()]

    def living_players(self) -> list[DungeonFighter]:
        return [f for f in self.fighters.values() if not f.is_enemy and f.alive()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "party_id": self.party_id,
            "dungeon_id": self.dungeon_id,
            "room_index": self.room_index,
            "room_label": self.room_label,
            "round_num": self.round_num,
            "fighters": {k: v.to_dict() for k, v in self.fighters.items()},
            "turn_order": list(self.turn_order),
            "turn_index": self.turn_index,
            "log": self.log[-50:],
            "finished": self.finished,
            "victory": self.victory,
            "room_cleared": self.room_cleared,
            "run_complete": self.run_complete,
            "pending_technique": self.pending_technique,
            "pending_loot": dict(self.pending_loot),
            "looted_enemy_ids": sorted(self.looted_enemy_ids),
            "log_cursor": int(self.log_cursor),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DungeonCombatState:
        fighters = {k: DungeonFighter.from_dict(v) for k, v in data.get("fighters", {}).items()}
        return cls(
            party_id=int(data["party_id"]),
            dungeon_id=str(data["dungeon_id"]),
            room_index=int(data["room_index"]),
            room_label=str(data.get("room_label", "")),
            round_num=int(data.get("round_num", 1)),
            fighters=fighters,
            turn_order=list(data.get("turn_order", [])),
            turn_index=int(data.get("turn_index", 0)),
            log=list(data.get("log", [])),
            finished=bool(data.get("finished", False)),
            victory=bool(data.get("victory", False)),
            room_cleared=bool(data.get("room_cleared", False)),
            run_complete=bool(data.get("run_complete", False)),
            pending_technique=data.get("pending_technique"),
            pending_loot={str(k): int(v) for k, v in data.get("pending_loot", {}).items()},
            looted_enemy_ids=set(data.get("looted_enemy_ids", [])),
            log_cursor=int(data.get("log_cursor", 0)),
        )


def should_advance_room(state: DungeonCombatState) -> bool:
    """True when Discord layer should call advance_to_next_room."""
    return (
        state.finished
        and state.victory
        and state.room_cleared
        and not state.run_complete
    )


@dataclass(frozen=True)
class DungeonActionResult:
    ok: bool
    message: str
    state: DungeonCombatState
    needs_target: bool = False


def load_combat_state(party) -> DungeonCombatState | None:
    if not party.state_json or party.state_json == "{}":
        return None
    try:
        data = json.loads(party.state_json)
    except json.JSONDecodeError:
        return None
    return DungeonCombatState.from_dict(data)


def save_combat_state(party, state: DungeonCombatState) -> None:
    party.state_json = json.dumps(state.to_dict())


def _spawn_enemies_for_room(
    room: CoopRoomDef,
    *,
    realm_index: int,
    party_size: int,
) -> dict[str, DungeonFighter]:
    templates = get_enemy_templates()
    fighters: dict[str, DungeonFighter] = {}
    idx = 0

    def add_enemy(template_id: str, is_boss: bool, suffix: str = "") -> None:
        nonlocal idx
        base = templates.get(template_id)
        if base is None:
            return
        scaled = scaled_enemy_stats(
            base, realm_index=realm_index, party_size=party_size, is_boss=is_boss
        )
        fid = f"enemy:{idx}"
        name = scaled.name if not suffix else f"{scaled.name} {suffix}"
        fighters[fid] = DungeonFighter(
            fighter_id=fid,
            name=name,
            is_enemy=True,
            player_id=None,
            discord_id=None,
            attack=scaled.attack,
            defense=scaled.defense,
            agility=scaled.speed,
            combatant=CombatantState(hp=scaled.hp, max_hp=scaled.hp),
            enemy_template_id=template_id,
            combat_tier=base.combat_tier if is_boss else base.combat_tier,
        )
        idx += 1

    if room.boss_template:
        add_enemy(room.boss_template, True)
    for template_id, count in room.enemies:
        for n in range(count):
            add_enemy(template_id, False, suffix=str(n + 1) if count > 1 else "")

    return fighters


def _spawn_players(
    session: Session,
    members: list[PartyMember],
) -> dict[str, DungeonFighter]:
    fighters: dict[str, DungeonFighter] = {}
    for member in members:
        player = session.get(Player, member.player_id)
        if player is None:
            continue
        mod = get_character_modifiers(session, player)
        stats = compute_combat_stats(player, session, mod)
        fighters[member.discord_id] = DungeonFighter(
            fighter_id=member.discord_id,
            name=member.dao_name,
            is_enemy=False,
            player_id=player.id,
            discord_id=member.discord_id,
            attack=stats.external_strength,
            defense=stats.defense,
            agility=stats.agility,
            combatant=CombatantState(hp=stats.max_hp, max_hp=stats.max_hp),
        )
    return fighters


def roll_initiative(state: DungeonCombatState, rng: random.Random) -> list[str]:
    rolls: list[tuple[int, str]] = []
    for fid, fighter in state.fighters.items():
        if not fighter.alive():
            continue
        roll = fighter.agility + rng.randint(1, 10)
        rolls.append((roll, fid))
    rolls.sort(key=lambda x: (-x[0], x[1]))
    return [fid for _, fid in rolls]


def start_room_combat(
    session: Session,
    *,
    party_id: int,
    dungeon: CooperativeDungeonDef,
    room_index: int,
    members: list[PartyMember],
    rng: random.Random | None = None,
) -> DungeonCombatState:
    rng = rng or random.Random()
    room = dungeon.rooms[room_index]
    enemies = _spawn_enemies_for_room(
        room,
        realm_index=dungeon.realm_index,
        party_size=len(members),
    )
    players = _spawn_players(session, members)
    fighters = {**players, **enemies}
    state = DungeonCombatState(
        party_id=party_id,
        dungeon_id=dungeon.dungeon_id,
        room_index=room_index,
        room_label=room.label,
        round_num=1,
        fighters=fighters,
        turn_order=[],
        turn_index=0,
        log=[f"**{room.label}** — {len(enemies)} foe(s) block the path."],
    )
    state.turn_order = roll_initiative(state, rng)
    if state.turn_order:
        actor = state.current_actor()
        if actor:
            state.log.append(f"**{actor.name}** moves first (Round {state.round_num}).")
    return state


def _decay_cooldowns(cooldowns: dict[str, int]) -> dict[str, int]:
    return {k: max(0, v - 1) for k, v in cooldowns.items() if v - 1 > 0}


def _living_turn_order(state: DungeonCombatState) -> list[str]:
    return [fid for fid in state.turn_order if fid in state.fighters and state.fighters[fid].alive()]


def _advance_turn(state: DungeonCombatState, rng: random.Random) -> None:
    state.pending_technique = None
    order = _living_turn_order(state)
    if not order:
        return
    try:
        idx = order.index(state.current_actor_id)
    except ValueError:
        idx = -1
    next_idx = idx + 1
    if next_idx >= len(order):
        state.round_num += 1
        state.turn_order = roll_initiative(state, rng)
        state.turn_index = 0
        actor = state.current_actor()
        if actor:
            state.log.append(f"— Round **{state.round_num}** — **{actor.name}** acts.")
    else:
        state.turn_order = order
        state.turn_index = next_idx


def _collect_defeated_enemy_loot(state: DungeonCombatState, rng: random.Random) -> None:
    from .cooperative_dungeons import get_cooperative_dungeon, get_enemy_templates
    from .loot import merge_loot_dicts, roll_creature_loot

    dungeon = get_cooperative_dungeon(state.dungeon_id)
    if dungeon is None:
        return
    templates = get_enemy_templates()
    for fighter in state.fighters.values():
        if not fighter.is_enemy or fighter.alive():
            continue
        if fighter.fighter_id in state.looted_enemy_ids:
            continue
        template = templates.get(fighter.enemy_template_id or "")
        if template is None or not template.drops:
            state.looted_enemy_ids.add(fighter.fighter_id)
            continue
        tier = "boss" if fighter.combat_tier == "boss" else fighter.combat_tier
        loot = roll_creature_loot(
            template.drops,
            rng,
            combat_tier=tier,
            luck=8.0 + dungeon.realm_index,
            drop_luck=0.0,
            player_realm_index=dungeon.realm_index,
            area_min_realm=dungeon.realm_index,
        )
        if loot:
            state.pending_loot = merge_loot_dicts(state.pending_loot, loot)
            names = ", ".join(f"**{item}** ×{qty}" for item, qty in loot.items())
            state.log.append(f"**{fighter.name}** yields {names}.")
        state.looted_enemy_ids.add(fighter.fighter_id)


def _check_end(state: DungeonCombatState, rng: random.Random | None = None) -> None:
    if not state.living_players():
        state.finished = True
        state.victory = False
        state.log.append("Your party falls — the dungeon claims victory.")
        return
    if not state.living_enemies():
        state.room_cleared = True
        state.finished = True
        state.victory = True
        state.log.append(f"**{state.room_label}** is cleared!")
    if rng is not None:
        _collect_defeated_enemy_loot(state, rng)


def _enemy_attack(
    state: DungeonCombatState,
    actor: DungeonFighter,
    target: DungeonFighter,
    rng: random.Random,
) -> None:
    skip = turn_skip_message(actor.combatant, actor.name, rng)
    if skip:
        state.log.append(skip)
        return
    if target.combatant.dodge_next or rng.random() < 0.05:
        target.combatant.dodge_next = False
        state.log.append(f"**{target.name}** dodges **{actor.name}**'s strike!")
        return
    from .combat.effects import attacker_damage_multiplier

    raw = actor.attack * rng.uniform(0.9, 1.15)
    damage = max(1, int(raw - target.defense * 0.35))
    mult = attacker_damage_multiplier(actor.combatant)
    if mult < 1.0:
        damage = max(1, int(damage * mult))
    target.combatant.hp = max(0, target.combatant.hp - damage)
    state.log.append(
        f"**{actor.name}** strikes **{target.name}** for **{damage}** "
        f"(**{max(0, target.combatant.hp)}** HP)."
    )


def _run_enemy_turn(
    state: DungeonCombatState,
    actor: DungeonFighter,
    rng: random.Random,
) -> None:
    targets = state.living_players()
    if not targets:
        return
    target = rng.choice(targets)
    _enemy_attack(state, actor, target, rng)
    _check_end(state, rng)


def _burn_spread_targets(state: DungeonCombatState, carrier_id: str) -> list[tuple[CombatantState, str]]:
    carrier = state.fighters.get(carrier_id)
    if carrier is None or not carrier.is_enemy:
        return []
    return [
        (foe.combatant, foe.name)
        for foe in state.living_enemies()
        if foe.fighter_id != carrier_id
    ]


def _tick_fighters_after_action(state: DungeonCombatState, rng: random.Random) -> None:
    for fighter in state.fighters.values():
        if not fighter.alive():
            continue
        for line in tick_statuses(fighter.combatant):
            state.log.append(f"({fighter.name}) {line}")
        if fighter.is_enemy and has_status(fighter.combatant, "burn"):
            others = _burn_spread_targets(state, fighter.fighter_id)
            for line in spread_burn(fighter.combatant, fighter.name, others, rng):
                state.log.append(line)


def _resolve_player_technique(
    session: Session,
    state: DungeonCombatState,
    actor: DungeonFighter,
    technique_id: str,
    target: DungeonFighter,
    rng: random.Random,
    *,
    finalize_turn: bool = True,
    volley: bool = False,
) -> str | None:
    player = session.get(Player, actor.player_id)
    if player is None:
        return "Cultivator not found."
    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    passive = get_equipped_passive(session, player.id)
    slice_state = CombatState(
        turn=state.round_num,
        player=CombatantState(
            hp=actor.combatant.hp,
            max_hp=actor.combatant.max_hp,
            statuses=list(actor.combatant.statuses),
            dodge_next=actor.combatant.dodge_next,
        ),
        opponent=CombatantState(
            hp=target.combatant.hp,
            max_hp=target.combatant.max_hp,
            statuses=list(target.combatant.statuses),
            dodge_next=target.combatant.dodge_next,
        ),
        opponent_id=target.fighter_id,
        opponent_name=target.name,
        opponent_attack=target.attack,
        opponent_defense=target.defense,
        opponent_speed=target.agility,
        technique_cooldowns=dict(actor.technique_cooldowns),
        log=[],
        context="dungeon",
        player_label=actor.name,
    )
    slice_state.player.sealed = actor.combatant.sealed
    slice_state.player.feared = actor.combatant.feared
    slice_state.opponent.sealed = target.combatant.sealed
    slice_state.opponent.feared = target.combatant.feared

    skip = turn_skip_message(actor.combatant, actor.name, rng)
    if skip:
        state.log.append(skip)
        return None

    if finalize_turn:
        action = "technique" if technique_id != "basic_strike" else "strike"
        tech_id = technique_id if technique_id != "basic_strike" else None
        result = execute_pvp_turn(
            slice_state,
            stats,
            mod,
            passive,
            action,
            technique_id=tech_id,
            rng=rng,
        )
        if result.error:
            return result.error
    else:
        err = resolve_technique(slice_state, stats, passive, technique_id, rng, volley=volley)
        if err:
            return err

    actor.combatant.hp = slice_state.player.hp
    actor.combatant.statuses = slice_state.player.statuses
    actor.combatant.dodge_next = slice_state.player.dodge_next
    actor.combatant.sealed = slice_state.player.sealed
    actor.technique_cooldowns = slice_state.technique_cooldowns

    target.combatant.hp = slice_state.opponent.hp
    target.combatant.statuses = slice_state.opponent.statuses
    target.combatant.dodge_next = slice_state.opponent.dodge_next
    target.combatant.sealed = slice_state.opponent.sealed
    target.combatant.feared = slice_state.opponent.feared

    for line in slice_state.log:
        state.log.append(line)

    if target.is_enemy and has_status(target.combatant, "burn"):
        others = _burn_spread_targets(state, target.fighter_id)
        for line in spread_burn(target.combatant, target.name, others, rng):
            state.log.append(line)
    return None


def select_technique(
    session: Session,
    state: DungeonCombatState,
    actor_discord_id: str,
    technique_id: str,
    rng: random.Random | None = None,
) -> DungeonActionResult:
    rng = rng or random.Random()
    if state.finished:
        return DungeonActionResult(False, "This fight has ended.", state)
    actor = state.current_actor()
    if actor is None or actor.fighter_id != actor_discord_id:
        return DungeonActionResult(False, "Wait for your turn.", state)
    if actor.is_enemy:
        return DungeonActionResult(False, "Only daoists choose techniques.", state)
    if state.pending_technique:
        return DungeonActionResult(False, "Choose a target for your technique first.", state)

    tech = get_technique(technique_id)
    if tech is None and technique_id != "basic_strike":
        return DungeonActionResult(False, "Unknown technique.", state)

    cd = actor.technique_cooldowns.get(technique_id, 0)
    if cd > 0:
        return DungeonActionResult(False, f"**{tech.name if tech else technique_id}** is on cooldown.", state)
    if actor.combatant.sealed and technique_id != "basic_strike":
        return DungeonActionResult(False, "Your meridians are sealed — only **Basic Strike** responds.", state)

    label = tech.name if tech else "Basic Strike"

    if technique_hits_all_enemies(tech):
        skip = turn_skip_message(actor.combatant, actor.name, rng)
        if skip is None:
            enemies = list(state.living_enemies())
            for idx, foe in enumerate(enemies):
                err = _resolve_player_technique(
                    session,
                    state,
                    actor,
                    technique_id,
                    foe,
                    rng,
                    finalize_turn=False,
                    volley=idx < len(enemies) - 1,
                )
                if err:
                    return DungeonActionResult(False, err, state)
            tech = get_technique(technique_id)
            if tech is not None and tech.cooldown > 0:
                actor.technique_cooldowns[technique_id] = tech.cooldown
            _tick_fighters_after_action(state, rng)
        else:
            state.log.append(skip)
        state.pending_technique = None
        _check_end(state, rng)
        if not state.finished:
            actor.technique_cooldowns = _decay_cooldowns(actor.technique_cooldowns)
            _advance_turn(state, rng)
            _process_npc_turns(session, state, rng)
        return DungeonActionResult(
            True,
            state.log[-1] if state.log else f"**{label}** scorches every foe.",
            state,
            needs_target=False,
        )

    state.pending_technique = technique_id
    state.log.append(f"**{actor.name}** readies **{label}** — pick a foe below.")
    return DungeonActionResult(
        True,
        "",
        state,
        needs_target=True,
    )


def select_target(
    session: Session,
    state: DungeonCombatState,
    actor_discord_id: str,
    target_id: str,
    rng: random.Random | None = None,
) -> DungeonActionResult:
    rng = rng or random.Random()
    if state.finished:
        return DungeonActionResult(False, "This fight has ended.", state)
    actor = state.current_actor()
    if actor is None or actor.fighter_id != actor_discord_id:
        return DungeonActionResult(False, "Wait for your turn.", state)
    if not state.pending_technique:
        return DungeonActionResult(False, "Select a technique first.", state)

    target = state.fighters.get(target_id)
    if target is None or not target.is_enemy or not target.alive():
        return DungeonActionResult(False, "Pick a living foe.", state)

    skip = turn_skip_message(actor.combatant, actor.name, rng)
    if skip:
        state.log.append(skip)
    else:
        err = _resolve_player_technique(
            session, state, actor, state.pending_technique, target, rng, finalize_turn=True
        )
        if err:
            state.pending_technique = None
            return DungeonActionResult(False, err, state)
        _tick_fighters_after_action(state, rng)

    state.pending_technique = None
    _check_end(state, rng)
    if not state.finished:
        actor.technique_cooldowns = _decay_cooldowns(actor.technique_cooldowns)
        _advance_turn(state, rng)
        _process_npc_turns(session, state, rng)

    return DungeonActionResult(True, state.log[-1] if state.log else "Action resolved.", state)


def _process_npc_turns(session: Session, state: DungeonCombatState, rng: random.Random) -> None:
    """Run consecutive enemy turns until a player acts or combat ends."""
    rules = load_combat_rules()
    safety = 0
    while not state.finished and safety < 20:
        safety += 1
        actor = state.current_actor()
        if actor is None:
            break
        if not actor.is_enemy:
            break
        _run_enemy_turn(state, actor, rng)
        if state.finished:
            break
        actor.technique_cooldowns = _decay_cooldowns(actor.technique_cooldowns)
        _advance_turn(state, rng)
        if state.round_num > rules.max_turns and state.living_enemies():
            state.finished = True
            state.victory = False
            state.log.append("The foes endure — your assault stalls.")
            break


def process_turn_start(session: Session, state: DungeonCombatState, rng: random.Random) -> None:
    """Auto-run enemy turns when combat opens on an enemy initiative."""
    _process_npc_turns(session, state, rng)


def advance_to_next_room(
    session: Session,
    state: DungeonCombatState,
    members: list[PartyMember],
    rng: random.Random,
) -> DungeonCombatState:
    dungeon = get_cooperative_dungeon(state.dungeon_id)
    if dungeon is None:
        state.run_complete = True
        return state
    next_index = state.room_index + 1
    if next_index >= len(dungeon.rooms):
        state.run_complete = True
        state.log.append("The final chamber falls — the dungeon is yours!")
        return state
    pending_loot = dict(state.pending_loot)
    carry_log = list(state.log)
    log_cursor = state.log_cursor
    new_state = start_room_combat(
        session,
        party_id=state.party_id,
        dungeon=dungeon,
        room_index=next_index,
        members=members,
        rng=rng,
    )
    new_state.pending_loot = pending_loot
    new_state.log = carry_log + [f"Advancing to **{new_state.room_label}**…"] + new_state.log
    new_state.log_cursor = log_cursor
    process_turn_start(session, new_state, rng)
    return new_state
