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
STAT_KEYS = (
    "hp",
    "internal_strength",
    "external_strength",
    "agility",
    "spiritual_sense",
    "defense",
    "comprehension",
    "luck",
)

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
    technique_tag_counts: dict[str, int] | None = None

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


def _realm_baselines(cfg: dict) -> list[dict[str, int]]:
    if "realm_baselines" in cfg:
        return [
            {key: int(row[key]) for key in STAT_KEYS}
            for row in cfg["realm_baselines"]
        ]
    return [
        {
            key: int(cfg["base"][key])
            + realm_index * int(cfg["per_realm"][key])
            for key in STAT_KEYS
        }
        for realm_index in range(10)
    ]


def _substage_multiplier(cfg: dict, substage: int) -> float:
    multipliers = cfg.get("substage_multipliers", [1.0, 1.25, 1.55])
    idx = max(0, min(int(substage), len(multipliers) - 1))
    return float(multipliers[idx])


def realm_baseline_stats(realm_index: int, substage: int = 0, cfg: dict | None = None) -> dict[str, int]:
    cfg = cfg or _load_realm_stats()
    baselines = _realm_baselines(cfg)
    idx = max(0, min(int(realm_index), len(baselines) - 1))
    mult = _substage_multiplier(cfg, substage)
    return {key: max(1, int(round(value * mult))) for key, value in baselines[idx].items()}


def _stat_from_realm(key: str, realm_index: int, substage: int, cfg: dict) -> int:
    return realm_baseline_stats(realm_index, substage, cfg)[key]


def scale_monster_stats(
    hp: int,
    attack: int,
    defense: int,
    *,
    realm_index: int,
    combat_tier: str = "normal",
    cfg: dict | None = None,
) -> dict[str, int]:
    """Scale mortal-template monster stats onto the target realm curve."""
    cfg = cfg or _load_realm_stats()
    target = realm_baseline_stats(realm_index, 0, cfg)
    mortal = realm_baseline_stats(0, 0, cfg)
    scaling = cfg.get("monster_scaling", {})
    tier_multipliers = scaling.get("tier_multipliers", {})
    tier_mult = float(tier_multipliers.get(combat_tier, tier_multipliers.get("elite", 1.25)))
    attack_mult = float(scaling.get("attack_multiplier", 1.0))
    return {
        "hp": max(1, int(round(hp * (target["hp"] / mortal["hp"]) * tier_mult))),
        "attack": max(
            1,
            int(round(attack * (target["external_strength"] / mortal["external_strength"]) * attack_mult * tier_mult)),
        ),
        "defense": max(1, int(round(defense * (target["defense"] / mortal["defense"]) * tier_mult))),
    }


def _apply_gear(stats: dict[str, int], gear: EquipmentStats, cfg: dict, *, mapping: dict | None = None) -> None:
    mapping = mapping or cfg["gear_mapping"]
    stats["internal_strength"] += int(gear.power * mapping["power_internal_ratio"])
    stats["external_strength"] += int(gear.power * mapping["power_external_ratio"])
    stats["defense"] += int(gear.defense * mapping["defense_per_point"])
    stats["luck"] += int(gear.fortune * mapping["fortune_luck_ratio"])
    stats["spiritual_sense"] += int(gear.insight * mapping["insight_spiritual_sense_ratio"])
    stats["comprehension"] += int(gear.insight * mapping["insight_comprehension_ratio"])


def _apply_player_gear(
    session: Session,
    player_id: int,
    stats: dict[str, int],
    cfg: dict,
    *,
    player_realm_index: int,
) -> None:
    from sqlalchemy import select

    from .equipment_tiers import gear_mapping_for_path, normalize_gear_path
    from .gear_stash import resolve_equipped_gear
    from .models import PlayerEquipment
    from .stats import equipment_row_is_active, stats_from_gear_view

    stmt = select(PlayerEquipment).where(PlayerEquipment.player_id == player_id)
    for eq in session.execute(stmt).scalars():
        view = resolve_equipped_gear(session, eq)
        if view is None or not equipment_row_is_active(session, eq, player_realm_index):
            continue
        gear = stats_from_gear_view(view, active=True)
        path = normalize_gear_path(view.gear_grade)
        mapping = gear_mapping_for_path(path, cfg["gear_mapping"])
        _apply_gear(stats, gear, cfg, mapping=mapping)


def compute_combat_stats(
    player: Player,
    session: Session,
    mod: CharacterModifiers | None = None,
) -> PlayerCombatStats:
    from .stats import get_technique_tag_counts

    cfg = _load_realm_stats()
    realm_index = max(0, player.realm_index)
    substage = max(0, min(player.substage, 2))

    stats: dict[str, int] = {}
    for key in STAT_KEYS:
        stats[key] = _stat_from_realm(key, realm_index, substage, cfg)

    from .foundation import apply_foundation_bonuses

    apply_foundation_bonuses(player, stats)

    _apply_player_gear(session, player.id, stats, cfg, player_realm_index=realm_index)
    tag_counts = get_technique_tag_counts(session, player.id, player_realm_index=realm_index)

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
        technique_tag_counts=tag_counts,
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
