from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .models import EQUIPMENT_SLOTS
from .realms import get_realm_name

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "equipment_tiers.json"
CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "gear_catalog.json"

GEAR_PATHS = ("internal", "external", "hp")
GEAR_RARITIES = ("common", "rare", "legendary")
# Backward-compatible alias used by forge autocomplete and DB column gear_grade.
GEAR_GRADES = GEAR_PATHS


@dataclass(frozen=True)
class EquipmentTierEntry:
    tier_id: str
    min_realm: int
    grade: str
    slot: str
    name: str
    item_id: str
    technique_tag: str | None
    inputs: dict[str, int]
    stat_ranges: dict[str, list[int]]
    rarity: str = "common"


@lru_cache(maxsize=1)
def _load_cfg() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_catalog_raw() -> tuple[dict, ...]:
    with CATALOG_PATH.open(encoding="utf-8") as f:
        return tuple(json.load(f))


@lru_cache(maxsize=1)
def _catalog_index() -> dict[tuple[int, str, str, str], dict]:
    index: dict[tuple[int, str, str, str], dict] = {}
    for row in _load_catalog_raw():
        key = (
            int(row["realm_index"]),
            str(row["slot"]).lower(),
            str(row["path"]).lower(),
            str(row["rarity"]).lower(),
        )
        index[key] = row
    return index


def invalidate_equipment_tiers_cache() -> None:
    _load_cfg.cache_clear()
    _load_catalog_raw.cache_clear()
    _catalog_index.cache_clear()
    list_equipment_tier_entries.cache_clear()


def normalize_gear_path(raw: str | None) -> str:
    path = str(raw or "external").lower()
    cfg = _load_cfg()
    legacy = cfg.get("legacy_grade_to_path", {})
    if path in legacy:
        path = str(legacy[path])
    if path in GEAR_PATHS:
        return path
    return "external"


def path_label(path: str) -> str:
    cfg = _load_cfg()
    normalized = normalize_gear_path(path)
    return str(cfg.get("path_labels", {}).get(normalized, normalized.title()))


def grade_label(grade: str) -> str:
    return path_label(grade)


def path_blurb(path: str) -> str:
    cfg = _load_cfg()
    normalized = normalize_gear_path(path)
    return str(cfg.get("path_blurbs", {}).get(normalized, ""))


def rarity_label(rarity: str) -> str:
    labels = {"common": "Common", "rare": "Rare", "legendary": "Legendary"}
    return labels.get(str(rarity).lower(), rarity.title())


def roll_forge_rarity(rng: random.Random) -> str:
    cfg = _load_cfg()
    weights = cfg.get("forge_rarity_weights", {"common": 70, "rare": 25, "legendary": 5})
    choices = list(GEAR_RARITIES)
    w = [int(weights.get(r, 0)) for r in choices]
    if not any(w):
        return "common"
    return rng.choices(choices, weights=w, k=1)[0]


def _realm_materials_for(realm_index: int) -> dict:
    cfg = _load_cfg()
    tiers = cfg.get("realm_materials", [])
    chosen = tiers[0] if tiers else {}
    for tier in tiers:
        if int(tier.get("min_realm", 0)) <= realm_index:
            chosen = tier
    return dict(chosen)


def _inputs_for_path(realm_index: int) -> dict[str, int]:
    cfg = _load_cfg()
    mats = _realm_materials_for(realm_index)
    qty_cfg = cfg.get("forge_inputs", {"primary": 2, "secondary": 1})
    inputs: dict[str, int] = {}
    primary = mats.get("primary")
    secondary = mats.get("secondary")
    if primary and qty_cfg.get("primary"):
        inputs[str(primary)] = int(qty_cfg["primary"])
    if secondary and qty_cfg.get("secondary"):
        inputs[str(secondary)] = int(qty_cfg["secondary"])
    return inputs


def _entry_from_catalog_row(row: dict, *, realm_index: int) -> EquipmentTierEntry:
    path = normalize_gear_path(str(row["path"]))
    slot = str(row["slot"]).lower()
    rarity = str(row.get("rarity", "common")).lower()
    path_name = path_label(path)
    display_name = f"{row['name']} ({path_name})"
    return EquipmentTierEntry(
        tier_id=str(row["id"]),
        min_realm=realm_index,
        grade=path,
        slot=slot,
        name=display_name,
        item_id=str(row["item_id"]),
        technique_tag=row.get("technique_tag"),
        inputs=_inputs_for_path(realm_index),
        stat_ranges={k: list(v) for k, v in row["stat_ranges"].items()},
        rarity=rarity,
    )


def resolve_gear_entry(
    realm_index: int,
    slot: str,
    path: str,
    rarity: str = "common",
) -> EquipmentTierEntry | None:
    slot = slot.lower()
    path = normalize_gear_path(path)
    rarity = str(rarity).lower()
    if slot not in EQUIPMENT_SLOTS or path not in GEAR_PATHS or rarity not in GEAR_RARITIES:
        return None

    realm_index = max(0, min(int(realm_index), 9))
    row = _catalog_index().get((realm_index, slot, path, rarity))
    if row is None:
        return None
    return _entry_from_catalog_row(row, realm_index=realm_index)


def resolve_equipment_tier(
    realm_index: int,
    slot: str,
    grade: str = "external",
    *,
    rarity: str = "common",
) -> EquipmentTierEntry | None:
    """Resolve common-tier catalog entry (backward-compatible helper)."""
    return resolve_gear_entry(realm_index, slot, grade, rarity)


@lru_cache(maxsize=128)
def list_equipment_tier_entries(realm_index: int) -> tuple[EquipmentTierEntry, ...]:
    entries: list[EquipmentTierEntry] = []
    realm_index = max(0, min(int(realm_index), 9))
    for slot in EQUIPMENT_SLOTS:
        for path in GEAR_PATHS:
            entry = resolve_gear_entry(realm_index, slot, path, "common")
            if entry is not None:
                entries.append(entry)
    return tuple(entries)


def list_forge_grades_for_player(realm_index: int) -> list[str]:
    return list(GEAR_PATHS)


def gear_is_active(eq, player_realm_index: int) -> bool:
    gear_realm = int(getattr(eq, "gear_realm", 0) or 0)
    return gear_realm == max(0, int(player_realm_index))


def gear_status_label(eq, player_realm_index: int) -> str | None:
    if not eq.item_id:
        return None
    gear_realm = int(getattr(eq, "gear_realm", 0) or 0)
    player_realm = max(0, int(player_realm_index))
    if gear_realm < player_realm:
        return f"Outgrown ({get_realm_name(gear_realm)} gear)"
    if gear_realm > player_realm:
        return f"Overcharged ({get_realm_name(gear_realm)} gear)"
    return path_label(getattr(eq, "gear_grade", None) or "external")
