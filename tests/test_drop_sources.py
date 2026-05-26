from __future__ import annotations

import random

import pytest

from src.content import load_all_content
from src.crafting import craft_recipe
from src.drop_sources import format_item_drop_hints, get_drop_sources
from src.forge import forge_equipment
from src.inventory import add_item, load_item_catalog


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_spirit_iron_shard_sources_include_adventure_and_dungeon():
    labels = {s.label for s in get_drop_sources("spirit_iron_shard")}
    assert any("Ashen Cliff" in label for label in labels)
    assert any("Blackwind" in label for label in labels)


def test_bamboo_materials_point_to_bamboo_grove():
    hint = format_item_drop_hints("green_dew_herb")
    assert "Whispering Bamboo Grove" in hint
    assert "/adventure" in hint


def test_forge_missing_materials_lists_farm_locations(session, player):
    res = forge_equipment(session, player.id, "weapon", rng=random.Random(1))
    assert res.success is False
    assert "Ashen Cliff" in res.message or "Bamboo" in res.message or "Whispering" in res.message
    assert "/adventure" in res.message


def test_craft_missing_materials_lists_farm_locations(session, player):
    res = craft_recipe(session, player, "qi_gathering_pill", amount=1, rng=random.Random(0))
    assert res.success is False
    assert "Whispering Bamboo Grove" in res.message
    assert "/areas" in res.message


def test_craft_key_missing_lists_sources(session, player):
    res = craft_recipe(session, player, "blackwind_key", amount=1, rng=random.Random(0))
    assert res.success is False
    assert "/adventure" in res.message or "/dungeon" in res.message
