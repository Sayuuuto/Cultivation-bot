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
    control: bool = False
    dr_window: int = 0
    dr_multiplier: float = 1.0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PvpLegalityRules:
    max_legendary: int = 1
    max_control: int = 2
    max_shield: int = 2
    max_heal: int = 2
    max_survival_passive: int = 1


@dataclass(frozen=True)
class KarmaPolicy:
    techniques_shift_karma_in_combat: bool = False
    max_karma_per_fight: int = 3
    max_karma_per_day: int = 10


@dataclass(frozen=True)
class CombatRules:
    max_turns: int
    auto_finish_after_turn: int
    partial_win_beast_hp_fraction: float
    flee_base_chance: float
    beast_speed_from_attack_ratio: float
    statuses: dict[str, StatusRule]
    feature_flags: dict[str, bool]
    pvp_legality: PvpLegalityRules
    karma_policy: KarmaPolicy

    def enabled(self, flag: str) -> bool:
        return bool(self.feature_flags.get(flag, False))


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
            control=bool(data.get("control", False)),
            dr_window=int(data.get("dr_window", 0)),
            dr_multiplier=float(data.get("dr_multiplier", 1.0)),
            tags=tuple(str(tag) for tag in data.get("tags", [])),
        )
    pvp_raw = raw.get("pvp_legality", {})
    karma_raw = raw.get("karma_policy", {})
    karma_policy = KarmaPolicy(
        techniques_shift_karma_in_combat=bool(karma_raw.get("techniques_shift_karma_in_combat", False)),
        max_karma_per_fight=int(karma_raw.get("max_karma_per_fight", 3)),
        max_karma_per_day=int(karma_raw.get("max_karma_per_day", 10)),
    )
    return CombatRules(
        max_turns=int(raw.get("max_turns", 8)),
        auto_finish_after_turn=int(raw.get("auto_finish_after_turn", 6)),
        partial_win_beast_hp_fraction=float(raw.get("partial_win_beast_hp_fraction", 0.40)),
        flee_base_chance=float(raw.get("flee_base_chance", 0.55)),
        beast_speed_from_attack_ratio=float(raw.get("beast_speed_from_attack_ratio", 0.5)),
        statuses=statuses,
        feature_flags={str(k): bool(v) for k, v in raw.get("feature_flags", {}).items()},
        pvp_legality=PvpLegalityRules(
            max_legendary=int(pvp_raw.get("max_legendary", 1)),
            max_control=int(pvp_raw.get("max_control", 2)),
            max_shield=int(pvp_raw.get("max_shield", 2)),
            max_heal=int(pvp_raw.get("max_heal", 2)),
            max_survival_passive=int(pvp_raw.get("max_survival_passive", 1)),
        ),
        karma_policy=karma_policy,
    )
