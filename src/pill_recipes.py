from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .content import RecipeDef

PILL_TIERS_PATH = Path(__file__).resolve().parent.parent / "config" / "pill_input_tiers.json"
PILL_EFFECTS_PATH = Path(__file__).resolve().parent.parent / "config" / "pill_effects.json"


@lru_cache(maxsize=1)
def _load_pill_input_tiers() -> dict[str, list[dict]]:
    if not PILL_TIERS_PATH.exists():
        return {}
    with PILL_TIERS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_pill_effects_config() -> dict[str, dict]:
    if not PILL_EFFECTS_PATH.exists():
        return {}
    with PILL_EFFECTS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def invalidate_pill_recipe_cache() -> None:
    _load_pill_input_tiers.cache_clear()
    _load_pill_effects_config.cache_clear()


def resolve_recipe_inputs(recipe: RecipeDef, realm_index: int) -> dict[str, int]:
    tiers = _load_pill_input_tiers().get(recipe.recipe_id)
    if not tiers:
        return dict(recipe.inputs)
    chosen = tiers[0]
    for tier in tiers:
        if int(tier.get("min_realm", 0)) <= realm_index:
            chosen = tier
    return dict(chosen.get("inputs", recipe.inputs))


def pill_effect_description(item_id: str, *, effect_id: str | None = None) -> str:
    cfg = _load_pill_effects_config()
    if item_id in cfg:
        return str(cfg[item_id].get("description", ""))
    if effect_id and effect_id in cfg:
        return str(cfg[effect_id].get("description", ""))
    return ""


def recipe_available_for_realm(recipe: RecipeDef, realm_index: int) -> bool:
    return realm_index >= recipe.min_realm
