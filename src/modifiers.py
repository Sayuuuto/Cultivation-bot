from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CharacterModifiers:
    # Cultivation
    cultivate_speed: float = 1.0
    offline_efficiency: float = 1.0

    # Breakthrough
    breakthrough_luck: float = 0.0
    setback_resistance: float = 1.0

    # Combat
    damageBonus: float = 0.0
    damageReduction: float = 0.0

    # Adventure/Loot
    adventure_luck: float = 0.0
    dropBonus: float = 0.0

    active_effects: list[str] = field(default_factory=list)
