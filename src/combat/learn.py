from __future__ import annotations

from sqlalchemy.orm import Session

from ..command_choices import resolve_manual_item_id
from ..inventory import get_item_def, get_item_name, get_item_quantity, remove_item
from ..manuals import can_unseal_manual, is_sealed_manual, unseal_manual_item_id
from ..models import Player
from .catalog import get_technique_by_manual
from .loadout import learn_technique
from ..technique_info import format_art_type_label


def learn_technique_from_manual(session: Session, player_id: int, manual_item_id: str) -> tuple[bool, str]:
    resolved = resolve_manual_item_id(manual_item_id)
    if resolved is None:
        return (
            False,
            "Pick a manual from **`/techniques`** → **Unlock Skill**.",
        )
    manual_item_id = resolved

    item_def = get_item_def(manual_item_id)
    if item_def is None:
        return False, "That item is unknown."
    if item_def.category != "manual":
        return False, "That item is not a technique manual."

    sealed = is_sealed_manual(manual_item_id)
    player = session.get(Player, player_id)
    if sealed:
        if player is None:
            return False, "Your cultivation record could not be found."
        ok, reason = can_unseal_manual(player, manual_item_id)
        if not ok:
            return False, reason
    teach_item_id = unseal_manual_item_id(manual_item_id) if sealed else manual_item_id

    tech = get_technique_by_manual(teach_item_id)
    if tech is None:
        return False, "This manual teaches no known technique."

    if get_item_quantity(session, player_id, manual_item_id) < 1:
        return False, f"You do not have **{get_item_name(manual_item_id)}**."

    ok, msg = learn_technique(session, player_id, tech.technique_id)
    if not ok:
        return ok, msg

    remove_item(session, player_id, manual_item_id, 1)
    from ..notifications import on_manual_learned

    on_manual_learned(session, player_id)
    equip_hint = (
        "Open **`/techniques`** → **Equip Skill** → **passive slot**."
        if tech.slot_type == "passive"
        else "Open **`/techniques`** → **Equip Skill** → **slots 1–4**."
    )
    return (
        True,
        f"You studied **{get_item_name(manual_item_id)}** and learned **{tech.name}**.\n"
        f"{format_art_type_label(tech)}\n{equip_hint}",
    )
