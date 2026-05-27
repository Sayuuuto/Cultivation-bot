from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PlayerEffect

HASTE_UNIVERSAL_EFFECT = "haste_universal"

# Legacy per-activity stacks (still honored for in-flight effects).
LEGACY_HASTE_BY_ACTIVITY = {
    "adventure": "haste_adventure",
    "cultivate": "haste_cultivate",
    "dungeon": "haste_dungeon",
    "duel": "haste_duel",
    "gather": "haste_gather",
    "hunt": "haste_hunt",
}

HASTE_ACTIVITIES = frozenset(set(LEGACY_HASTE_BY_ACTIVITY.keys()) | {"daily"})

LEGACY_HASTE_EFFECT_IDS = tuple(LEGACY_HASTE_BY_ACTIVITY.values())


def _effect_has_charge(eff: PlayerEffect | None) -> bool:
    if eff is None:
        return False
    if eff.charges is not None and eff.charges <= 0:
        return False
    return (eff.value_int or 0) > 0


def _pick_haste_effect(session: Session, player_id: int, activity: str) -> PlayerEffect | None:
    stmt = select(PlayerEffect).where(
        PlayerEffect.player_id == player_id,
        PlayerEffect.effect_id == HASTE_UNIVERSAL_EFFECT,
    )
    universal = session.execute(stmt).scalar_one_or_none()
    if _effect_has_charge(universal):
        return universal

    legacy_id = LEGACY_HASTE_BY_ACTIVITY.get(activity)
    if legacy_id is None:
        return None
    stmt = select(PlayerEffect).where(
        PlayerEffect.player_id == player_id,
        PlayerEffect.effect_id == legacy_id,
    )
    legacy = session.execute(stmt).scalar_one_or_none()
    if _effect_has_charge(legacy):
        return legacy
    return None


def get_haste_reduction_seconds(session: Session, player_id: int, activity: str) -> int:
    if activity not in HASTE_ACTIVITIES:
        return 0
    eff = _pick_haste_effect(session, player_id, activity)
    if eff is None:
        return 0
    return eff.value_int or 0


def consume_haste_for_activity(session: Session, player_id: int, activity: str) -> int:
    """Consume one haste charge when starting an activity; returns seconds shaved."""
    if activity not in HASTE_ACTIVITIES:
        return 0

    eff = _pick_haste_effect(session, player_id, activity)
    if eff is None:
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
