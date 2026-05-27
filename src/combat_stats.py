from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from .models import Player
from .modifiers import CharacterModifiers

if TYPE_CHECKING:
    from .stats import EquipmentStats

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "realm_stats.json"

_realm_stats: dict | None = None


def _load_realm_stats() -> dict:
    global _realm_stats
    if _realm_stats is None:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            _realm_stats = json.load(f)
    return _realm_stats


@dataclass(frozen=True)
class PlayerCombatStats:
    hp: int
    max_hp: int
    internal_strength: int
    external_strength: int
    agility: int
    spiritual_sense: int
    defense: int
    comprehension: int
    luck: int
    crit_chance: float
    dodge: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "hp": self.hp,
            "max_hp": self.max_hp,
            "internal_strength": self.internal_strength,
            "external_strength": self.external_strength,
            "agility": self.agility,
            "spiritual_sense": self.spiritual_sense,
            "defense": self.defense,
            "comprehension": self.comprehension,
            "luck": self.luck,
            "crit_chance": self.crit_chance,
            "dodge": self.dodge,
        }


def _stat_from_realm(key: str, realm_index: int, substage: int, cfg: dict) -> int:
    base = int(cfg["base"][key])
    per_realm = int(cfg["per_realm"][key])
    per_sub = int(cfg["per_substage"][key])
    return base + realm_index * per_realm + substage * per_sub


def _apply_gear(stats: dict[str, int], gear: EquipmentStats, cfg: dict) -> None:
    mapping = cfg["gear_mapping"]
    stats["internal_strength"] += int(gear.power * mapping["power_internal_ratio"])
    stats["external_strength"] += int(gear.power * mapping["power_external_ratio"])
    stats["defense"] += int(gear.defense * mapping["defense_per_point"])
    stats["luck"] += int(gear.fortune * mapping["fortune_luck_ratio"])
    stats["spiritual_sense"] += int(gear.insight * mapping["insight_spiritual_sense_ratio"])
    stats["comprehension"] += int(gear.insight * mapping["insight_comprehension_ratio"])


def compute_combat_stats(
    player: Player,
    session: Session,
    mod: CharacterModifiers | None = None,
) -> PlayerCombatStats:
    from .stats import get_total_equipment_stats

    cfg = _load_realm_stats()
    realm_index = max(0, player.realm_index)
    substage = max(0, min(player.substage, 2))

    stats: dict[str, int] = {}
    for key in cfg["base"]:
        stats[key] = _stat_from_realm(key, realm_index, substage, cfg)

    gear = get_total_equipment_stats(session, player.id)
    _apply_gear(stats, gear, cfg)

    if mod is not None:
        stats["internal_strength"] = int(
            stats["internal_strength"] * (1.0 + mod.dungeon_damage * 0.4 + mod.pvp_power * 0.2)
        )
        stats["external_strength"] = int(
            stats["external_strength"] * (1.0 + mod.pvp_power * 0.3 + mod.dungeon_damage * 0.2)
        )
        stats["defense"] = int(stats["defense"] * (1.0 + mod.adventure_defense + mod.dungeon_defense * 0.5))
        stats["luck"] = int(stats["luck"] * (1.0 + mod.drop_luck))
        stats["spiritual_sense"] = int(stats["spiritual_sense"] * mod.rare_event_mult)

    derived = cfg["derived"]
    crit = (
        stats["spiritual_sense"] * derived["crit_per_spiritual_sense"]
        + stats["luck"] * derived["crit_per_luck"]
    )
    dodge = stats["agility"] * derived["dodge_per_agility"]
    if mod is not None:
        crit = min(0.45, crit + mod.adventure_success * 0.05)
        dodge = min(0.40, dodge + mod.adventure_defense * 0.03)

    max_hp = max(1, stats["hp"])
    return PlayerCombatStats(
        hp=max_hp,
        max_hp=max_hp,
        internal_strength=max(1, stats["internal_strength"]),
        external_strength=max(1, stats["external_strength"]),
        agility=max(1, stats["agility"]),
        spiritual_sense=max(1, stats["spiritual_sense"]),
        defense=max(1, stats["defense"]),
        comprehension=max(1, stats["comprehension"]),
        luck=max(1, stats["luck"]),
        crit_chance=max(0.0, min(0.45, crit)),
        dodge=max(0.0, min(0.40, dodge)),
    )


def gather_quantity_bonus(comprehension: int) -> float:
    cfg = _load_realm_stats()
    return 1.0 + (comprehension / 10.0) * cfg["derived"]["comprehension_gather_bonus_per_10"]


def gather_rare_bonus(luck: int, drop_luck: float = 0.0) -> float:
    cfg = _load_realm_stats()
    return (luck / 10.0) * cfg["derived"]["luck_rare_bonus_per_10"] + drop_luck


def format_combat_stats_block(stats: PlayerCombatStats) -> str:
    lines = [
        f"**HP** {stats.hp}/{stats.max_hp} · **Defense** {stats.defense}",
        f"**Internal** {stats.internal_strength} · **External** {stats.external_strength}",
        f"**Agility** {stats.agility} · **Spirit Sense** {stats.spiritual_sense}",
        f"**Comprehension** {stats.comprehension} · **Luck** {stats.luck}",
        f"**Crit** {stats.crit_chance * 100:.1f}% · **Dodge** {stats.dodge * 100:.1f}%",
    ]
    return "\n".join(lines)


def format_combat_stats_summary(session: Session, player: Player, mod: CharacterModifiers) -> str:
    stats = compute_combat_stats(player, session, mod)
    block = format_combat_stats_block(stats)
    return block + "\n\n_Combat stats scale with realm, gear, and modifiers. Use `/techniques` for your loadout._"
