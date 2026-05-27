from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from .content import get_affix, get_all_affixes
from .inventory import get_item_name, get_item_quantity, remove_item
from .models import EQUIPMENT_SLOTS, PlayerEquipment
from .stats import format_equipment_slot_line

AFFIX_FIELD_MAP = {
    "power": "pvp_power",
    "defense": "adventure_defense",
}

MULT_FIELDS = {
    "rare_event_mult",
    "breakthrough_setback_mult",
    "cultivate_qi_mult",
    "offline_cap_mult",
    "pvp_stones_mult",
    "qi_gathering_mult",
}


def get_player_equipment(session: Session, player_id: int) -> list[PlayerEquipment]:
    stmt = select(PlayerEquipment).where(PlayerEquipment.player_id == player_id)
    return list(session.execute(stmt).scalars().all())


def get_or_create_slot(session: Session, player_id: int, slot: str) -> PlayerEquipment:
    stmt = select(PlayerEquipment).where(
        PlayerEquipment.player_id == player_id,
        PlayerEquipment.slot == slot,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        row = PlayerEquipment(player_id=player_id, slot=slot, affix_id=None)
        session.add(row)
        session.flush()
    return row


def get_player_affix_modifiers(session: Session, player_id: int) -> dict[str, float]:
    totals: dict[str, float] = {}
    for eq in get_player_equipment(session, player_id):
        if not eq.affix_id:
            continue
        affix = get_affix(eq.affix_id)
        if affix is None:
            continue
        for key, val in affix.values.items():
            mapped = AFFIX_FIELD_MAP.get(key, key)
            if mapped in MULT_FIELDS or mapped.endswith("_mult"):
                totals[mapped] = totals.get(mapped, 1.0) * val
            else:
                totals[mapped] = totals.get(mapped, 0.0) + val
    return totals


def apply_affix_stone(session: Session, player_id: int, slot: str, rng: random.Random | None = None) -> tuple[bool, str, str | None]:
    slot = slot.lower()
    if slot not in EQUIPMENT_SLOTS:
        return False, f"Invalid slot. Choose: {', '.join(EQUIPMENT_SLOTS)}.", None

    if get_item_quantity(session, player_id, "affix_stone") < 1:
        return False, "You need an Affix Stone.", None

    row = get_or_create_slot(session, player_id, slot)
    if not row.item_id:
        return False, f"Forge gear for your **{slot}** first with `/forge`, then apply an Affix Stone.", None

    affixes = list(get_all_affixes().keys())
    if not affixes:
        return False, "No affixes are configured.", None

    rng = rng or random.Random()
    affix_id = rng.choice(affixes)
    affix = get_affix(affix_id)
    if affix is None:
        return False, "Affix roll failed.", None

    if not remove_item(session, player_id, "affix_stone", 1):
        return False, "You need an Affix Stone.", None

    row.affix_id = affix_id
    session.add(row)

    gear_name = get_item_name(row.item_id)
    return True, f"Your **{gear_name}** ({slot}) now bears **{affix.name}** ({affix.description}).", affix_id


def format_loadout(session: Session, player_id: int) -> str:
    rows = {eq.slot: eq for eq in get_player_equipment(session, player_id)}
    lines: list[str] = []
    for slot in EQUIPMENT_SLOTS:
        eq = rows.get(slot)
        if eq is None:
            lines.append(f"**{slot.title()}** — empty · forge with `/forge`")
            continue
        lines.append(format_equipment_slot_line(eq))
        if eq.affix_id:
            affix = get_affix(eq.affix_id)
            if affix is not None:
                lines[-1] += f"\n↳ {affix.name}: {affix.description}"
    return "\n".join(lines)
