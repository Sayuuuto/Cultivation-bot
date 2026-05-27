from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from src.inventory import (
    add_item,
    build_inventory_embed,
    format_inventory_embed,
    get_item_def,
    get_item_quantity,
    get_player_inventory,
    has_items,
    load_item_catalog,
    remove_item,
)
from src.models import Player


def test_item_catalog_loads_all_phase1_items():
    catalog = load_item_catalog()
    expected = {
        "green_dew_herb",
        "bamboo_resin",
        "minor_beast_core",
        "ember_moss",
        "spirit_iron_shard",
        "bandit_token",
        "moonlotus",
        "ancient_dust",
        "refined_beast_core",
        "blackwind_key",
        "affix_stone",
        "pill_ash",
        "qi_gathering_pill",
        "tempering_pill",
        "clarity_pill",
        "swiftwind_pill",
        "blood_ember_pill",
        "moonwell_tonic",
    }
    assert expected.issubset(set(catalog.keys()))
    herb = get_item_def("green_dew_herb")
    assert herb is not None
    assert herb.name == "Green Dew Herb"
    assert herb.category == "material"


def test_add_and_remove_items(session: Session, player: Player):
    add_item(session, player.id, "green_dew_herb", 5)
    add_item(session, player.id, "blackwind_key", 1)
    session.commit()

    assert get_item_quantity(session, player.id, "green_dew_herb") == 5
    assert get_item_quantity(session, player.id, "blackwind_key") == 1

    stacks = get_player_inventory(session, player.id)
    assert len(stacks) == 2
    assert {s.item_id for s in stacks} == {"blackwind_key", "green_dew_herb"}


def test_remove_item_insufficient_returns_false(session: Session, player: Player):
    add_item(session, player.id, "bamboo_resin", 2)
    session.commit()

    assert remove_item(session, player.id, "bamboo_resin", 3) is False
    assert get_item_quantity(session, player.id, "bamboo_resin") == 2

    assert remove_item(session, player.id, "bamboo_resin", 2) is True
    session.commit()
    assert get_item_quantity(session, player.id, "bamboo_resin") == 0


def test_has_items(session: Session, player: Player):
    add_item(session, player.id, "spirit_iron_shard", 3)
    add_item(session, player.id, "ancient_dust", 2)
    session.commit()

    assert has_items(session, player.id, {"spirit_iron_shard": 3, "ancient_dust": 2})
    assert not has_items(session, player.id, {"spirit_iron_shard": 4})


def test_add_unknown_item_raises(session: Session, player: Player):
    with pytest.raises(ValueError, match="unknown item_id"):
        add_item(session, player.id, "not_a_real_item", 1)


def test_build_inventory_empty(player: Player):
    embed = build_inventory_embed(player, [])
    assert "Storage Ring" in embed.title
    assert "empty" in (embed.description or "").lower()


def test_build_inventory_names_only_grouped(session: Session, player: Player):
    add_item(session, player.id, "green_dew_herb", 3)
    add_item(session, player.id, "qi_gathering_pill", 1)
    add_item(session, player.id, "blackwind_key", 2)
    session.commit()

    stacks = get_player_inventory(session, player.id)
    embed = build_inventory_embed(player, stacks)
    field_text = "\n".join(f.value for f in embed.fields)

    assert any("Materials" in f.name for f in embed.fields)
    assert any("Pills" in f.name for f in embed.fields)
    assert any("Keys" in f.name for f in embed.fields)
    assert "Green Dew Herb" in field_text
    assert "×3" in field_text
    assert "Qi Gathering Pill" in field_text
    assert "Blackwind Key" in field_text
    assert "Manual binding" not in field_text
    assert "/craft" not in field_text


def test_format_inventory_embed_legacy(player: Player):
    title, description = format_inventory_embed(player, [])
    assert "TestDao" in title or "Storage Ring" in title
    assert "empty" in description.lower()
