from __future__ import annotations



import random



from sqlalchemy import select

from sqlalchemy.orm import Session



from .content import get_affix, get_all_affixes

from .gear_stash import get_gear_item, resolve_equipped_gear

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

        view = resolve_equipped_gear(session, eq)

        if view is None or not view.affix_id:

            continue

        affix = get_affix(view.affix_id)

        if affix is None:

            continue

        for key, val in affix.values.items():

            mapped = AFFIX_FIELD_MAP.get(key, key)

            if mapped in MULT_FIELDS or mapped.endswith("_mult"):

                totals[mapped] = totals.get(mapped, 1.0) * val

            else:

                totals[mapped] = totals.get(mapped, 0.0) + val

    return totals





def apply_affix_stone(

    session: Session,

    player_id: int,

    gear_item_id: int,

    rng: random.Random | None = None,

) -> tuple[bool, str, str | None]:

    if get_item_quantity(session, player_id, "affix_stone") < 1:

        return False, "You need an Affix Stone.", None



    item = get_gear_item(session, player_id, gear_item_id)

    if item is None:

        return False, "Pick forged gear from your stash or worn slots.", None



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



    item.affix_id = affix_id

    session.add(item)



    if item.equipped_in_slot:

        slot_row = get_or_create_slot(session, player_id, item.equipped_in_slot)

        slot_row.affix_id = affix_id

        session.add(slot_row)



    gear_name = get_item_name(item.item_id)

    location = item.equipped_in_slot or "stash"

    return True, f"Your **{gear_name}** (#{item.id}, {location}) now bears **{affix.name}** ({affix.description}).", affix_id





def format_loadout(session: Session, player_id: int, *, player_realm_index: int | None = None) -> str:

    if player_realm_index is None:

        from .models import Player



        player = session.get(Player, player_id)

        player_realm_index = player.realm_index if player is not None else 0

    rows = {eq.slot: eq for eq in get_player_equipment(session, player_id)}

    lines: list[str] = []

    for slot in EQUIPMENT_SLOTS:

        eq = rows.get(slot)

        if eq is None or resolve_equipped_gear(session, eq) is None:

            lines.append(f"**{slot.title()}** — empty · forge with **`/forge`**, then **`/equip`**")

            continue

        lines.append(format_equipment_slot_line(session, eq, player_realm_index=player_realm_index))

        view = resolve_equipped_gear(session, eq)

        if view and view.affix_id:

            affix = get_affix(view.affix_id)

            if affix is not None:

                lines[-1] += f"\n↳ {affix.name}: {affix.description}"

    return "\n".join(lines)


