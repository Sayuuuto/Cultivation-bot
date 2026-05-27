from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "combat_rules.json"


@dataclass(frozen=True)
class StatusRule:
    status_id: str
    damage_per_stack: int = 0
    duration: int = 1
    max_stacks: int = 1
    damage_mult: float = 1.0
    propagates: bool = False
    spread_chance: float = 0.0
    skip_turn_chance: float = 0.0
    cancels_turn: bool = False


@dataclass(frozen=True)
class CombatRules:
    max_turns: int
    auto_finish_after_turn: int
    partial_win_beast_hp_fraction: float
    flee_base_chance: float
    beast_speed_from_attack_ratio: float
    statuses: dict[str, StatusRule]


@lru_cache(maxsize=1)
def load_combat_rules() -> CombatRules:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    statuses: dict[str, StatusRule] = {}
    for status_id, data in raw.get("statuses", {}).items():
        statuses[status_id] = StatusRule(
            status_id=status_id,
            damage_per_stack=int(data.get("damage_per_stack", 0)),
            duration=int(data.get("duration", 1)),
            max_stacks=int(data.get("max_stacks", 1)),
            damage_mult=float(data.get("damage_mult", 1.0)),
            propagates=bool(data.get("propagates", False)),
            spread_chance=float(data.get("spread_chance", 0.0)),
            skip_turn_chance=float(data.get("skip_turn_chance", 0.0)),
            cancels_turn=bool(data.get("cancels_turn", False)),
        )
    return CombatRules(
        max_turns=int(raw.get("max_turns", 8)),
        auto_finish_after_turn=int(raw.get("auto_finish_after_turn", 6)),
        partial_win_beast_hp_fraction=float(raw.get("partial_win_beast_hp_fraction", 0.40)),
        flee_base_chance=float(raw.get("flee_base_chance", 0.55)),
        beast_speed_from_attack_ratio=float(raw.get("beast_speed_from_attack_ratio", 0.5)),
        statuses=statuses,
    )
