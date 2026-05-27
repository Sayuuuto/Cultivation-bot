from __future__ import annotations

import random

import pytest

from src.adventure import run_adventure
from src.character import get_character_modifiers
from src.content import load_all_content
from src.crafting import craft_recipe
from src.dungeon import run_dungeon
from src.equipment import apply_affix_stone
from src.inventory import add_item, get_item_quantity, load_item_catalog
from src.consumables import use_item


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_character_modifiers_from_origin(session, player):
    player.origin = "Mountain Rises"
    player.spirit_root = "Pure Jade Root"
    mod = get_character_modifiers(session, player)
    assert mod.stamina_regen_mult > 1.0
    assert mod.breakthrough_stability > 0


def test_adventure_success_grants_drops(session, player):
    rng = random.Random(42)
    res = run_adventure(session, player, "bamboo_grove", "balanced", rng=rng)
    assert res.success is True
    assert res.segments_cleared >= 1
    session.commit()
    total_items = sum(get_item_quantity(session, player.id, item_id) for item_id in res.drops)
    assert total_items > 0 or res.segments_cleared == 0


def test_craft_pill_consumes_materials(session, player):
    add_item(session, player.id, "green_dew_herb", 3)
    session.commit()

    res = craft_recipe(session, player, "qi_gathering_pill", amount=1, rng=random.Random(0))
    session.commit()
    assert res.success is True
    assert get_item_quantity(session, player.id, "qi_gathering_pill") >= 1
    assert get_item_quantity(session, player.id, "green_dew_herb") == 0


def test_dungeon_requires_key(session, player):
    player.realm_index = 1
    session.commit()
    res = run_dungeon(session, player, "blackwind", rng=random.Random(1))
    assert res.success is False
    assert res.outcome == "no_key"


def test_dungeon_with_key(session, player):
    player.realm_index = 1
    add_item(session, player.id, "blackwind_key", 1)
    session.commit()

    res = run_dungeon(session, player, "blackwind", rng=random.Random(99))
    session.commit()
    assert res.outcome in {"success", "fail"}
    assert get_item_quantity(session, player.id, "blackwind_key") == 0


def test_equip_affix_stone(session, player):
    from src.forge import forge_equipment

    add_item(session, player.id, "spirit_iron_shard", 2)
    add_item(session, player.id, "minor_beast_core", 1)
    add_item(session, player.id, "affix_stone", 1)
    session.commit()
    forge_res = forge_equipment(session, player.id, "weapon", rng=random.Random(1))
    assert forge_res.success is True
    ok, message, affix_id = apply_affix_stone(session, player.id, "weapon", rng=random.Random(1))
    session.commit()
    assert ok is True
    assert affix_id is not None
    assert get_item_quantity(session, player.id, "affix_stone") == 0


def test_use_qi_gathering_pill(session, player):
    add_item(session, player.id, "qi_gathering_pill", 1)
    session.commit()
    ok, message = use_item(session, player, "qi_gathering_pill", rng=random.Random(1))
    session.commit()
    assert ok is True
    mod = get_character_modifiers(session, player)
    assert "qi_gathering" in mod.active_effects


def test_wandering_elder_rare_event_applies_effect_not_item(session, player):
    from src.adventure import _apply_rare_event
    from src.content import RareEventDef, get_area

    area = get_area("bamboo_grove")
    assert area is not None
    event = RareEventDef(id="wandering_elder", weight=1, message="An elder nods.")
    drops: dict[str, int] = {}
    messages: list[str] = []

    _apply_rare_event(session, player, event, area, drops, messages, rng=random.Random(1))
    session.commit()

    assert "charges" not in drops
    mod = get_character_modifiers(session, player)
    assert "qi_gathering" in mod.active_effects
    assert any(k.startswith("manual_") for k in drops)
