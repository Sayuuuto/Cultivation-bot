from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PlayerEffect

HASTE_BY_ACTIVITY = {
    "adventure": "haste_adventure",
    "cultivate": "haste_cultivate",
    "dungeon": "haste_dungeon",
    "duel": "haste_duel",
    "gather": "haste_gather",
    "hunt": "haste_hunt",
}

HASTE_EFFECT_IDS = tuple(HASTE_BY_ACTIVITY.values())


def get_haste_reduction_seconds(session: Session, player_id: int, activity: str) -> int:
    effect_id = HASTE_BY_ACTIVITY.get(activity)
    if effect_id is None:
        return 0

    stmt = select(PlayerEffect).where(
        PlayerEffect.player_id == player_id,
        PlayerEffect.effect_id == effect_id,
    )
    eff = session.execute(stmt).scalar_one_or_none()
    if eff is None:
        return 0
    if eff.charges is not None and eff.charges <= 0:
        return 0

    per_charge = eff.value_int or 0
    if per_charge <= 0:
        return 0
    return per_charge


def consume_haste_for_activity(session: Session, player_id: int, activity: str) -> int:
    """Consume one haste charge when starting an activity; returns seconds shaved."""
    effect_id = HASTE_BY_ACTIVITY.get(activity)
    if effect_id is None:
        return 0

    stmt = select(PlayerEffect).where(
        PlayerEffect.player_id == player_id,
        PlayerEffect.effect_id == effect_id,
    )
    eff = session.execute(stmt).scalar_one_or_none()
    if eff is None:
        return 0
    if eff.charges is not None and eff.charges <= 0:
        return 0

    seconds = eff.value_int or 0
    if eff.charges is not None:
        eff.charges -= 1
        if eff.charges <= 0:
            session.delete(eff)
        else:
            session.add(eff)
    return seconds


def cooldown_remaining_with_haste(
    base_remaining: int,
    haste_seconds: int,
) -> int:
    if base_remaining <= 0 or haste_seconds <= 0:
        return base_remaining
    return max(0, base_remaining - haste_seconds)
