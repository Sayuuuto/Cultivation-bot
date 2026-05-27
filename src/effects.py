from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .game import CLARITY_BONUS_PER_CHARGE
from .modifiers import CharacterModifiers
from .game import to_utc
from .models import PlayerEffect


EFFECT_DESCRIPTIONS: dict[str, str] = {
    "qi_gathering": "+55% qi from cultivation (stacks charges per pill)",
    "tempering": "+12% adventure defense (1 run)",
    "clarity": "+14% breakthrough stability per charge (stacks; consumed on attempt)",
    "swiftwind": "+10% adventure success (1 run)",
    "blood_ember": "+15% dungeon damage (1 run)",
    "moonwell_tonic": "+35% rare event chance (1 run)",
    "shrine_boon": "+10% adventure success and PvP power (2 runs)",
    "shrine_curse": "-8% adventure success (2 runs)",
    "haste_universal": "Shaves time off your next command cooldowns (all activities)",
}

EFFECT_LABELS: dict[str, str] = {
    "qi_gathering": "Qi Gathering",
    "tempering": "Tempering",
    "clarity": "Clarity",
    "swiftwind": "Swiftwind",
    "blood_ember": "Blood Ember",
    "moonwell_tonic": "Moonwell attunement",
    "shrine_boon": "Shrine boon",
    "shrine_curse": "Shrine curse",
    "haste_universal": "Meridian haste",
    "haste_adventure": "Adventure haste",
    "haste_cultivate": "Cultivation haste",
    "haste_dungeon": "Dungeon haste",
    "haste_duel": "Duel haste",
    "haste_gather": "Gather haste",
    "haste_hunt": "Hunt haste",
}

HASTE_EFFECTS: dict[str, dict[str, int | str]] = {
    "flow_pill": {
        "default_charges": 2,
        "seconds_per_charge": 900,
        "label": "Flow Meridian",
    },
    "meridian_surge_pill": {
        "default_charges": 3,
        "seconds_per_charge": 900,
        "label": "Meridian Surge",
    },
    "gatebreaker_dust": {
        "default_charges": 2,
        "seconds_per_charge": 2400,
        "label": "Gatebreaker Dust",
    },
    "void_pulse_pill": {
        "default_charges": 2,
        "seconds_per_charge": 3600,
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
        "qi_gathering": lambda m: setattr(m, "qi_gathering_mult", m.qi_gathering_mult * 1.55),
        "tempering": lambda m: setattr(m, "adventure_defense", m.adventure_defense + 0.12),
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


def get_effect_charges(session: Session, player_id: int, effect_id: str) -> int:
    stmt = select(PlayerEffect).where(
        PlayerEffect.player_id == player_id,
        PlayerEffect.effect_id == effect_id,
    )
    rows = list(session.execute(stmt).scalars().all())
    if not rows:
        return 0
    return sum(max(0, row.charges or 0) for row in rows)


def clarity_breakthrough_bonus(session: Session, player_id: int) -> tuple[int, float]:
    charges = get_effect_charges(session, player_id, "clarity")
    return charges, charges * CLARITY_BONUS_PER_CHARGE


def consume_clarity_for_breakthrough(session: Session, player_id: int) -> None:
    stmt = select(PlayerEffect).where(
        PlayerEffect.player_id == player_id,
        PlayerEffect.effect_id == "clarity",
    )
    eff = session.execute(stmt).scalar_one_or_none()
    if eff is not None:
        session.delete(eff)


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
    existing_rows = list(session.execute(stmt).scalars().all())
    if existing_rows:
        primary = existing_rows[0]
        for duplicate in existing_rows[1:]:
            if duplicate.charges is not None:
                primary.charges = (primary.charges or 0) + duplicate.charges
            session.delete(duplicate)
        if charges is not None:
            primary.charges = (primary.charges or 0) + charges
        if expires_at is not None:
            primary.expires_at = expires_at
        if value_int is not None:
            primary.value_int = value_int
        session.add(primary)
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
    from .cooldown_haste import HASTE_UNIVERSAL_EFFECT

    meta = HASTE_EFFECTS[item_id]
    add_effect(
        session,
        player_id,
        HASTE_UNIVERSAL_EFFECT,
        charges=int(meta["default_charges"]),
        value_int=int(meta["seconds_per_charge"]),
    )


def _format_minutes(seconds: int) -> str:
    minutes = max(1, seconds // 60)
    if minutes >= 60:
        hours, rem = divmod(minutes, 60)
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    return f"{minutes} min"


def _active_player_effects(session: Session, player_id: int) -> list[PlayerEffect]:
    from .cooldown_haste import HASTE_UNIVERSAL_EFFECT

    now = datetime.now(timezone.utc)
    stmt = select(PlayerEffect).where(PlayerEffect.player_id == player_id)
    rows = list(session.execute(stmt).scalars().all())
    active: list[PlayerEffect] = []
    for eff in rows:
        if eff.expires_at is not None and to_utc(eff.expires_at) < now:
            continue
        if eff.charges is not None and eff.charges <= 0:
            continue
        if eff.effect_id == HASTE_UNIVERSAL_EFFECT and (eff.value_int or 0) <= 0:
            continue
        if eff.effect_id.startswith("haste_") and eff.effect_id != HASTE_UNIVERSAL_EFFECT:
            if (eff.charges or 0) <= 0 and (eff.value_int or 0) <= 0:
                continue
        active.append(eff)
    active.sort(key=lambda row: row.effect_id)
    return active


def _format_effect_line(eff: PlayerEffect) -> str:
    from .cooldown_haste import HASTE_UNIVERSAL_EFFECT

    label = EFFECT_LABELS.get(eff.effect_id, eff.effect_id.replace("_", " ").title())
    charges = eff.charges or 1

    if eff.effect_id == HASTE_UNIVERSAL_EFFECT:
        shave = _format_minutes(eff.value_int or 0)
        count = eff.charges or 1
        return f"**{label}** — **−{shave}** per cooldown · **{count}** use(s) left (all commands)"

    if eff.effect_id.startswith("haste_"):
        shave = _format_minutes(eff.value_int or 0)
        activity = eff.effect_id.removeprefix("haste_")
        return f"**{label}** — **−{shave}** on next **`/{activity}`** cooldown"

    profile_lines = {
        "qi_gathering": lambda c: f"**Qi Gathering** — **+55% Qi** · **{c}** **`/cultivate`** left",
        "tempering": lambda c: f"**Tempering** — **+12% defense** · **{c}** adventure/dungeon run(s) left",
        "clarity": lambda c: f"**Clarity** — **+14% breakthrough** per charge · **{c}** for **`/breakthrough`**",
        "swiftwind": lambda c: f"**Swiftwind** — **+10% adventure success** · **{c}** **`/adventure`** left",
        "blood_ember": lambda c: f"**Blood Ember** — **+15% dungeon damage** · **{c}** dungeon run(s) left",
        "moonwell_tonic": lambda c: f"**Moonwell attunement** — **+35% rare events** · **{c}** **`/adventure`** left",
        "shrine_boon": lambda c: f"**Shrine boon** — **+10% success & PvP power** · **{c}** run(s) left",
        "shrine_curse": lambda c: f"**Shrine curse** — **−8% adventure success** · **{c}** run(s) left",
    }
    formatter = profile_lines.get(eff.effect_id)
    if formatter is not None:
        return formatter(charges)

    desc = EFFECT_DESCRIPTIONS.get(eff.effect_id, label)
    return f"**{label}** — {desc} · **{charges}** charge(s) left"


def list_active_player_effects(session: Session, player_id: int) -> list[PlayerEffect]:
    return _active_player_effects(session, player_id)


def format_active_effects_block(session: Session, player_id: int) -> str | None:
    """Player-facing summary of pill and shrine effects still active."""
    rows = list_active_player_effects(session, player_id)
    if not rows:
        return None
    return "\n".join(_format_effect_line(eff) for eff in rows)


def format_pill_use_message(
    session: Session,
    player_id: int,
    effect_id: str,
    pill_name: str,
) -> str:
    """Clear feedback after consuming a pill (includes stacked charge totals)."""
    total = get_effect_charges(session, player_id, effect_id)

    if effect_id == "qi_gathering":
        return (
            f"You consume **{pill_name}**. **+55% Qi** on your next **`/cultivate`** "
            f"(and each stored charge after). **{total}** charge(s) stored."
        )
    if effect_id == "clarity":
        return (
            f"You consume **{pill_name}**. **+14% breakthrough stability** per charge — "
            f"**{total}** charge(s) waiting; all apply on your next **`/breakthrough`**."
        )
    if effect_id == "tempering":
        return (
            f"You consume **{pill_name}**. **+12% defense** on your next **`/adventure`** "
            f"or dungeon run. **{total}** charge(s) stored."
        )
    if effect_id == "swiftwind":
        return (
            f"You consume **{pill_name}**. **+10% adventure success** on your next **`/adventure`**. "
            f"**{total}** charge(s) stored."
        )
    if effect_id == "blood_ember":
        return (
            f"You consume **{pill_name}**. **+15% dungeon damage** on your next dungeon run. "
            f"**{total}** charge(s) stored."
        )
    if effect_id == "moonwell_tonic":
        return (
            f"You consume **{pill_name}**. **+35% rare event chance** on your next **`/adventure`**. "
            f"**{total}** charge(s) stored."
        )

    desc = EFFECT_DESCRIPTIONS.get(effect_id, "Its power settles within you.")
    return f"You consume **{pill_name}**. {desc} **{total}** charge(s) stored."


def format_haste_use_message(
    session: Session,
    player_id: int,
    pill_name: str,
    *,
    charges_per_pill: int,
    seconds_per_charge: int,
) -> str:
    from .cooldown_haste import HASTE_UNIVERSAL_EFFECT

    total = get_effect_charges(session, player_id, HASTE_UNIVERSAL_EFFECT)
    shave = _format_minutes(seconds_per_charge)
    return (
        f"You consume **{pill_name}**. Void qi settles in your meridians — "
        f"the next **{total}** command cooldowns (**`/cultivate`**, **`/hunt`**, **`/adventure`**, "
        f"**`/daily`**, and more) shorten by **{shave}** each. "
        f"(This pill added **{charges_per_pill}** charge(s).)"
    )


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
