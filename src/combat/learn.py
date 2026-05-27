from __future__ import annotations

from sqlalchemy.orm import Session

from ..command_choices import resolve_manual_item_id
from ..inventory import get_item_def, get_item_name, get_item_quantity, remove_item
from .catalog import get_technique_by_manual
from .loadout import learn_technique
from ..technique_info import format_art_type_label


def learn_technique_from_manual(session: Session, player_id: int, manual_item_id: str) -> tuple[bool, str]:
    resolved = resolve_manual_item_id(manual_item_id)
    if resolved is None:
        return (
            False,
            "Pick a manual from the **`/learn`** list, or type its name "
            "(e.g. `Manual: Swift Slash`).",
        )
    manual_item_id = resolved

    item_def = get_item_def(manual_item_id)
    if item_def is None:
        return False, "That item is unknown."
    if item_def.category != "manual":
        return False, "That item is not a technique manual."

    tech = get_technique_by_manual(manual_item_id)
    if tech is None:
        return False, "This manual teaches no known technique."

    if get_item_quantity(session, player_id, manual_item_id) < 1:
        return False, f"You do not have **{get_item_name(manual_item_id)}**."

    ok, msg = learn_technique(session, player_id, tech.technique_id)
    if not ok:
        return ok, msg

    remove_item(session, player_id, manual_item_id, 1)
    equip_hint = (
        "Equip to the **passive slot** with **`/equip-technique`**."
        if tech.slot_type == "passive"
        else "Equip to **active slots 1–4** with **`/equip-technique`**."
    )
    return (
        True,
        f"You studied **{get_item_name(manual_item_id)}** and learned **{tech.name}**.\n"
        f"{format_art_type_label(tech)}\n{equip_hint}",
    )
