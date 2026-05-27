from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .models import Player

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "realms.json"


@lru_cache(maxsize=1)
def _load_realms_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def invalidate_realms_cache() -> None:
    _load_realms_config.cache_clear()


def realm_count() -> int:
    return len(_load_realms_config()["realms"])


def get_realm_names() -> list[str]:
    return [entry["name"] for entry in _load_realms_config()["realms"]]


def get_substage_names() -> list[str]:
    return list(_load_realms_config()["substages"])


def get_base_qi_caps() -> list[int]:
    return [int(entry["base_qi_cap"]) for entry in _load_realms_config()["realms"]]


def get_substage_multipliers() -> list[float]:
    return [float(v) for v in _load_realms_config()["substage_multipliers"]]


def get_breakthrough_config() -> dict:
    return dict(_load_realms_config().get("breakthrough", {}))


def realm_breakthrough_base_success(realm_index: int, substage: int) -> float:
    """Base breakthrough odds before karma, pills, and gear — high in Mortal, lower in late realms."""
    cfg = get_breakthrough_config()
    start = float(cfg.get("start_success", 0.90))
    per_realm = float(cfg.get("penalty_per_realm", 0.055))
    per_sub = float(cfg.get("penalty_per_substage", 0.008))
    minimum = float(cfg.get("min_base_success", 0.30))
    realm_index = max(0, min(realm_index, len(REALMS) - 1))
    substage = max(0, min(substage, len(SUBSTAGES) - 1))
    penalty = realm_index * per_realm + substage * per_sub
    return max(minimum, start - penalty)


def breakthrough_start_success() -> float:
    return float(get_breakthrough_config().get("start_success", 0.90))


# Backward-compatible module-level constants (loaded once at import).
REALMS = get_realm_names()
SUBSTAGES = get_substage_names()
REALM_BASE_QI_CAP = get_base_qi_caps()
SUBSTAGE_MULTIPLIER = get_substage_multipliers()


def get_realm_name(realm_index: int) -> str:
    idx = max(0, min(realm_index, len(REALMS) - 1))
    return REALMS[idx]


def realm_index_range(realm_index: int) -> bool:
    return 0 <= realm_index < len(REALMS)


def substage_range(substage: int) -> bool:
    return 0 <= substage < len(SUBSTAGES)


def qi_cap(realm_index: int, substage: int, player: Player | None = None) -> int:
    realm_index = max(0, min(realm_index, len(REALMS) - 1))
    substage = max(0, min(substage, len(SUBSTAGES) - 1))
    cap = int(REALM_BASE_QI_CAP[realm_index] * SUBSTAGE_MULTIPLIER[substage])
    if player is not None:
        from .novice_trial import NOVICE_MORTAL_EARLY_CAP, novice_breakthrough_pace

        if novice_breakthrough_pace(player):
            cap = min(cap, NOVICE_MORTAL_EARLY_CAP)
    return cap
