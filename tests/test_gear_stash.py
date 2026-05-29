from __future__ import annotations

import random

import pytest

from src.content import load_all_content
from src.forge import forge_and_equip, forge_equipment_for_player
from src.gear_stash import equip_gear_item, list_stash, recycle_gear_item, stash_count
from src.inventory import add_item, get_item_quantity
from src.stats import get_total_equipment_stats


def test_forge_puts_gear_in_stash_not_worn(session, player):
    load_all_content()
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    res = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    assert res.success
    assert res.gear_item_id is not None
    session.commit()

    assert stash_count(session, player.id) == 1
    stats = get_total_equipment_stats(session, player.id, player_realm_index=player.realm_index)
    assert stats.power == 0


def test_equip_from_stash_applies_stats(session, player):
    load_all_content()
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    forged = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    assert forged.gear_item_id is not None
    equip_res = equip_gear_item(session, player.id, forged.gear_item_id)
    assert equip_res.success
    session.commit()

    stats = get_total_equipment_stats(session, player.id, player_realm_index=player.realm_index)
    assert stats.power > 0
    assert stash_count(session, player.id) == 0


def test_recycle_returns_spirit_stones(session, player):
    load_all_content()
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    forged = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    assert forged.gear_item_id is not None
    before_stones = player.spirit_stones
    recycle_res = recycle_gear_item(session, player, forged.gear_item_id)
    session.commit()

    assert recycle_res.success
    assert recycle_res.spirit_stones == 8
    assert player.spirit_stones == before_stones + 8
    assert stash_count(session, player.id) == 0


def test_recycle_returns_affix_stone_when_affixed(session, player):
    load_all_content()
    from src.equipment import apply_affix_stone

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    add_item(session, player.id, "affix_stone", 1)
    session.commit()

    forged = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    apply_affix_stone(session, player.id, forged.gear_item_id, rng=random.Random(2))
    session.commit()

    recycle_res = recycle_gear_item(session, player, forged.gear_item_id)
    session.commit()
    assert recycle_res.success
    assert recycle_res.affix_stones == 1
    assert get_item_quantity(session, player.id, "affix_stone") == 1


def test_forge_and_equip_helper(session, player):
    load_all_content()
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    res = forge_and_equip(session, player, "weapon", rng=random.Random(1))
    assert res.success
    stats = get_total_equipment_stats(session, player.id, player_realm_index=player.realm_index)
    assert stats.power > 0
