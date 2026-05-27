from __future__ import annotations

import random

import pytest

from src.consumables import resolve_use_item_id, use_item
from src.content import load_all_content
from src.effects import format_active_effects_block
from src.inventory import add_item, load_item_catalog


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("qi_gathering_pill", "qi_gathering_pill"),
        ("Qi Gathering Pill", "qi_gathering_pill"),
        ("qi gathering pill", "qi_gathering_pill"),
        ("flow pill", "flow_pill"),
        ("Flow Meridian Pill", "flow_pill"),
        ("root reforging", "root_reforging_pill"),
    ],
)
def test_resolve_use_item_id_friendly_names(raw, expected):
    assert resolve_use_item_id(raw) == expected


def test_resolve_use_item_id_unknown():
    assert resolve_use_item_id("green_dew_herb") is None
    assert resolve_use_item_id("not a pill") is None


def test_use_item_accepts_display_name(session, player):
    add_item(session, player.id, "qi_gathering_pill", 1)
    session.commit()
    ok, message = use_item(session, player, "Qi Gathering Pill", rng=random.Random(1))
    session.commit()
    assert ok is True
    assert "Qi Gathering" in message
    assert "+55%" in message
    assert "3" in message


def test_use_tempering_pill_describes_effect(session, player):
    add_item(session, player.id, "tempering_pill", 1)
    session.commit()
    ok, message = use_item(session, player, "tempering_pill", rng=random.Random(1))
    session.commit()
    assert ok is True
    assert "+12% defense" in message
    assert "/adventure" in message


def test_format_active_effects_block(session, player):
    add_item(session, player.id, "swiftwind_pill", 1)
    session.commit()
    use_item(session, player, "swiftwind_pill", rng=random.Random(1))
    session.commit()
    block = format_active_effects_block(session, player.id)
    assert block is not None
    assert "Swiftwind" in block
    assert "+10%" in block
