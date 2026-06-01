#!/usr/bin/env python3
"""Build config/gear_catalog.json — 360 fixed forge entries (build-time only)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REALMS_PATH = ROOT / "config" / "realms.json"
OUTPUT_PATH = ROOT / "config" / "gear_catalog.json"

SLOTS = ("weapon", "armor", "accessory", "talisman")
PATHS = ("internal", "external", "hp")
RARITIES = ("common", "rare", "legendary")

# Anchor external-armor common ranges (realms 0, 1, 3) — validated in tests.
EXTERNAL_ARMOR_COMMON_ANCHORS: dict[int, dict[str, list[int]]] = {
    0: {"power": [5, 15], "defense": [15, 50], "fortune": [1, 5], "insight": [1, 5]},
    1: {"power": [50, 150], "defense": [150, 500], "fortune": [10, 50], "insight": [10, 50]},
    3: {"power": [500, 1500], "defense": [1500, 5000], "fortune": [100, 500], "insight": [100, 500]},
}

# Mortal profile ratios vs external armor (realm 0); scaled by realm multiplier elsewhere.
MORTAL_PROFILES: dict[str, dict[str, dict[str, list[int] | float]]] = {
    "external": {
        "weapon": {"power": [8, 18], "defense": [2, 8], "fortune": [1, 4], "insight": [1, 4]},
        "armor": {"power": [5, 15], "defense": [15, 50], "fortune": [1, 5], "insight": [1, 5]},
        "accessory": {"power": [4, 12], "defense": [3, 10], "fortune": [3, 12], "insight": [2, 6]},
        "talisman": {"power": [5, 14], "defense": [4, 12], "fortune": [2, 8], "insight": [6, 14]},
    },
    "internal": {
        "weapon": {"power": [6, 14], "defense": [1, 4], "fortune": [1, 4], "insight": [3, 8]},
        "armor": {"power": [2, 8], "defense": [10, 35], "fortune": [1, 5], "insight": [3, 8]},
        "accessory": {"power": [3, 10], "defense": [1, 4], "fortune": [5, 14], "insight": [5, 12]},
        "talisman": {"power": [4, 12], "defense": [2, 8], "fortune": [2, 6], "insight": [8, 16]},
    },
    "hp": {
        "weapon": {"hp": [100, 240], "defense": [3, 10], "fortune": [2, 6], "insight": [2, 6], "power": [0, 0]},
        "armor": {"hp": [80, 200], "defense": [5, 15], "fortune": [1, 5], "insight": [1, 5], "power": [0, 0]},
        "accessory": {"hp": [60, 160], "defense": [2, 8], "fortune": [4, 12], "insight": [3, 8], "power": [0, 0]},
        "talisman": {"hp": [70, 180], "defense": [3, 10], "fortune": [2, 8], "insight": [5, 12], "power": [0, 0]},
    },
}

SLOT_META: dict[str, dict[str, dict[str, str]]] = {
    "internal": {
        "weapon": {"name": "Inner Channel Blade", "item_id": "spirit_blade", "technique_tag": "sword"},
        "armor": {"name": "Qiwoven Vest", "item_id": "mistwoven_vest", "technique_tag": "body"},
        "accessory": {"name": "Meridian Talisman", "item_id": "bandit_talisman", "technique_tag": "fire"},
        "talisman": {"name": "Soulwell Pendant", "item_id": "moonwell_pendant", "technique_tag": "soul"},
    },
    "external": {
        "weapon": {"name": "Vanguard Blade", "item_id": "spirit_blade", "technique_tag": "sword"},
        "armor": {"name": "Ironheart Vest", "item_id": "mistwoven_vest", "technique_tag": "body"},
        "accessory": {"name": "Warband Talisman", "item_id": "bandit_talisman", "technique_tag": "fire"},
        "talisman": {"name": "Bulwark Pendant", "item_id": "moonwell_pendant", "technique_tag": "soul"},
    },
    "hp": {
        "weapon": {"name": "Lifebound Blade", "item_id": "spirit_blade", "technique_tag": "sword"},
        "armor": {"name": "Lifebound Vest", "item_id": "mistwoven_vest", "technique_tag": "body"},
        "accessory": {"name": "Lifebound Talisman", "item_id": "bandit_talisman", "technique_tag": "fire"},
        "talisman": {"name": "Lifebound Pendant", "item_id": "moonwell_pendant", "technique_tag": "soul"},
    },
}


def _load_realm_names() -> list[str]:
    with REALMS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return [str(r["name"]) for r in data["realms"]]


def _realm_multipliers() -> list[float]:
    """Log-linear multipliers anchored at external-armor defense midpoints for r0/r1/r3/r9."""
    r0_mid = sum(EXTERNAL_ARMOR_COMMON_ANCHORS[0]["defense"]) / 2
    r1_mid = sum(EXTERNAL_ARMOR_COMMON_ANCHORS[1]["defense"]) / 2
    r3_mid = sum(EXTERNAL_ARMOR_COMMON_ANCHORS[3]["defense"]) / 2
    # Extrapolate r9 from r0→r3 growth (×100 over three realm steps → ×100^(6/3) from r3).
    r9_mid = r3_mid * ((r3_mid / r0_mid) ** 2)
    anchor_realms = [0, 1, 3, 9]
    anchor_mids = [r0_mid, r1_mid, r3_mid, r9_mid]
    multipliers = [1.0] * 10
    for r, mid in zip(anchor_realms, anchor_mids):
        multipliers[r] = mid / anchor_mids[0]

    for left, right in zip(anchor_realms, anchor_realms[1:]):
        lo_mult, hi_mult = multipliers[left], multipliers[right]
        lo_log = math.log(lo_mult)
        hi_log = math.log(hi_mult)
        span = right - left
        for r in range(left + 1, right):
            t = (r - left) / span
            multipliers[r] = math.exp(lo_log + t * (hi_log - lo_log))

    return multipliers


def _scale_range(rng: list[int], mult: float) -> list[int]:
    low, high = int(rng[0]), int(rng[1])
    if high <= 0 and low <= 0:
        return [0, 0]
    scaled_low = max(0, int(round(low * mult)))
    scaled_high = max(scaled_low, int(round(high * mult)))
    if low > 0 and scaled_high == 0:
        scaled_high = max(1, int(round(mult)))
    return [scaled_low, scaled_high]


def _mortal_common_ranges(path: str, slot: str) -> dict[str, list[int]]:
    if path == "external" and slot == "armor":
        return dict(EXTERNAL_ARMOR_COMMON_ANCHORS[0])
    profile = MORTAL_PROFILES[path][slot]
    return {stat: [int(rng[0]), int(rng[1])] for stat, rng in profile.items()}  # type: ignore[index]


def _realm_common_ranges(path: str, slot: str, realm_index: int, mult: float) -> dict[str, list[int]]:
    if path == "external" and slot == "armor" and realm_index in EXTERNAL_ARMOR_COMMON_ANCHORS:
        return dict(EXTERNAL_ARMOR_COMMON_ANCHORS[realm_index])
    mortal = _mortal_common_ranges(path, slot)
    if realm_index == 0:
        return mortal
    return {stat: _scale_range(rng, mult) for stat, rng in mortal.items()}


def _apply_rarity(common: dict[str, list[int]], rarity: str) -> dict[str, list[int]]:
    if rarity == "common":
        return common
    result: dict[str, list[int]] = {}
    for stat, (c_lo, c_hi) in common.items():
        if c_hi <= 0 and c_lo <= 0:
            result[stat] = [0, 0]
            continue
        if rarity == "rare":
            r_lo = c_hi
            r_hi = max(r_lo + 1, int(round(c_hi * 1.45)))
        else:  # legendary
            rare_hi = max(c_hi + 1, int(round(c_hi * 1.45)))
            r_lo = rare_hi
            r_hi = max(r_lo + 1, int(round(rare_hi * 1.55)))
        result[stat] = [r_lo, r_hi]
    return result


def generate_catalog() -> list[dict]:
    realm_names = _load_realm_names()
    multipliers = _realm_multipliers()
    entries: list[dict] = []

    for realm_index in range(10):
        realm_name = realm_names[realm_index]
        mult = multipliers[realm_index]
        for path in PATHS:
            for slot in SLOTS:
                common_ranges = _realm_common_ranges(path, slot, realm_index, mult)
                meta = SLOT_META[path][slot]
                for rarity in RARITIES:
                    stat_ranges = _apply_rarity(common_ranges, rarity)
                    entry_id = f"r{realm_index}_{path}_{slot}_{rarity}"
                    display = f"{realm_name} {meta['name']}"
                    entries.append(
                        {
                            "id": entry_id,
                            "realm_index": realm_index,
                            "path": path,
                            "slot": slot,
                            "rarity": rarity,
                            "name": display,
                            "item_id": meta["item_id"],
                            "technique_tag": meta["technique_tag"],
                            "stat_ranges": stat_ranges,
                        }
                    )
    return entries


def validate_anchors(entries: list[dict]) -> None:
    by_id = {e["id"]: e for e in entries}
    for realm_index, expected in EXTERNAL_ARMOR_COMMON_ANCHORS.items():
        entry = by_id[f"r{realm_index}_external_armor_common"]
        assert entry["stat_ranges"] == expected, (
            f"Anchor mismatch r{realm_index} external armor common: "
            f"{entry['stat_ranges']} != {expected}"
        )

    keys = {(e["realm_index"], e["slot"], e["path"], e["rarity"]) for e in entries}
    assert len(keys) == 360, f"Expected 360 unique keys, got {len(keys)}"
    assert len(entries) == 360, f"Expected 360 entries, got {len(entries)}"

    for path in PATHS:
        for rarity in RARITIES:
            count = sum(1 for e in entries if e["path"] == path and e["rarity"] == rarity)
            assert count == 40, f"Expected 40 entries for {path}/{rarity}, got {count}"


def main() -> int:
    entries = generate_catalog()
    validate_anchors(entries)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")
    print(f"Wrote {len(entries)} entries to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
