from __future__ import annotations

import random

import pytest

from src.content import load_all_content
from src.crafting import craft_recipe
from src.drop_sources import format_item_drop_hints, get_drop_sources
from src.forge import forge_equipment_for_player
from src.inventory import add_item, load_item_catalog


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_spirit_iron_shard_sources_include_adventure_and_dungeon():
    labels = {s.label for s in get_drop_sources("spirit_iron_shard")}
    assert any("Qi Refining Cliffs" in label for label in labels)
    assert any("Blackwind" in label for label in labels)


def test_bamboo_materials_point_to_bamboo_grove():
    hint = format_item_drop_hints("green_dew_herb")
    assert "Mortal Grove" in hint
    assert "/adventure" in hint or "/gather" in hint


def test_forge_missing_materials_lists_farm_locations(session, player):
    res = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    assert res.success is False
    assert "Qi Refining Cliffs" in res.message or "Mortal Grove" in res.message
    assert "/adventure" in res.message


def test_craft_missing_materials_lists_farm_locations(session, player):
    res = craft_recipe(session, player, "qi_gathering_pill", amount=1, rng=random.Random(0))
    assert res.success is False
    assert "brew this pill" in res.message.lower()
    assert "Mortal Grove" in res.message
    assert "/item" in res.message


def test_drop_sources_include_gather_for_herbs():
    vias = {s.via for s in get_drop_sources("green_dew_herb")}
    assert "`/gather`" in vias


def test_drop_sources_include_hunt_for_beast_cores():
    import src.drop_sources as drop_sources

    drop_sources._item_sources = None
    vias = {s.via for s in get_drop_sources("minor_beast_core")}
    assert "`/hunt`" in vias


def test_blank_scroll_sources_include_shop_and_gather():
    import src.drop_sources as drop_sources

    drop_sources._item_sources = None
    vias = {s.via for s in get_drop_sources("blank_scroll")}
    labels = {s.label for s in get_drop_sources("blank_scroll")}
    assert "`/shop buy`" in vias
    assert "Spirit Stone Shop" in labels
    assert "`/gather`" in vias


def test_craft_key_missing_lists_sources(session, player):
    res = craft_recipe(session, player, "blackwind_key", amount=1, rng=random.Random(0))
    assert res.success is False
    assert "/adventure" in res.message or "/dungeon" in res.message
