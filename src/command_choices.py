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
from .area_risk import format_area_choice_label
from .content import area_for_realm, get_areas, get_dungeons, get_recipes, resolve_area_id
from .equipment import get_player_equipment
from .equipment_tiers import GEAR_GRADES, grade_label, normalize_gear_path, path_label, resolve_equipment_tier
from .forge import get_forge_recipes
from .inventory import get_item_def, get_item_name, get_item_quantity, get_player_inventory, has_items, load_item_catalog
from .manuals import MANUAL_CRAFT_INPUTS, can_unseal_manual, is_sealed_manual, unseal_manual_item_id
from .player_guides import guide_text
from .models import Player
from .pill_recipes import recipe_available_for_realm, resolve_recipe_inputs
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
        manual_id = unseal_manual_item_id(stack.item_id) if is_sealed_manual(stack.item_id) else stack.item_id
        tech = get_technique_by_manual(manual_id)
        if tech is not None and tech.technique_id in learned:
            continue
        technique_name = tech.name if tech is not None else "Unknown art"
        player = session.get(Player, player_id)
        status = ""
        if is_sealed_manual(stack.item_id) and player is not None:
            ok, _ = can_unseal_manual(player, stack.item_id)
            status = (
                f" · {guide_text('sealed_manual', 'inventory_ready')}"
                if ok
                else f" · {guide_text('sealed_manual', 'inventory_sealed')}"
            )
        label = f"{item.name} (×{stack.quantity}) — {technique_name}{status}"
        options.append((stack.item_id, label, 0 if "Ready" in status else 1))

    options.sort(key=lambda row: (row[2], row[1].lower()))
    return [(value, label) for value, label, _ in options]


def resolve_manual_item_id(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None

    catalog = load_item_catalog()
    normalized = text.lower().replace(" ", "_").replace("-", "_")
    if is_sealed_manual(normalized):
        return normalized
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


def _recipe_option_label(
    session: Session,
    player_id: int,
    recipe,
    *,
    player: Player | None = None,
) -> str:
    if player is None:
        player = session.get(Player, player_id)
    realm_index = player.realm_index if player is not None else 0
    pct = int(recipe.success_chance * 100)
    inputs = resolve_recipe_inputs(recipe, realm_index)
    inputs_text = ", ".join(
        f"{get_item_name(item_id)} ×{qty}" for item_id, qty in sorted(inputs.items())
    )
    ready = has_items(session, player_id, inputs)
    prefix = "✓ " if ready else ""
    return f"{prefix}{recipe.name} ({pct}%) — {inputs_text}"


def list_recipe_options(
    session: Session,
    player_id: int,
    recipe_type: str,
    *,
    player: Player | None = None,
) -> list[tuple[str, str]]:
    """All recipes of a type for autocomplete (craft attempt may still fail on materials)."""
    if player is None:
        player = session.get(Player, player_id)
    realm_index = player.realm_index if player is not None else 0
    options: list[tuple[str, str]] = []
    for recipe in get_recipes().values():
        if recipe.recipe_type != recipe_type:
            continue
        if not recipe_available_for_realm(recipe, realm_index):
            continue
        options.append((recipe.recipe_id, _recipe_option_label(session, player_id, recipe, player=player)))
    options.sort(key=lambda row: row[0])
    return options


def list_craftable_recipes(
    session: Session,
    player_id: int,
    recipe_type: str,
    *,
    player: Player | None = None,
) -> list[tuple[str, str]]:
    if player is None:
        player = session.get(Player, player_id)
    realm_index = player.realm_index if player is not None else 0
    options: list[tuple[str, str]] = []
    for recipe in get_recipes().values():
        if recipe.recipe_type != recipe_type:
            continue
        if not recipe_available_for_realm(recipe, realm_index):
            continue
        inputs = resolve_recipe_inputs(recipe, realm_index)
        if not has_items(session, player_id, inputs):
            continue
        options.append((recipe.recipe_id, _recipe_option_label(session, player_id, recipe, player=player)))

    options.sort(key=lambda row: row[0])
    return options


def can_bind_technique_manual(session: Session, player_id: int) -> bool:
    return has_items(session, player_id, MANUAL_CRAFT_INPUTS)


def list_forgeable_slots(session: Session, player_id: int, *, player: Player | None = None) -> list[tuple[str, str]]:
    if player is None:
        player = session.get(Player, player_id)
    realm_index = player.realm_index if player is not None else 0
    options: list[tuple[str, str]] = []
    for slot in ("weapon", "armor", "accessory", "talisman"):
        for grade in GEAR_GRADES:
            entry = resolve_equipment_tier(realm_index, slot, grade)
            if entry is None:
                continue
            if not has_items(session, player_id, entry.inputs):
                continue
            inputs_text = ", ".join(
                f"{get_item_name(item_id)} ×{qty}" for item_id, qty in sorted(entry.inputs.items())
            )
            label = f"{slot.title()} ({path_label(grade)}) — {entry.name} ({inputs_text})"
            options.append((f"{slot}|{grade}", label))
    return options


def list_affixable_gear(session: Session, player_id: int) -> list[tuple[str, str]]:
    from .gear_stash import format_gear_item_label, list_all_gear_items

    has_stone = get_item_quantity(session, player_id, "affix_stone") >= 1
    options: list[tuple[str, str]] = []
    for item in list_all_gear_items(session, player_id):
        affix = item.affix_id.replace("_", " ").title() if item.affix_id else "no affix"
        stone_note = "" if has_stone else " — need Affix Stone in bag"
        worn = " · worn" if item.equipped_in_slot else " · stash"
        label = f"{format_gear_item_label(item)}{worn} ({affix}){stone_note}"
        options.append((str(item.id), label))
    return options


def list_affixable_slots(session: Session, player_id: int) -> list[tuple[str, str]]:
    return list_affixable_gear(session, player_id)


def list_equippable_gear(session: Session, player_id: int) -> list[tuple[str, str]]:
    from .gear_stash import format_gear_item_label, list_stash

    return [(str(item.id), format_gear_item_label(item)) for item in list_stash(session, player_id)]


def list_recyclable_gear(session: Session, player_id: int) -> list[tuple[str, str]]:
    from .gear_stash import format_gear_item_label, list_stash, recycle_spirit_stones_for_realm

    options: list[tuple[str, str]] = []
    for item in list_stash(session, player_id):
        stones = recycle_spirit_stones_for_realm(item.gear_realm)
        affix_note = " + Affix Stone" if item.affix_id else ""
        label = f"{format_gear_item_label(item)} → {stones} stones{affix_note}"
        options.append((str(item.id), label))
    return options


def resolve_gear_item_id(raw: str) -> int | None:
    text = raw.strip()
    if not text.isdigit():
        return None
    return int(text)


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
        label = format_area_choice_label(player, area)
        options.append((area_id, label))
    return options


def resolve_area_choice(raw: str) -> str | None:
    return resolve_area_id(raw)


def adventure_area_for_player(player: Player) -> str:
    return area_for_realm(player.realm_index).area_id


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
                label = f"[Active] {tech.name} → Slot {slot}"
                options.append((f"{tech.technique_id}|{slot}", label))
        elif loadout.get(PASSIVE_SLOT) != tech.technique_id:
            options.append((f"{tech.technique_id}|passive", f"[Passive] {tech.name} → Passive slot"))

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
            options.append((slot, f"Active slot {slot}{suffix}"))
    elif tech.slot_type == "passive":
        suffix = " (equipped)" if loadout.get(PASSIVE_SLOT) == technique_id else ""
        options.append(("passive", f"Passive slot{suffix}"))
    return options


def resolve_forge_slot(raw: str) -> str | None:
    text = raw.strip().lower()
    if "|" in text:
        slot, _grade = text.split("|", 1)
        slot = slot.strip()
        if slot in ("weapon", "armor", "accessory", "talisman"):
            return slot
    if text in ("weapon", "armor", "accessory", "talisman"):
        return text
    return None


def resolve_forge_choice(raw: str) -> tuple[str, str] | None:
    text = raw.strip().lower()
    if "|" in text:
        slot, grade = text.split("|", 1)
        slot = slot.strip()
        grade = normalize_gear_path(grade.strip())
        if slot in ("weapon", "armor", "accessory", "talisman") and grade in GEAR_GRADES:
            return slot, grade
    slot = resolve_forge_slot(text)
    if slot is not None:
        return slot, "external"
    return None
