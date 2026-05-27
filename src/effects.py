from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .modifiers import CharacterModifiers
from .game import to_utc
from .models import PlayerEffect


EFFECT_DESCRIPTIONS: dict[str, str] = {
    "qi_gathering": "+30% qi from cultivation (3 sessions)",
    "tempering": "+12% adventure defense (1 run)",
    "clarity": "+5% breakthrough stability (1 attempt)",
    "swiftwind": "+10% adventure success (1 run)",
    "blood_ember": "+15% dungeon damage (1 run)",
    "moonwell_tonic": "+35% rare event chance (1 run)",
    "shrine_boon": "+10% adventure success and PvP power (2 runs)",
    "shrine_curse": "-8% adventure success (2 runs)",
    "haste_adventure": "Shaves minutes off adventure cooldown",
    "haste_cultivate": "Shaves minutes off cultivate cooldown",
    "haste_dungeon": "Shaves minutes off dungeon cooldown",
    "haste_gather": "Shaves minutes off gather cooldown",
    "haste_hunt": "Shaves minutes off hunt cooldown",
}

HASTE_EFFECTS: dict[str, dict[str, int | str]] = {
    "flow_pill": {
        "effect_id": "haste_adventure",
        "default_charges": 1,
        "seconds_per_charge": 600,
        "label": "Flow Meridian",
    },
    "meridian_surge_pill": {
        "effect_id": "haste_cultivate",
        "default_charges": 2,
        "seconds_per_charge": 420,
        "label": "Meridian Surge",
    },
    "gatebreaker_dust": {
        "effect_id": "haste_dungeon",
        "default_charges": 1,
        "seconds_per_charge": 1800,
        "label": "Gatebreaker Dust",
    },
    "void_pulse_pill": {
        "effect_id": "void_pulse",
        "default_charges": 1,
        "seconds_per_charge": 0,
        "label": "Void Pulse",
    },
}


def apply_effects_from_db(session: Session, mod: CharacterModifiers, player_id: int) -> None:
    now = datetime.now(timezone.utc)
    stmt = select(PlayerEffect).where(PlayerEffect.player_id == player_id)
    effects = list(session.execute(stmt).scalars().all())
    for eff in effects:
        if eff.expires_at is not None:
            expires = to_utc(eff.expires_at)
            if expires < now:
                continue
        if eff.charges is not None and eff.charges <= 0:
            continue
        if eff.effect_id.startswith("haste_"):
            mod.active_effects.append(eff.effect_id)
            continue
        _apply_effect(mod, eff.effect_id)
        mod.active_effects.append(eff.effect_id)


def _apply_effect(mod: CharacterModifiers, effect_id: str) -> None:
    mapping = {
        "qi_gathering": lambda m: setattr(m, "qi_gathering_mult", m.qi_gathering_mult * 1.30),
        "tempering": lambda m: setattr(m, "adventure_defense", m.adventure_defense + 0.12),
        "clarity": lambda m: setattr(m, "clarity_breakthrough_bonus", m.clarity_breakthrough_bonus + 0.05),
        "swiftwind": lambda m: setattr(m, "adventure_success", m.adventure_success + 0.10),
        "blood_ember": lambda m: setattr(m, "dungeon_damage", m.dungeon_damage + 0.15),
        "moonwell_tonic": lambda m: setattr(m, "rare_event_mult", m.rare_event_mult * 1.35),
        "shrine_boon": lambda m: (
            setattr(m, "adventure_success", m.adventure_success + 0.10),
            setattr(m, "pvp_power", m.pvp_power + 0.10),
        ),
        "shrine_curse": lambda m: setattr(m, "adventure_success", m.adventure_success - 0.08),
    }
    fn = mapping.get(effect_id)
    if fn:
        fn(mod)


def add_effect(
    session: Session,
    player_id: int,
    effect_id: str,
    charges: int | None = None,
    hours: float | None = None,
    value_int: int | None = None,
) -> None:
    from datetime import timedelta

    expires_at = None
    if hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    stmt = select(PlayerEffect).where(PlayerEffect.player_id == player_id, PlayerEffect.effect_id == effect_id)
    existing = session.execute(stmt).scalar_one_or_none()
    if existing:
        if charges is not None:
            existing.charges = (existing.charges or 0) + charges
        if expires_at is not None:
            existing.expires_at = expires_at
        if value_int is not None:
            existing.value_int = value_int
        session.add(existing)
        return

    session.add(
        PlayerEffect(
            player_id=player_id,
            effect_id=effect_id,
            charges=charges,
            value_int=value_int,
            expires_at=expires_at,
        )
    )


def add_haste_effect(session: Session, player_id: int, item_id: str) -> None:
    meta = HASTE_EFFECTS[item_id]
    if meta["effect_id"] == "void_pulse":
        add_void_pulse_haste(session, player_id)
        return
    add_effect(
        session,
        player_id,
        str(meta["effect_id"]),
        charges=int(meta["default_charges"]),
        value_int=int(meta["seconds_per_charge"]),
    )


VOID_PULSE_HASTE: dict[str, int] = {
    "haste_cultivate": 420,
    "haste_adventure": 600,
    "haste_dungeon": 1800,
    "haste_duel": 3600,
    "haste_gather": 180,
    "haste_hunt": 180,
}


def add_void_pulse_haste(session: Session, player_id: int) -> None:
    for effect_id, seconds in VOID_PULSE_HASTE.items():
        add_effect(session, player_id, effect_id, charges=1, value_int=seconds)


def consume_effect_charge(session: Session, player_id: int, effect_id: str) -> None:
    stmt = select(PlayerEffect).where(PlayerEffect.player_id == player_id, PlayerEffect.effect_id == effect_id)
    eff = session.execute(stmt).scalar_one_or_none()
    if eff is None or eff.charges is None:
        return
    eff.charges -= 1
    if eff.charges <= 0:
        session.delete(eff)
    else:
        session.add(eff)
