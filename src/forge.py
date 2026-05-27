from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .drop_sources import format_missing_materials_message
from .equipment import get_or_create_slot
from .inventory import get_item_name, get_item_quantity, remove_item
from .models import EQUIPMENT_SLOTS

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "equipment_forge.json"

_forge_recipes: dict[str, dict] | None = None


def get_forge_recipes() -> dict[str, dict]:
    global _forge_recipes
    if _forge_recipes is None:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            _forge_recipes = json.load(f)
    return _forge_recipes


def get_forge_recipe(slot: str) -> dict | None:
    return get_forge_recipes().get(slot.lower())


@dataclass
class ForgeResult:
    success: bool
    message: str
    slot: str | None = None
    stats: dict[str, int] | None = None


def _roll_stat(rng: random.Random, stat_range: list[int]) -> int:
    low, high = stat_range[0], stat_range[1]
    return rng.randint(low, high)


def forge_equipment(
    session: Session,
    player_id: int,
    slot: str,
    rng: random.Random | None = None,
) -> ForgeResult:
    rng = rng or random.Random()
    slot = slot.lower()
    if slot not in EQUIPMENT_SLOTS:
        return ForgeResult(False, f"Invalid slot. Choose: {', '.join(EQUIPMENT_SLOTS)}.")

    recipe = get_forge_recipe(slot)
    if recipe is None:
        return ForgeResult(False, "No forge recipe exists for that slot.")

    inputs: dict[str, int] = recipe["inputs"]
    short = any(get_item_quantity(session, player_id, item_id) < qty for item_id, qty in inputs.items())
    if short:
        return ForgeResult(
            False,
            format_missing_materials_message(session, player_id, inputs, action="forge"),
        )

    for item_id, qty in inputs.items():
        if not remove_item(session, player_id, item_id, qty):
            return ForgeResult(False, "Materials vanished mid-forge. Try again.")

    stat_ranges: dict[str, list[int]] = recipe["stat_ranges"]
    rolled = {
        stat: _roll_stat(rng, stat_range)
        for stat, stat_range in stat_ranges.items()
    }

    row = get_or_create_slot(session, player_id, slot)
    row.item_id = recipe["item_id"]
    row.stat_power = rolled.get("power", 0)
    row.stat_defense = rolled.get("defense", 0)
    row.stat_fortune = rolled.get("fortune", 0)
    row.stat_insight = rolled.get("insight", 0)
    row.technique_tag = recipe.get("technique_tag")
    session.add(row)

    stat_bits = [f"{k.title()} {v}" for k, v in rolled.items() if v > 0]
    stats_text = ", ".join(stat_bits) if stat_bits else "modest qi"
    tag = recipe.get("technique_tag")
    tag_text = f" · **{tag.title()}** lane" if tag else ""
    return ForgeResult(
        success=True,
        message=(
            f"You forge **{recipe['name']}** for your {slot}. "
            f"Rolled stats: {stats_text}.{tag_text}"
        ),
        slot=slot,
        stats=rolled,
    )
