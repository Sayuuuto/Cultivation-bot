from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActiveCombat, Player
from .engine import CombatState, TurnResult, attempt_flee, auto_finish_combat, execute_turn
from .loadout import ensure_starter_techniques, get_equipped_passive


COMBAT_EXPIRY_MINUTES = 30

COMBAT_BUSY_MESSAGE = "You are already in combat. Win the fight or flee first."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_active_combat(session: Session, player_id: int) -> ActiveCombat | None:
    stmt = select(ActiveCombat).where(ActiveCombat.player_id == player_id)
    return session.execute(stmt).scalar_one_or_none()


def load_combat_state(active: ActiveCombat) -> CombatState:
    return CombatState.from_dict(json.loads(active.state_json))


def save_combat_state(active: ActiveCombat, state: CombatState) -> None:
    active.state_json = json.dumps(state.to_dict())
    active.updated_at = _utcnow()


def create_active_combat(
    session: Session,
    player: Player,
    state: CombatState,
    *,
    context: str,
    context_key: str,
) -> ActiveCombat:
    existing = get_active_combat(session, player.id)
    if existing is not None:
        session.delete(existing)
        session.flush()
    active = ActiveCombat(
        player_id=player.id,
        context=context,
        context_key=context_key,
        state_json=json.dumps(state.to_dict()),
        expires_at=_utcnow() + timedelta(minutes=COMBAT_EXPIRY_MINUTES),
    )
    session.add(active)
    session.flush()
    return active


def delete_active_combat(session: Session, player_id: int) -> None:
    active = get_active_combat(session, player_id)
    if active is not None:
        session.delete(active)


def abandon_active_combat(session: Session, player_id: int) -> tuple[bool, str]:
    """Drop a persisted combat row (e.g. lost fight message). Returns (cleared, player message)."""
    active = get_active_combat(session, player_id)
    if active is None:
        return False, "No combat is bound to you — you are free to hunt or adventure."
    context_label = {"hunt": "hunt", "adventure": "adventure"}.get(active.context, active.context)
    delete_active_combat(session, player_id)
    return True, (
        f"Cleared your unfinished **{context_label}** fight. "
        "Run **`/hunt`** or **`/adventure`** when ready."
    )


def process_combat_action(
    session: Session,
    player: Player,
    combat_id: int,
    action: str,
    *,
    technique_id: str | None = None,
    stats,
    mod,
    rng,
) -> tuple[TurnResult | None, str | None]:
    active = session.get(ActiveCombat, combat_id)
    if active is None or active.player_id != player.id:
        return None, "That combat session is no longer active."
    if _as_utc(active.expires_at) < _utcnow():
        session.delete(active)
        return None, "Combat session expired."

    ensure_starter_techniques(session, player.id)
    passive = get_equipped_passive(session, player.id)
    state = load_combat_state(active)

    if action == "flee":
        result = attempt_flee(state, stats, rng)
    elif action == "finish":
        result = auto_finish_combat(state, stats, mod, rng)
    else:
        result = execute_turn(
            state,
            stats,
            mod,
            passive,
            action,
            technique_id=technique_id,
            rng=rng,
        )
        if result.error:
            return result, result.error

    save_combat_state(active, state)
    if state.finished:
        session.delete(active)
    return result, None
