from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..ui.formatting import format_compact_number
from .rules import StatusRule, load_combat_rules


@dataclass
class StatusInstance:
    status_id: str
    stacks: int = 1
    turns_remaining: int = 1
    potency: float = 1.0


@dataclass
class CombatantState:
    hp: int
    max_hp: int
    statuses: list[StatusInstance] = field(default_factory=list)
    sealed: bool = False
    feared: bool = False
    dodge_next: bool = False
    control_dr: dict[str, int] = field(default_factory=dict)


def _status_rule(status_id: str) -> StatusRule | None:
    return load_combat_rules().statuses.get(status_id)


def apply_status(target: CombatantState, status_id: str, *, potency: float = 1.0) -> str | None:
    rule = _status_rule(status_id)
    if rule is None:
        return None
    scaled_potency = potency if rule.damage_per_stack > 0 else 1.0
    existing = next((s for s in target.statuses if s.status_id == status_id), None)
    if existing is not None:
        existing.stacks = min(rule.max_stacks, existing.stacks + 1)
        if rule.damage_per_stack > 0:
            # DoT: refresh duration; stacks raise per-tick damage.
            existing.turns_remaining = max(existing.turns_remaining, rule.duration)
            existing.potency = max(existing.potency, scaled_potency)
        else:
            # CC: each re-apply adds another turn skipped (capped).
            cc_cap = rule.max_stacks * rule.duration
            existing.turns_remaining = min(cc_cap, existing.turns_remaining + rule.duration)
    else:
        target.statuses.append(
            StatusInstance(
                status_id=status_id,
                stacks=1,
                turns_remaining=rule.duration,
                potency=scaled_potency,
            )
        )
    if status_id == "seal":
        target.sealed = True
    if status_id == "fear":
        target.feared = True
    if rule.control and rule.dr_window > 0:
        target.control_dr[status_id] = max(target.control_dr.get(status_id, 0), rule.dr_window)
    return status_id


def status_application_chance(target: CombatantState, status_id: str, base_chance: float) -> float:
    rule = _status_rule(status_id)
    if rule is None or not rule.control:
        return base_chance
    stacks = max(0, int(target.control_dr.get(status_id, 0)))
    if stacks <= 0:
        return base_chance
    return max(0.0, min(1.0, base_chance * (rule.dr_multiplier ** stacks)))


def get_status_instance(target: CombatantState, status_id: str) -> StatusInstance | None:
    return next((s for s in target.statuses if s.status_id == status_id), None)


def status_stacks(target: CombatantState, status_id: str) -> int:
    inst = get_status_instance(target, status_id)
    return inst.stacks if inst is not None else 0


def status_turns_remaining(target: CombatantState, status_id: str) -> int:
    inst = get_status_instance(target, status_id)
    return inst.turns_remaining if inst is not None else 0


def has_status(target: CombatantState, status_id: str) -> bool:
    return any(s.status_id == status_id for s in target.statuses)


def attacker_damage_multiplier(attacker: CombatantState) -> float:
    """Stacking penalty when the attacker is sealed, etc."""
    mult = 1.0
    for status in attacker.statuses:
        rule = _status_rule(status.status_id)
        if rule is not None and rule.damage_mult < 1.0:
            mult *= rule.damage_mult
    return mult


def is_stunned(target: CombatantState) -> bool:
    return has_status(target, "stun")


def turn_skip_message(actor: CombatantState, actor_name: str, rng: random.Random) -> str | None:
    """Stun always skips; fear may skip (roll can fail)."""
    if is_stunned(actor):
        return f"**{actor_name}** is stunned and cannot act."
    fear_rule = _status_rule("fear")
    if fear_rule is not None and has_status(actor, "fear") and fear_rule.skip_turn_chance > 0:
        if rng.random() < fear_rule.skip_turn_chance:
            return f"**{actor_name}** is frozen by fear and loses the turn!"
    return None


def spread_burn(
    carrier: CombatantState,
    carrier_name: str,
    others: list[tuple[CombatantState, str]],
    rng: random.Random,
) -> list[str]:
    """Spread burn to nearby foes (dungeon / multi-target fights). Bleed does not spread."""
    rule = _status_rule("burn")
    if rule is None or not rule.propagates or not has_status(carrier, "burn"):
        return []
    lines: list[str] = []
    for target, name in others:
        if has_status(target, "burn"):
            continue
        if rng.random() < rule.spread_chance:
            carrier_inst = get_status_instance(carrier, "burn")
            spread_potency = carrier_inst.potency if carrier_inst is not None else 1.0
            spread_cfg = load_combat_rules().dot_scaling
            spread_potency *= spread_cfg.spread_potency_ratio
            apply_status(target, "burn", potency=spread_potency)
            lines.append(f"**Burn** leaps from **{carrier_name}** to **{name}**!")
    return lines


def cleanse_debuffs(target: CombatantState, count: int, *, only: set[str] | None = None) -> list[str]:
    removed: list[str] = []
    remaining: list[StatusInstance] = []
    for status in target.statuses:
        if status.status_id not in {"burn", "bleed", "poison", "stun", "fear", "seal"}:
            remaining.append(status)
            continue
        if only is not None and status.status_id not in only:
            remaining.append(status)
            continue
        if len(removed) < count:
            removed.append(status.status_id)
        else:
            remaining.append(status)
    target.statuses = remaining
    target.sealed = has_status(target, "seal")
    target.feared = has_status(target, "fear")
    return removed


def tick_statuses(target: CombatantState) -> list[str]:
    """Apply end-of-round status damage and decay. Returns log lines."""
    lines: list[str] = []
    remaining: list[StatusInstance] = []
    for status in target.statuses:
        rule = _status_rule(status.status_id)
        if rule is None:
            continue
        if rule.damage_per_stack > 0:
            damage = max(1, int(rule.damage_per_stack * status.stacks * status.potency))
            target.hp = max(0, target.hp - damage)
            lines.append(f"**{status.status_id.title()}** deals **{format_compact_number(damage)}** damage.")
        status.turns_remaining -= 1
        if status.turns_remaining > 0:
            remaining.append(status)
    target.statuses = remaining
    target.control_dr = {
        status_id: turns - 1
        for status_id, turns in target.control_dr.items()
        if turns - 1 > 0
    }
    target.sealed = has_status(target, "seal")
    target.feared = has_status(target, "fear")
    return lines


def status_instances_to_json(statuses: list[StatusInstance]) -> list[dict]:
    return [
        {
            "status_id": s.status_id,
            "stacks": s.stacks,
            "turns_remaining": s.turns_remaining,
            "potency": s.potency,
        }
        for s in statuses
    ]


def status_instances_from_json(raw: list[dict]) -> list[StatusInstance]:
    return [
        StatusInstance(
            status_id=str(entry["status_id"]),
            stacks=int(entry.get("stacks", 1)),
            turns_remaining=int(entry.get("turns_remaining", 1)),
            potency=float(entry.get("potency", 1.0)),
        )
        for entry in raw
    ]
