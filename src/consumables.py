from __future__ import annotations

import random

from sqlalchemy.orm import Session

from .effects import add_effect, add_haste_effect, format_haste_use_message, format_pill_use_message, HASTE_EFFECTS
from .game import SPIRIT_ROOTS
from .inventory import get_item_def, get_item_name, get_item_quantity, load_item_catalog, remove_item
from .models import Player


PILL_EFFECTS: dict[str, dict[str, int | float | None]] = {
    "qi_gathering_pill": {"effect_id": "qi_gathering", "charges": 3},
    "tempering_pill": {"effect_id": "tempering", "charges": 1},
    "clarity_pill": {"effect_id": "clarity", "charges": 1},
    "swiftwind_pill": {"effect_id": "swiftwind", "charges": 1},
    "blood_ember_pill": {"effect_id": "blood_ember", "charges": 1},
    "moonwell_tonic": {"effect_id": "moonwell_tonic", "charges": 1},
}


def get_usable_item_ids() -> frozenset[str]:
    return frozenset(set(PILL_EFFECTS.keys()) | set(HASTE_EFFECTS.keys()) | {"root_reforging_pill"})


def resolve_use_item_id(raw: str) -> str | None:
    """Map player input (id, display name, or partial) to a usable item_id."""
    text = raw.strip()
    if not text:
        return None

    usable = get_usable_item_ids()
    catalog = load_item_catalog()

    normalized = text.lower().replace(" ", "_").replace("-", "_")
    if normalized in usable:
        return normalized

    lower = text.lower()
    for item_id in usable:
        item = catalog.get(item_id)
        if item is not None and item.name.lower() == lower:
            return item_id

    matches: list[str] = []
    tokens = [part for part in lower.replace("_", " ").split() if part]
    for item_id in usable:
        item = catalog.get(item_id)
        if item is None:
            continue
        haystack = f"{item_id.replace('_', ' ')} {item.name}".lower()
        if tokens:
            if all(token in haystack for token in tokens):
                matches.append(item_id)
        elif lower in haystack:
            matches.append(item_id)

    if len(matches) == 1:
        return matches[0]
    return None


def list_usable_inventory(session: Session, player_id: int) -> list[tuple[str, int]]:
    from .inventory import get_player_inventory

    usable = get_usable_item_ids()
    rows: list[tuple[str, int]] = []
    for stack in get_player_inventory(session, player_id):
        if stack.item_id in usable and stack.quantity > 0:
            rows.append((stack.item_id, stack.quantity))
    rows.sort(key=lambda row: get_item_name(row[0]).lower())
    return rows


def use_item(session: Session, player: Player, item_id: str, rng: random.Random | None = None) -> tuple[bool, str]:
    rng = rng or random.Random()
    resolved = resolve_use_item_id(item_id)
    if resolved is None:
        hint = "Pick a pill from the **`/use`** autocomplete list, or type its name (e.g. `Qi Gathering Pill`)."
        return False, f"Could not find a usable item matching **{item_id.strip()}**. {hint}"

    item_id = resolved

    if get_item_quantity(session, player.id, item_id) < 1:
        return False, "You do not have that item."

    if item_id in HASTE_EFFECTS:
        if not remove_item(session, player.id, item_id, 1):
            return False, "You do not have that item."
        add_haste_effect(session, player.id, item_id)
        session.flush()
        meta = HASTE_EFFECTS[item_id]
        item = get_item_def(item_id)
        name = item.name if item else item_id
        return (
            True,
            format_haste_use_message(
                session,
                player.id,
                name,
                charges_per_pill=int(meta["default_charges"]),
                seconds_per_charge=int(meta["seconds_per_charge"]),
            ),
        )

    if item_id == "root_reforging_pill":
        if not remove_item(session, player.id, item_id, 1):
            return False, "You do not have a Root Reforging Pill."
        old = player.spirit_root
        choices = [r for r in SPIRIT_ROOTS if r != old]
        player.spirit_root = rng.choice(choices) if choices else rng.choice(SPIRIT_ROOTS)
        session.add(player)
        return True, f"Your spirit root shifts from **{old}** to **{player.spirit_root}**."

    pill = PILL_EFFECTS.get(item_id)
    if pill is None:
        return False, "That item cannot be used directly."

    if not remove_item(session, player.id, item_id, 1):
        return False, "You do not have that item."

    effect_id = str(pill["effect_id"])
    charges = pill.get("charges")
    hours = pill.get("hours")
    add_effect(
        session,
        player.id,
        effect_id,
        charges=int(charges) if charges is not None else None,
        hours=float(hours) if hours is not None else None,
    )
    session.flush()

    item = get_item_def(item_id)
    name = item.name if item else item_id
    return True, format_pill_use_message(session, player.id, effect_id, name)
