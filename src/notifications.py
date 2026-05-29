from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .combat.loadout import get_learned_technique_ids, validate_pvp_loadout
from .combat.ranks import rank_cost
from .combat.catalog import get_technique, get_technique_by_manual
from .combat.rules import load_combat_rules
from .inventory import get_item_def, get_item_name, get_item_quantity, get_player_inventory
from .manuals import can_unseal_manual, is_sealed_manual, unseal_manual_item_id
from .models import Player, PlayerNotification, utcnow
from .player_guides import guide_text

NOTIFICATION_SEALED_MANUAL = "sealed_manual_unlock"


def _unlockable_sealed_manual_ids(session: Session, player: Player) -> list[str]:
    if not load_combat_rules().enabled("sealed_manuals"):
        return []
    learned = get_learned_technique_ids(session, player.id)
    ready: list[str] = []
    for stack in get_player_inventory(session, player.id):
        if stack.quantity <= 0 or not is_sealed_manual(stack.item_id):
            continue
        manual_id = unseal_manual_item_id(stack.item_id)
        tech = get_technique_by_manual(manual_id)
        if tech is not None and tech.technique_id in learned:
            continue
        ok, _ = can_unseal_manual(player, stack.item_id)
        if ok:
            ready.append(stack.item_id)
    return ready


def count_unlockable_sealed_manuals(session: Session, player: Player) -> int:
    return len(_unlockable_sealed_manual_ids(session, player))


def count_rankable_techniques(session: Session, player: Player) -> int:
    if not load_combat_rules().enabled("technique_ranks"):
        return 0
    from .combat.loadout import get_learned_technique_ids
    from .realms import get_technique_rank_cap
    from .combat.loadout import get_technique_rank

    cap = get_technique_rank_cap(player.realm_index)
    count = 0
    for technique_id in get_learned_technique_ids(session, player.id):
        tech = get_technique(technique_id)
        if tech is None:
            continue
        rank = get_technique_rank(session, player.id, technique_id)
        if rank >= cap or rank >= 10:
            continue
        cost = rank_cost(tech, rank)
        if player.spirit_stones < cost.stones:
            continue
        if all(
            get_item_quantity(session, player.id, item_id) >= qty
            for item_id, qty in cost.materials.items()
        ):
            count += 1
    return count


def duel_loadout_illegal(session: Session, player: Player) -> bool:
    if not load_combat_rules().enabled("pvp_legality_checks"):
        return False
    ok, _ = validate_pvp_loadout(session, player)
    return not ok


def format_profile_progression_flags(session: Session, player: Player) -> list[str]:
    flags: list[str] = []
    sealed = count_unlockable_sealed_manuals(session, player)
    if sealed:
        noun = "manual" if sealed == 1 else "manuals"
        flags.append(f"**{sealed}** sealed {noun} ready to study — **`/learn`**")
    rankable = count_rankable_techniques(session, player)
    if rankable:
        noun = "art" if rankable == 1 else "arts"
        flags.append(f"**{rankable}** {noun} can rank up — **`/upgrade-technique`**")
    if duel_loadout_illegal(session, player):
        flags.append(guide_text("pvp_legality", "profile_flag"))
    return flags


def _active_notification(
    session: Session, player_id: int, kind: str
) -> PlayerNotification | None:
    stmt = (
        select(PlayerNotification)
        .where(
            PlayerNotification.player_id == player_id,
            PlayerNotification.kind == kind,
            PlayerNotification.dismissed_at.is_(None),
        )
        .order_by(PlayerNotification.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def refresh_sealed_manual_notification(session: Session, player: Player) -> str | None:
    """Create or refresh sealed-manual unlock notice; return embed line if newly surfaced."""
    ready = _unlockable_sealed_manual_ids(session, player)
    if not ready:
        existing = _active_notification(session, player.id, NOTIFICATION_SEALED_MANUAL)
        if existing is not None:
            existing.dismissed_at = utcnow()
            session.add(existing)
        return None

    payload = {"manual_ids": ready, "count": len(ready)}
    existing = _active_notification(session, player.id, NOTIFICATION_SEALED_MANUAL)
    if existing is not None:
        existing.payload_json = json.dumps(payload)
        session.add(existing)
        return None

    session.add(
        PlayerNotification(
            player_id=player.id,
            kind=NOTIFICATION_SEALED_MANUAL,
            payload_json=json.dumps(payload),
        )
    )
    return guide_text("sealed_manual", "ready_line")


def dismiss_sealed_manual_notifications(session: Session, player_id: int) -> None:
    stmt = select(PlayerNotification).where(
        PlayerNotification.player_id == player_id,
        PlayerNotification.kind == NOTIFICATION_SEALED_MANUAL,
        PlayerNotification.dismissed_at.is_(None),
    )
    for row in session.execute(stmt).scalars():
        row.dismissed_at = utcnow()
        session.add(row)


def on_manual_learned(session: Session, player_id: int) -> None:
    dismiss_sealed_manual_notifications(session, player_id)

