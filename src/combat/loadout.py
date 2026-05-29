from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Player, PlayerTechnique, TechniqueLoadout
from ..player_guides import guide_text
from ..realms import get_technique_load_budget
from .catalog import TechniqueDef, get_technique, load_technique_catalog
from .rules import load_combat_rules

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


def get_technique_rank(session: Session, player_id: int, technique_id: str) -> int:
    stmt = select(PlayerTechnique).where(
        PlayerTechnique.player_id == player_id,
        PlayerTechnique.technique_id == technique_id,
    )
    row = session.execute(stmt).scalar_one_or_none()
    return 1 if row is None else max(1, int(getattr(row, "rank", 1) or 1))


def learn_technique(session: Session, player_id: int, technique_id: str) -> tuple[bool, str]:
    tech = get_technique(technique_id)
    if tech is None:
        return False, "That technique is unknown."
    if technique_id in get_learned_technique_ids(session, player_id):
        return False, f"You already know **{tech.name}**."
    session.add(PlayerTechnique(player_id=player_id, technique_id=technique_id, rank=1))
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


def _load_totals(catalog: dict[str, TechniqueDef], loadout: dict[str, str]) -> dict[str, int]:
    totals = {"active": 0, "passive": 0, "total": 0}
    seen: set[str] = set()
    for slot, technique_id in loadout.items():
        if technique_id in seen:
            continue
        tech = catalog.get(technique_id)
        if tech is None:
            continue
        seen.add(technique_id)
        bucket = "passive" if slot == PASSIVE_SLOT or tech.slot_type == "passive" else "active"
        totals[bucket] += tech.load
        totals["total"] += tech.load
    return totals


def validate_loadout_budget(
    session: Session,
    player: Player,
    *,
    proposed_slot: str | None = None,
    proposed_technique_id: str | None = None,
) -> tuple[bool, str]:
    if not load_combat_rules().enabled("technique_load_budget"):
        return True, ""
    loadout = get_loadout(session, player.id)
    if proposed_slot and proposed_technique_id:
        loadout[proposed_slot] = proposed_technique_id
    catalog = load_technique_catalog()
    totals = _load_totals(catalog, loadout)
    budget = get_technique_load_budget(player.realm_index)
    over = [key for key in ("active", "passive", "total") if totals[key] > budget[key]]
    if not over:
        return True, ""
    details = ", ".join(f"{key} {totals[key]}/{budget[key]}" for key in over)
    hint = guide_text("load_budget", "equip_fail")
    return False, f"{hint} (**{details}**)."


def _technique_has_role(tech: TechniqueDef, role: str) -> bool:
    if role == "control":
        return tech.status_id in {"stun", "fear", "seal"} or any(
            effect.type == "apply_status" and effect.params.get("status") in {"stun", "fear", "seal"}
            for effect in tech.effects
        )
    if role == "shield":
        return any(effect.type == "shield" for effect in tech.effects)
    if role == "heal":
        return tech.heal_ratio > 0 or any(effect.type in {"heal", "lifesteal", "cleanse"} for effect in tech.effects)
    if role == "survival_passive":
        return tech.slot_type == "passive" and any(
            trig.type in {"fatal_survival", "hp_threshold_heal"} for trig in tech.passive_triggers
        )
    return False


def list_pvp_loadout_violations(session: Session, player: Player) -> list[str]:
    rules = load_combat_rules()
    if not rules.enabled("pvp_legality_checks"):
        return []
    violations: list[str] = []
    ok, msg = validate_loadout_budget(session, player)
    if not ok:
        violations.append(msg)
    catalog = load_technique_catalog()
    loadout = get_loadout(session, player.id)
    equipped = [catalog[tid] for tid in set(loadout.values()) if tid in catalog]
    limits = rules.pvp_legality
    checks = {
        "legendary": (sum(1 for tech in equipped if tech.rarity == "legendary"), limits.max_legendary),
        "control": (sum(1 for tech in equipped if _technique_has_role(tech, "control")), limits.max_control),
        "shield": (sum(1 for tech in equipped if _technique_has_role(tech, "shield")), limits.max_shield),
        "healing": (sum(1 for tech in equipped if _technique_has_role(tech, "heal")), limits.max_heal),
        "survival_passive": (
            sum(1 for tech in equipped if _technique_has_role(tech, "survival_passive")),
            limits.max_survival_passive,
        ),
    }
    role_map = {
        "legendary": lambda t: t.rarity == "legendary",
        "control": lambda t: _technique_has_role(t, "control"),
        "shield": lambda t: _technique_has_role(t, "shield"),
        "healing": lambda t: _technique_has_role(t, "heal"),
        "survival_passive": lambda t: _technique_has_role(t, "survival_passive"),
    }
    for code, (count, limit) in checks.items():
        if count <= limit:
            continue
        offenders = [tech.name for tech in equipped if role_map[code](tech)]
        if offenders:
            violations.append(f"**{code}** {count}/{limit} — {', '.join(offenders)}")
        else:
            violations.append(f"**{code}** {count}/{limit}")
    return violations


def validate_pvp_loadout(session: Session, player: Player) -> tuple[bool, str]:
    violations = list_pvp_loadout_violations(session, player)
    if not violations:
        return True, ""
    if len(violations) == 1:
        return False, violations[0]
    return False, "Your duel loadout cannot enter the arena:\n• " + "\n• ".join(violations)


def get_equipped_active_techniques(session: Session, player_id: int) -> list[TechniqueDef]:
    """Active arts in slot order (1→4). Each technique appears at most once."""
    loadout = get_loadout(session, player_id)
    catalog = load_technique_catalog()
    result: list[TechniqueDef] = []
    seen: set[str] = set()
    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        if not technique_id or technique_id in seen:
            continue
        tech = catalog.get(technique_id)
        if tech is None or tech.slot_type != "active":
            continue
        seen.add(technique_id)
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
    ok, msg = validate_loadout_budget(
        session,
        player,
        proposed_slot=slot,
        proposed_technique_id=technique_id,
    )
    if not ok:
        return False, msg

    stmt = select(TechniqueLoadout).where(
        TechniqueLoadout.player_id == player.id,
        TechniqueLoadout.slot == slot,
    )
    row = session.execute(stmt).scalar_one_or_none()
    replaced_id = row.technique_id if row is not None else None
    if row is None:
        session.add(TechniqueLoadout(player_id=player.id, slot=slot, technique_id=technique_id))
    else:
        row.technique_id = technique_id
    session.flush()

    swap_note = ""
    if replaced_id and replaced_id != technique_id:
        old = get_technique(replaced_id)
        if old is not None:
            swap_note = f" **{old.name}** is unequipped but still in **My Skills**."

    if slot == PASSIVE_SLOT:
        return True, f"Equipped **{tech.name}** as your **passive**.{swap_note}"
    return True, f"Equipped **{tech.name}** in **slot {slot}**.{swap_note}"


def unequip_slot(session: Session, player_id: int, slot: str) -> tuple[bool, str]:
    slot = slot.lower()
    if slot == "passive":
        slot = PASSIVE_SLOT
    if slot not in ACTIVE_SLOTS and slot != PASSIVE_SLOT:
        return False, "Slot must be 1–4 or passive."

    stmt = select(TechniqueLoadout).where(
        TechniqueLoadout.player_id == player_id,
        TechniqueLoadout.slot == slot,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        label = "passive slot" if slot == PASSIVE_SLOT else f"slot {slot}"
        return False, f"**{label.title()}** is already empty."

    tech = get_technique(row.technique_id)
    name = tech.name if tech is not None else row.technique_id
    session.delete(row)
    session.flush()
    label = "passive slot" if slot == PASSIVE_SLOT else f"slot {slot}"
    return True, f"Cleared **{label}** — **{name}** is unequipped but still in your library."


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
            rank = get_technique_rank(session, player.id, tech.technique_id)
            lines.append(f"• **{tech.name}** [{tech.category}/{tech.tier}] load {tech.load} · rank {rank}{equipped}")

    lines.append("")
    lines.append("**Loadout**")
    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        name = get_technique(technique_id).name if technique_id else "—"
        lines.append(f"Slot {slot}: **{name}**")
    passive_id = loadout.get(PASSIVE_SLOT)
    passive_name = get_technique(passive_id).name if passive_id else "—"
    lines.append(f"Passive: **{passive_name}**")
    budget = get_technique_load_budget(player.realm_index)
    totals = _load_totals(load_technique_catalog(), loadout)
    lines.append(f"Load: active **{totals['active']}/{budget['active']}** · passive **{totals['passive']}/{budget['passive']}** · total **{totals['total']}/{budget['total']}**")
    return "\n".join(lines)
