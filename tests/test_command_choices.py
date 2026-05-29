from __future__ import annotations

import random

import pytest

from src.command_choices import (
    can_bind_technique_manual,
    list_affixable_gear,
    list_craftable_recipes,
    list_recipe_options,
    list_enterable_dungeons,
    list_equippable_techniques,
    list_forgeable_slots,
    list_player_manuals,
    list_technique_equip_options,
    list_unlocked_areas,
    list_valid_slots_for_technique,
    resolve_manual_item_id,
    resolve_technique_id,
)
from src.content import load_all_content
from src.inventory import add_item, load_item_catalog


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_list_player_manuals_excludes_learned_techniques(session, player):
    add_item(session, player.id, "manual_ember_palm", 1)
    add_item(session, player.id, "manual_swift_slash", 1)
    from src.combat.loadout import learn_technique

    learn_technique(session, player.id, "ember_palm")
    session.commit()

    manuals = list_player_manuals(session, player.id)
    ids = {item_id for item_id, _ in manuals}
    assert "manual_swift_slash" in ids
    assert "manual_ember_palm" not in ids


def test_list_recipe_options_includes_all_pills(session, player):
    options = list_recipe_options(session, player.id, "pill")
    recipe_ids = {recipe_id for recipe_id, _ in options}
    assert "qi_gathering_pill" in recipe_ids
    assert len(recipe_ids) >= 2


def test_list_craftable_recipes_only_when_materials_present(session, player):
    empty = list_craftable_recipes(session, player.id, "pill")
    assert empty == []

    add_item(session, player.id, "green_dew_herb", 5)
    session.commit()

    recipes = list_craftable_recipes(session, player.id, "pill")
    recipe_ids = {recipe_id for recipe_id, _ in recipes}
    assert "qi_gathering_pill" in recipe_ids

    add_item(session, player.id, "minor_beast_core", 2)
    session.commit()
    recipes = list_craftable_recipes(session, player.id, "pill")
    recipe_ids = {recipe_id for recipe_id, _ in recipes}
    assert "tempering_pill" in recipe_ids


def test_list_forgeable_slots_requires_inputs(session, player):
    assert list_forgeable_slots(session, player.id) == []

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    slots = list_forgeable_slots(session, player.id)
    assert any(slot.startswith("weapon|") for slot, _ in slots)


def test_list_affixable_gear_shows_stash_and_worn(session, player):
    assert list_affixable_gear(session, player.id) == []

    from src.forge import forge_and_equip

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()
    forge_and_equip(session, player, "weapon", rng=random.Random(1))
    session.commit()

    slots = list_affixable_gear(session, player.id)
    assert len(slots) == 1
    assert slots[0][0].isdigit()
    assert any("need Affix Stone" in label for _, label in slots)

    add_item(session, player.id, "affix_stone", 1)
    session.commit()
    ready_slots = list_affixable_gear(session, player.id)
    assert len(ready_slots) == 1
    assert all("need Affix Stone" not in label for _, label in ready_slots)


def test_list_equippable_techniques_filters_by_realm(session, player):
    from src.combat.loadout import learn_technique

    learn_technique(session, player.id, "swift_slash")
    learn_technique(session, player.id, "iron_cleave")
    session.commit()

    player.realm_index = 0
    session.commit()
    options = list_equippable_techniques(session, player)
    ids = {tech_id for tech_id, _ in options}
    assert "swift_slash" in ids
    assert "iron_cleave" not in ids


def test_list_unlocked_areas_shows_danger_labels(session, player):
    player.realm_index = 0
    session.commit()
    areas = {area_id: label for area_id, label in list_unlocked_areas(player)}
    assert "mortal_grove" in areas
    assert "foundation_ruins" in areas
    assert "deadly" in areas["foundation_ruins"].lower()


def test_list_enterable_dungeons_requires_key_and_realm(session, player):
    player.realm_index = 1
    session.commit()
    assert list_enterable_dungeons(session, player) == []

    add_item(session, player.id, "blackwind_key", 1)
    session.commit()
    dungeons = list_enterable_dungeons(session, player)
    assert any(dungeon_id == "blackwind" for dungeon_id, _ in dungeons)


def test_can_bind_technique_manual(session, player):
    assert can_bind_technique_manual(session, player.id) is False
    add_item(session, player.id, "technique_fragment", 3)
    add_item(session, player.id, "blank_scroll", 1)
    add_item(session, player.id, "spirit_ink", 1)
    session.commit()
    assert can_bind_technique_manual(session, player.id) is True


def test_resolve_manual_item_id_by_name():
    assert resolve_manual_item_id("Manual: Swift Slash") == "manual_swift_slash"


def test_list_technique_equip_options(session, player):
    from src.combat.loadout import learn_technique

    learn_technique(session, player.id, "swift_slash")
    learn_technique(session, player.id, "ember_palm")
    session.commit()

    options = list_technique_equip_options(session, player)
    assert options
    assert any("|" in value for value, _ in options)


def test_list_valid_slots_for_technique(session, player):
    from src.combat.loadout import learn_technique

    learn_technique(session, player.id, "swift_slash")
    session.commit()
    slots = list_valid_slots_for_technique(session, player, "swift_slash")
    assert len(slots) == 4
