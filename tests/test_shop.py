from __future__ import annotations

import pytest

from src.config import Config
from src.equipment import get_player_equipment
from src.inventory import get_item_quantity
from src.shop import buy_from_shop, load_shop_catalog, resolve_shop_id


@pytest.fixture
def cfg() -> Config:
    return Config(
        discord_token="test",
        guild_id="test-guild",
        database_path=":memory:",
        announce_channel_id=None,
        tutorial_channel_id=None,
        library_channel_id=None,
    )


def test_shop_catalog_loads(session):
    catalog = load_shop_catalog()
    assert "void_pulse_pill" in catalog
    assert "shop_spirit_blade" in catalog
    assert catalog["void_pulse_pill"].listing_type == "item"
    assert catalog["shop_spirit_blade"].listing_type == "equipment"


def test_resolve_shop_id_by_name(session):
    assert resolve_shop_id("Void Pulse Pill") == "void_pulse_pill"


def test_buy_pill_deducts_stones(session, player, cfg):
    player.spirit_stones = 100
    session.add(player)
    session.commit()

    ok, message = buy_from_shop(session, player, "flow_pill", 1)
    assert ok is True
    assert player.spirit_stones == 70
    assert get_item_quantity(session, player.id, "flow_pill") == 1
    assert "70" in message or "Balance" in message


def test_buy_insufficient_stones(session, player, cfg):
    player.spirit_stones = 5
    session.add(player)
    session.commit()

    ok, message = buy_from_shop(session, player, "void_pulse_pill", 1)
    assert ok is False
    assert "need" in message.lower()


def test_buy_equipment_equips_slot(session, player, cfg):
    player.spirit_stones = 500
    session.add(player)
    session.commit()

    ok, _ = buy_from_shop(session, player, "shop_spirit_blade", 1)
    assert ok is True

    rows = {eq.slot: eq for eq in get_player_equipment(session, player.id)}
    assert rows["weapon"].item_id == "spirit_blade"
    assert rows["weapon"].stat_power == 6
