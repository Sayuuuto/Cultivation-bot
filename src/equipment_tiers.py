from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .combat_stats import realm_baseline_stats
from .models import EQUIPMENT_SLOTS
from .realms import get_realm_name

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "equipment_tiers.json"

GEAR_PATHS = ("internal", "external", "crit")
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


@lru_cache(maxsize=1)
def _load_cfg() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def invalidate_equipment_tiers_cache() -> None:
    _load_cfg.cache_clear()
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


def gear_mapping_for_path(path: str, base_mapping: dict) -> dict:
    cfg = _load_cfg()
    normalized = normalize_gear_path(path)
    overrides = cfg.get("path_gear_mapping", {}).get(normalized, {})
    merged = dict(base_mapping)
    merged.update(overrides)
    return merged


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


def _scale_range(
    mortal_range: list[int],
    *,
    realm_index: int,
    scale_key: str,
) -> list[int]:
    cfg = _load_cfg()
    stat_keys = cfg.get("stat_scale_keys", {})
    baseline_key = str(stat_keys.get(scale_key, "external_strength"))
    mortal = realm_baseline_stats(0, 0)
    target = realm_baseline_stats(realm_index, 0)
    mortal_val = max(1, int(mortal.get(baseline_key, 1)))
    target_val = max(1, int(target.get(baseline_key, 1)))
    scale = target_val / mortal_val
    low = max(0, int(round(mortal_range[0] * scale)))
    high = max(low, int(round(mortal_range[1] * scale)))
    if high == 0 and mortal_range[1] > 0:
        high = max(1, int(round(scale)))
    return [low, high]


def resolve_equipment_tier(
    realm_index: int,
    slot: str,
    grade: str = "external",
) -> EquipmentTierEntry | None:
    slot = slot.lower()
    path = normalize_gear_path(grade)
    if slot not in EQUIPMENT_SLOTS or path not in GEAR_PATHS:
        return None

    cfg = _load_cfg()
    template = cfg.get("path_slot_templates", {}).get(path, {}).get(slot)
    if template is None:
        return None

    realm_index = max(0, min(int(realm_index), 9))
    mortal_ranges: dict[str, list[int]] = dict(template.get("stat_ranges", {}))
    stat_ranges = {
        stat: _scale_range(rng, realm_index=realm_index, scale_key=stat)
        for stat, rng in mortal_ranges.items()
    }

    realm_name = get_realm_name(realm_index)
    base_name = str(template.get("name", slot.title()))
    path_name = path_label(path)
    display_name = f"{realm_name} {base_name} ({path_name})"

    return EquipmentTierEntry(
        tier_id=f"realm_{realm_index}_{slot}_{path}",
        min_realm=realm_index,
        grade=path,
        slot=slot,
        name=display_name,
        item_id=str(template.get("item_id", slot)),
        technique_tag=template.get("technique_tag"),
        inputs=_inputs_for_path(realm_index),
        stat_ranges=stat_ranges,
    )


@lru_cache(maxsize=128)
def list_equipment_tier_entries(realm_index: int) -> tuple[EquipmentTierEntry, ...]:
    entries: list[EquipmentTierEntry] = []
    for slot in EQUIPMENT_SLOTS:
        for path in GEAR_PATHS:
            entry = resolve_equipment_tier(realm_index, slot, path)
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
