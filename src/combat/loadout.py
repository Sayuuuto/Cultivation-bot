from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Player, PlayerTechnique, TechniqueLoadout
from .catalog import TechniqueDef, get_technique, load_technique_catalog

ACTIVE_SLOTS = ("1", "2", "3", "4")
PASSIVE_SLOT = "passive"
DEFAULT_STARTER_TECHNIQUES = ("basic_strike",)


def ensure_starter_techniques(session: Session, player_id: int) -> None:
    learned = get_learned_technique_ids(session, player_id)
    for technique_id in DEFAULT_STARTER_TECHNIQUES:
        if technique_id not in learned:
            learn_technique(session, player_id, technique_id)
    loadout = get_loadout(session, player_id)
    if not loadout.get("1"):
        player = session.get(Player, player_id)
        if player is not None:
            equip_technique(session, player, "basic_strike", "1")


def get_learned_technique_ids(session: Session, player_id: int) -> set[str]:
    stmt = select(PlayerTechnique.technique_id).where(PlayerTechnique.player_id == player_id)
    return set(session.execute(stmt).scalars().all())


def get_learned_techniques(session: Session, player_id: int) -> list[TechniqueDef]:
    ids = get_learned_technique_ids(session, player_id)
    catalog = load_technique_catalog()
    return [catalog[tid] for tid in sorted(ids) if tid in catalog]


def learn_technique(session: Session, player_id: int, technique_id: str) -> tuple[bool, str]:
    tech = get_technique(technique_id)
    if tech is None:
        return False, "That technique is unknown."
    if technique_id in get_learned_technique_ids(session, player_id):
        return False, f"You already know **{tech.name}**."
    session.add(PlayerTechnique(player_id=player_id, technique_id=technique_id))
    session.flush()
    from ..models import Player
    from ..novice_trial import on_technique_learned

    player = session.get(Player, player_id)
    extra = on_technique_learned(session, player, technique_id) if player is not None else []
    msg = f"You learned **{tech.name}**."
    if extra:
        msg += "\n" + "\n".join(extra)
    return True, msg


def get_loadout(session: Session, player_id: int) -> dict[str, str]:
    stmt = select(TechniqueLoadout).where(TechniqueLoadout.player_id == player_id)
    rows = session.execute(stmt).scalars().all()
    return {row.slot: row.technique_id for row in rows}


def get_equipped_active_techniques(session: Session, player_id: int) -> list[TechniqueDef]:
    loadout = get_loadout(session, player_id)
    catalog = load_technique_catalog()
    result: list[TechniqueDef] = []
    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        if technique_id and technique_id in catalog:
            tech = catalog[technique_id]
            if tech.slot_type == "active":
                result.append(tech)
    return result


def get_equipped_passive(session: Session, player_id: int) -> TechniqueDef | None:
    loadout = get_loadout(session, player_id)
    technique_id = loadout.get(PASSIVE_SLOT)
    if not technique_id:
        return None
    tech = get_technique(technique_id)
    if tech is None or tech.slot_type != "passive":
        return None
    return tech


def equip_technique(
    session: Session,
    player: Player,
    technique_id: str,
    slot: str,
) -> tuple[bool, str]:
    slot = slot.lower()
    if slot == "passive":
        slot = PASSIVE_SLOT
    if slot not in ACTIVE_SLOTS and slot != PASSIVE_SLOT:
        return False, "Slot must be 1–4 or passive."

    tech = get_technique(technique_id)
    if tech is None:
        return False, "That technique is unknown."
    if technique_id not in get_learned_technique_ids(session, player.id):
        return False, f"You have not learned **{tech.name}** yet."
    if player.realm_index < tech.min_realm:
        return False, f"**{tech.name}** requires a higher realm."
    if slot == PASSIVE_SLOT and tech.slot_type != "passive":
        return False, f"**{tech.name}** is an **active** art — equip it to slots **1–4**, not the passive slot."
    if slot in ACTIVE_SLOTS and tech.slot_type != "active":
        return False, f"**{tech.name}** is a **passive** art — equip it to the **passive** slot only."

    stmt = select(TechniqueLoadout).where(
        TechniqueLoadout.player_id == player.id,
        TechniqueLoadout.slot == slot,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        session.add(TechniqueLoadout(player_id=player.id, slot=slot, technique_id=technique_id))
    else:
        row.technique_id = technique_id
    if slot == PASSIVE_SLOT:
        return True, f"Equipped **{tech.name}** as your **passive** (always on in combat)."
    return True, f"Equipped **{tech.name}** in **active slot {slot}** (manual use in combat)."


def format_techniques_embed_text(session: Session, player: Player) -> str:
    ensure_starter_techniques(session, player.id)
    learned = get_learned_techniques(session, player.id)
    loadout = get_loadout(session, player.id)

    lines = ["**Learned techniques**"]
    if not learned:
        lines.append("_None yet — hunt beasts, adventure rare events, or visit the shop for manuals._")
    else:
        for tech in learned:
            slot = next((s for s, tid in loadout.items() if tid == tech.technique_id), None)
            equipped = f" _(slot {slot})_" if slot else ""
            lines.append(f"• **{tech.name}** [{tech.category}/{tech.tier}]{equipped}")

    lines.append("")
    lines.append("**Loadout**")
    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        name = get_technique(technique_id).name if technique_id else "—"
        lines.append(f"Slot {slot}: **{name}**")
    passive_id = loadout.get(PASSIVE_SLOT)
    passive_name = get_technique(passive_id).name if passive_id else "—"
    lines.append(f"Passive: **{passive_name}**")
    return "\n".join(lines)
