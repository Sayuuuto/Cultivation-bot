from __future__ import annotations

import random

from src.combat_stats import compute_combat_stats
from src.foundation import (
    apply_foundation_bonuses,
    get_body_bonuses,
    grant_meridian_points,
    spend_meridian_point,
    temper_body,
)
from src.inventory import add_item
from src.models import Player


def test_body_temper_increases_combat_stat(session, player):
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 3)
    before = compute_combat_stats(player, session).external_strength

    res = temper_body(player, "external_strength", session=session, player_id=player.id)
    assert res.success
    after = compute_combat_stats(player, session).external_strength
    assert after == before + 1
    assert get_body_bonuses(player)["external_strength"] == 1


def test_body_temper_respects_realm_cap(session, player):
    player.realm_index = 0
    for _ in range(10):
        player.foundation_body_json = '{"external_strength": 5}'
        res = temper_body(
            player,
            "external_strength",
            session=session,
            player_id=player.id,
            use_charge=True,
        )
        player.body_temper_charges = 1
    assert not res.success
    assert "limit" in res.message.lower()


def test_meridian_spend_costs_points(session, player):
    player.meridian_points = 2
    res = spend_meridian_point(player, "internal_strength")
    assert res.success
    assert player.meridian_points == 0


def test_foundation_hp_stacks(session, player):
    bonuses = {"hp": 2}
    player.foundation_body_json = '{"hp": 2}'
    stats = {"hp": 100, "internal_strength": 10, "external_strength": 10}
    apply_foundation_bonuses(player, stats)
    assert stats["hp"] == 116


def test_grant_meridian_points(session, player):
    msg = grant_meridian_points(player, 2)
    assert player.meridian_points == 2
    assert "2" in msg


def test_cultivate_event_meridian_points(session, player):
    from src.cultivate_events import roll_cultivate_event

    event = roll_cultivate_event(random.Random(1), force_event_id="meridian_awakening")
    assert event is not None
    assert event.meridian_points == 1
