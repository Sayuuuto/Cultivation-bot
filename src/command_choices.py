from __future__ import annotations

from sqlalchemy.orm import Session

from .combat.loadout import (
    ACTIVE_SLOTS,
    PASSIVE_SLOT,
    get_learned_technique_ids,
    get_learned_techniques,
    get_loadout,
)
from .combat.catalog import get_technique, get_technique_by_manual, load_technique_catalog
from .content import get_areas, get_dungeons, get_recipes
from .equipment import get_player_equipment
from .forge import get_forge_recipes
from .inventory import get_item_def, get_item_name, get_item_quantity, get_player_inventory, has_items, load_item_catalog
from .manuals import MANUAL_CRAFT_INPUTS
from .models import Player
from .shop import list_shop_listings

MAX_AUTOCOMPLETE = 25

TECHNIQUE_SLOT_OPTIONS = ("1", "2", "3", "4", "passive")


def filter_options(
    options: list[tuple[str, str]],
    current: str,
    *,
    limit: int = MAX_AUTOCOMPLETE,
) -> list[tuple[str, str]]:
    current_lower = current.lower()
    if not current_lower:
        return options[:limit]

    filtered: list[tuple[str, str]] = []
    for value, label in options:
        haystack = f"{value} {label}".lower()
        if current_lower not in haystack:
            continue
        filtered.append((value, label))
        if len(filtered) >= limit:
            break
    return filtered


def list_player_manuals(session: Session, player_id: int) -> list[tuple[str, str]]:
    learned = get_learned_technique_ids(session, player_id)
    options: list[tuple[str, str]] = []

    for stack in get_player_inventory(session, player_id):
        item = get_item_def(stack.item_id)
        if item is None or item.category != "manual" or stack.quantity <= 0:
            continue
        tech = get_technique_by_manual(stack.item_id)
        if tech is not None and tech.technique_id in learned:
            continue
        technique_name = tech.name if tech is not None else "Unknown art"
        label = f"{item.name} (×{stack.quantity}) — {technique_name}"
        options.append((stack.item_id, label))

    options.sort(key=lambda row: row[1].lower())
    return options


def resolve_manual_item_id(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None

    catalog = load_item_catalog()
    normalized = text.lower().replace(" ", "_").replace("-", "_")
    item = catalog.get(normalized)
    if item is not None and item.category == "manual":
        return normalized

    lower = text.lower()
    for item_id, item_def in catalog.items():
        if item_def.category == "manual" and item_def.name.lower() == lower:
            return item_id

    matches: list[str] = []
    tokens = [part for part in lower.replace("_", " ").split() if part]
    for item_id, item_def in catalog.items():
        if item_def.category != "manual":
            continue
        haystack = f"{item_id.replace('_', ' ')} {item_def.name}".lower()
        if tokens:
            if all(token in haystack for token in tokens):
                matches.append(item_id)
        elif lower in haystack:
            matches.append(item_id)

    if len(matches) == 1:
        return matches[0]
    return None


def list_craftable_recipes(session: Session, player_id: int, recipe_type: str) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for recipe in get_recipes().values():
        if recipe.recipe_type != recipe_type:
            continue
        if not has_items(session, player_id, recipe.inputs):
            continue
        pct = int(recipe.success_chance * 100)
        inputs = ", ".join(
            f"{get_item_name(item_id)} ×{qty}" for item_id, qty in sorted(recipe.inputs.items())
        )
        label = f"{recipe.name} ({pct}%) — {inputs}"
        options.append((recipe.recipe_id, label))

    options.sort(key=lambda row: row[0])
    return options


def can_bind_technique_manual(session: Session, player_id: int) -> bool:
    return has_items(session, player_id, MANUAL_CRAFT_INPUTS)


def list_forgeable_slots(session: Session, player_id: int) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for slot, recipe in get_forge_recipes().items():
        inputs: dict[str, int] = recipe["inputs"]
        if not has_items(session, player_id, inputs):
            continue
        inputs_text = ", ".join(
            f"{get_item_name(item_id)} ×{qty}" for item_id, qty in sorted(inputs.items())
        )
        label = f"{slot.title()} — {recipe['name']} ({inputs_text})"
        options.append((slot, label))
    return options


def list_affixable_slots(session: Session, player_id: int) -> list[tuple[str, str]]:
    if get_item_quantity(session, player_id, "affix_stone") < 1:
        return []

    equipped = {row.slot: row for row in get_player_equipment(session, player_id)}
    options: list[tuple[str, str]] = []
    for slot in ("weapon", "armor", "accessory", "talisman"):
        row = equipped.get(slot)
        if row is None or not row.item_id:
            continue
        item_name = get_item_name(row.item_id)
        affix = row.affix_id.replace("_", " ").title() if row.affix_id else "no affix"
        label = f"{slot.title()} — {item_name} ({affix})"
        options.append((slot, label))
    return options


def list_equippable_techniques(session: Session, player: Player) -> list[tuple[str, str]]:
    learned = get_learned_technique_ids(session, player.id)
    catalog = load_technique_catalog()
    options: list[tuple[str, str]] = []

    for technique_id in sorted(learned):
        tech = catalog.get(technique_id)
        if tech is None:
            continue
        if player.realm_index < tech.min_realm:
            continue
        slot_type = "Passive" if tech.slot_type == "passive" else "Active"
        label = f"{tech.name} [{tech.category}/{tech.tier}] — {slot_type}"
        options.append((technique_id, label))

    return options


def list_unlocked_areas(player: Player) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for area_id, area in get_areas().items():
        if player.realm_index < area.min_realm:
            continue
        label = f"{area.name} ({area.recommended_text})"
        options.append((area_id, label))
    return options


def list_enterable_dungeons(session: Session, player: Player) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for dungeon_id, dungeon in get_dungeons().items():
        if player.realm_index < dungeon.min_realm:
            continue
        keys = get_item_quantity(session, player.id, dungeon.key_item_id)
        if keys < 1:
            continue
        label = f"{dungeon.name} (×{keys} {get_item_name(dungeon.key_item_id)})"
        options.append((dungeon_id, label))
    return options


def list_affordable_shop_items(player: Player) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for listing in list_shop_listings():
        if player.spirit_stones < listing.price:
            continue
        label = f"{listing.name} ({listing.price} stones)"
        options.append((listing.shop_id, label))
    return options


def resolve_technique_id(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None

    catalog = load_technique_catalog()
    normalized = text.lower().replace(" ", "_").replace("-", "_")
    if normalized in catalog:
        return normalized

    lower = text.lower()
    for technique_id, tech in catalog.items():
        if tech.name.lower() == lower:
            return technique_id

    matches: list[str] = []
    tokens = [part for part in lower.replace("_", " ").split() if part]
    for technique_id, tech in catalog.items():
        haystack = f"{technique_id.replace('_', ' ')} {tech.name}".lower()
        if tokens:
            if all(token in haystack for token in tokens):
                matches.append(technique_id)
        elif lower in haystack:
            matches.append(technique_id)

    if len(matches) == 1:
        return matches[0]
    return None


def list_technique_equip_options(session: Session, player: Player) -> list[tuple[str, str]]:
    """Return (value, label) pairs where value is 'technique_id|slot'."""
    learned = get_learned_techniques(session, player.id)
    loadout = get_loadout(session, player.id)
    options: list[tuple[str, str]] = []

    for tech in learned:
        if player.realm_index < tech.min_realm:
            continue
        if tech.slot_type == "active":
            for slot in ACTIVE_SLOTS:
                if loadout.get(slot) == tech.technique_id:
                    continue
                label = f"{tech.name} → Slot {slot}"
                options.append((f"{tech.technique_id}|{slot}", label))
        elif loadout.get(PASSIVE_SLOT) != tech.technique_id:
            options.append((f"{tech.technique_id}|passive", f"{tech.name} → Passive"))

    options.sort(key=lambda row: row[1].lower())
    return options[:MAX_AUTOCOMPLETE]


def list_valid_slots_for_technique(session: Session, player: Player, technique_id: str) -> list[tuple[str, str]]:
    tech = get_technique(technique_id)
    if tech is None or player.realm_index < tech.min_realm:
        return []
    loadout = get_loadout(session, player.id)
    options: list[tuple[str, str]] = []
    if tech.slot_type == "active":
        for slot in ACTIVE_SLOTS:
            current = loadout.get(slot)
            suffix = " (equipped)" if current == technique_id else ""
            options.append((slot, f"Slot {slot}{suffix}"))
    elif tech.slot_type == "passive":
        suffix = " (equipped)" if loadout.get(PASSIVE_SLOT) == technique_id else ""
        options.append(("passive", f"Passive{suffix}"))
    return options


def resolve_forge_slot(raw: str) -> str | None:
    slot = raw.strip().lower()
    if slot in get_forge_recipes():
        return slot
    for candidate in get_forge_recipes():
        if candidate.lower() == slot:
            return candidate
    return None
