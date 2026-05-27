from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CharacterModifiers:
    cultivate_qi_mult: float = 1.0
    breakthrough_stability: float = 0.0
    breakthrough_setback_mult: float = 1.0
    adventure_success: float = 0.0
    adventure_defense: float = 0.0
    dungeon_damage: float = 0.0
    dungeon_defense: float = 0.0
    drop_luck: float = 0.0
    rare_event_mult: float = 1.0
    pvp_power: float = 0.0
    pvp_stones_mult: float = 1.0
    offline_cap_mult: float = 1.0
    clan_contribution_mult: float = 1.0
    dungeon_luck: float = 0.0
    dungeon_risk: float = 0.0
    clarity_breakthrough_bonus: float = 0.0
    qi_gathering_mult: float = 1.0
    active_effects: list[str] = field(default_factory=list)
