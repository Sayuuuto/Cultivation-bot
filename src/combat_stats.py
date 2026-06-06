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
    "qi_power",
    "might",
    "speed",
    "perception",
    "armor",
    "resolve",
)

_realm_stats: dict | None = None


def _load_realm_stats() -> dict:
    global _realm_stats
    if _realm_stats is None:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            _realm_stats = json.load(f)
    return _realm_stats


def invalidate_realm_stats_cache() -> None:
    global _realm_stats
    _realm_stats = None


@dataclass(frozen=True)
class PlayerCombatStats:
    hp: int
    max_hp: int
    qi_power: int
    might: int
    speed: int
    perception: int
    armor: int
    resolve: int
    crit_chance: float
    dodge: float
    luck: float = 0.0
    technique_tag_counts: dict[str, int] | None = None

    def as_dict(self) -> dict[str, int | float]:
        return {
            "hp": self.hp,
            "max_hp": self.max_hp,
            "qi_power": self.qi_power,
            "might": self.might,
            "speed": self.speed,
            "perception": self.perception,
            "armor": self.armor,
            "resolve": self.resolve,
            "crit_chance": self.crit_chance,
            "dodge": self.dodge,
            "luck": self.luck,
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
            int(round(attack * (target["might"] / mortal["might"]) * attack_mult * tier_mult)),
        ),
        "defense": max(1, int(round(defense * (target["armor"] / mortal["armor"]) * tier_mult))),
    }


def _apply_gear(stats: dict[str, int], gear: EquipmentStats, path: str, cfg: dict) -> None:
    from .equipment_tiers import normalize_gear_path

    path = normalize_gear_path(path)

    stats["armor"] += gear.warding

    if path == "external":
        stats["might"] += gear.might
        stats["perception"] += gear.finesse
    elif path == "internal":
        stats["qi_power"] += gear.might
        stats["perception"] += gear.finesse
    elif path == "hp":
        stats["hp"] += gear.vitality
        stats["perception"] += gear.finesse


def gear_combat_contribution(gear: EquipmentStats, path: str, cfg: dict | None = None) -> dict[str, int]:
    """Return per-stat combat deltas from one gear piece (for display breakdowns)."""
    cfg = cfg or _load_realm_stats()
    out = {key: 0 for key in STAT_KEYS}
    _apply_gear(out, gear, path, cfg)
    return out


def _apply_player_gear(
    session: Session,
    player_id: int,
    stats: dict[str, int],
    cfg: dict,
    *,
    player_realm_index: int,
) -> None:
    from sqlalchemy import select

    from .equipment_tiers import normalize_gear_path
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
        _apply_gear(stats, gear, path, cfg)


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
        stats["qi_power"] = int(stats["qi_power"] * (1.0 + mod.damageBonus))
        stats["might"] = int(stats["might"] * (1.0 + mod.damageBonus))
        stats["armor"] = int(stats["armor"] * (1.0 + mod.damageReduction))

    derived = cfg["derived"]
    crit = stats["perception"] * derived["crit_per_perception"]
    dodge = stats["speed"] * derived["dodge_per_speed"]

    max_hp = max(1, stats["hp"])
    return PlayerCombatStats(
        hp=max_hp,
        max_hp=max_hp,
        qi_power=max(1, stats["qi_power"]),
        might=max(1, stats["might"]),
        speed=max(1, stats["speed"]),
        perception=max(1, stats["perception"]),
        armor=max(1, stats["armor"]),
        resolve=max(1, stats["resolve"]),
        crit_chance=max(0.0, min(0.50, crit)),
        dodge=max(0.0, min(0.40, dodge)),
        technique_tag_counts=tag_counts,
    )


def gather_quantity_bonus(perception: int) -> float:
    return 1.0 + (perception / 10.0) * 0.005


def gather_rare_bonus(fortune: float = 0.0) -> float:
    return fortune * 0.01


def format_combat_stats_block(stats: PlayerCombatStats) -> str:
    lines = [
        f"**Vitality** {stats.hp}/{stats.max_hp} · **Armor** {stats.armor}",
        f"**Might** {stats.might} · **Qi Power** {stats.qi_power}",
        f"**Speed** {stats.speed} · **Perception** {stats.perception}",
        f"**Resolve** {stats.resolve}",
        f"**Crit** {stats.crit_chance * 100:.1f}% · **Dodge** {stats.dodge * 100:.1f}%",
    ]
    return "\n".join(lines)


def format_combat_stats_summary(session: Session, player: Player, mod: CharacterModifiers) -> str:
    stats = compute_combat_stats(player, session, mod)
    block = format_combat_stats_block(stats)
    return block + "\n\n_Combat stats scale with realm, gear, and modifiers. Use `/techniques` for your loadout._"
