from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .drop_sources import format_missing_materials_message
from .equipment_tiers import (
    GEAR_GRADES,
    EquipmentTierEntry,
    grade_label,
    normalize_gear_path,
    resolve_equipment_tier,
)
from .gear_stash import create_gear_item
from .inventory import get_item_quantity, remove_item
from .models import EQUIPMENT_SLOTS, Player


@dataclass
class ForgeResult:
    success: bool
    message: str
    slot: str | None = None
    stats: dict[str, int] | None = None
    grade: str | None = None
    gear_item_id: int | None = None


def _roll_stat(rng: random.Random, stat_range: list[int]) -> int:
    low, high = stat_range[0], stat_range[1]
    if high <= low:
        return low
    return rng.randint(low, high)


def get_forge_recipes() -> dict[str, dict]:
    """Backward-compatible summary for mortal Vanguard path (recipes embed)."""
    recipes: dict[str, dict] = {}
    for slot in EQUIPMENT_SLOTS:
        entry = resolve_equipment_tier(0, slot, "external")
        if entry is None:
            continue
        recipes[slot] = {
            "name": entry.name,
            "item_id": entry.item_id,
            "inputs": entry.inputs,
            "stat_ranges": entry.stat_ranges,
            "technique_tag": entry.technique_tag,
        }
    return recipes


def get_forge_recipe(slot: str) -> dict | None:
    return get_forge_recipes().get(slot.lower())


def forge_equipment(
    session: Session,
    player_id: int,
    slot: str,
    *,
    realm_index: int = 0,
    grade: str = "external",
    rng: random.Random | None = None,
) -> ForgeResult:
    rng = rng or random.Random()
    slot = slot.lower()
    grade = normalize_gear_path(grade)
    if slot not in EQUIPMENT_SLOTS:
        return ForgeResult(False, f"Invalid slot. Choose: {', '.join(EQUIPMENT_SLOTS)}.")
    if grade not in GEAR_GRADES:
        return ForgeResult(
            False,
            f"Choose a forge path: {', '.join(grade_label(g) for g in GEAR_GRADES)}.",
        )

    entry = resolve_equipment_tier(realm_index, slot, grade)
    if entry is None:
        return ForgeResult(False, "No forge recipe exists for that slot.")

    inputs = entry.inputs
    short = any(get_item_quantity(session, player_id, item_id) < qty for item_id, qty in inputs.items())
    if short:
        return ForgeResult(
            False,
            format_missing_materials_message(session, player_id, inputs, action="forge"),
        )

    for item_id, qty in inputs.items():
        if not remove_item(session, player_id, item_id, qty):
            return ForgeResult(False, "Materials vanished mid-forge. Try again.")

    rolled = {
        stat: _roll_stat(rng, stat_range)
        for stat, stat_range in entry.stat_ranges.items()
    }

    gear_item = create_gear_item(
        session,
        player_id,
        entry,
        rolled,
        realm_index=realm_index,
        grade=grade,
    )

    stat_bits = [f"{k.title()} {v}" for k, v in rolled.items() if v > 0]
    stats_text = ", ".join(stat_bits) if stat_bits else "modest qi"
    tag = entry.technique_tag
    tag_text = f" · **{tag.title()}** lane" if tag else ""
    return ForgeResult(
        success=True,
        message=(
            f"You forge **{entry.name}**. Rolled stats: {stats_text}.{tag_text} "
            f"Stored in your gear stash (**#{gear_item.id}**) — **`/equip`** to wear it."
        ),
        slot=slot,
        stats=rolled,
        grade=grade,
        gear_item_id=gear_item.id,
    )


def forge_equipment_for_player(
    session: Session,
    player: Player,
    slot: str,
    *,
    grade: str = "external",
    rng: random.Random | None = None,
) -> ForgeResult:
    return forge_equipment(
        session,
        player.id,
        slot,
        realm_index=player.realm_index,
        grade=grade,
        rng=rng,
    )


def forge_and_equip(
    session: Session,
    player: Player,
    slot: str,
    *,
    grade: str = "external",
    rng: random.Random | None = None,
) -> ForgeResult:
    """Test/helper: forge then equip in one step."""
    from .gear_stash import equip_gear_item

    result = forge_equipment_for_player(session, player, slot, grade=grade, rng=rng)
    if not result.success or result.gear_item_id is None:
        return result
    equip_res = equip_gear_item(session, player.id, result.gear_item_id)
    if not equip_res.success:
        return ForgeResult(False, equip_res.message)
    return result
