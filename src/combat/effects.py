from __future__ import annotations

from dataclasses import dataclass, field

from .rules import StatusRule, load_combat_rules


@dataclass
class StatusInstance:
    status_id: str
    stacks: int = 1
    turns_remaining: int = 1


@dataclass
class CombatantState:
    hp: int
    max_hp: int
    statuses: list[StatusInstance] = field(default_factory=list)
    sealed: bool = False
    feared: bool = False
    dodge_next: bool = False


def _status_rule(status_id: str) -> StatusRule | None:
    return load_combat_rules().statuses.get(status_id)


def apply_status(target: CombatantState, status_id: str) -> str | None:
    rule = _status_rule(status_id)
    if rule is None:
        return None
    existing = next((s for s in target.statuses if s.status_id == status_id), None)
    if existing is not None:
        existing.stacks = min(rule.max_stacks, existing.stacks + 1)
        existing.turns_remaining = max(existing.turns_remaining, rule.duration)
    else:
        target.statuses.append(
            StatusInstance(status_id=status_id, stacks=1, turns_remaining=rule.duration)
        )
    if status_id == "seal":
        target.sealed = True
    if status_id == "fear":
        target.feared = True
    return status_id


def has_status(target: CombatantState, status_id: str) -> bool:
    return any(s.status_id == status_id for s in target.statuses)


def is_stunned(target: CombatantState) -> bool:
    return has_status(target, "stun")


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
            damage = rule.damage_per_stack * status.stacks
            target.hp = max(0, target.hp - damage)
            lines.append(f"**{status.status_id.title()}** deals **{damage}** damage.")
        status.turns_remaining -= 1
        if status.turns_remaining > 0:
            remaining.append(status)
    target.statuses = remaining
    target.sealed = has_status(target, "seal")
    target.feared = has_status(target, "fear")
    return lines


def status_instances_to_json(statuses: list[StatusInstance]) -> list[dict]:
    return [
        {"status_id": s.status_id, "stacks": s.stacks, "turns_remaining": s.turns_remaining}
        for s in statuses
    ]


def status_instances_from_json(raw: list[dict]) -> list[StatusInstance]:
    return [
        StatusInstance(
            status_id=str(entry["status_id"]),
            stacks=int(entry.get("stacks", 1)),
            turns_remaining=int(entry.get("turns_remaining", 1)),
        )
        for entry in raw
    ]
