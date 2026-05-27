from __future__ import annotations

import random

from src.adventure import start_adventure_session
from src.area_risk import adventure_realm_modifiers, realm_gap, underleveled_drop_bonus
from src.command_choices import list_unlocked_areas
from src.content import get_area
from src.hunt import run_hunt, start_hunt_combat


def test_list_unlocked_areas_includes_higher_realm_zones(session, player):
    player.realm_index = 0
    session.commit()
    areas = {area_id: label for area_id, label in list_unlocked_areas(player)}
    assert "bamboo_grove" in areas
    assert "moonwell_ruins" in areas
    assert "deadly" in areas["moonwell_ruins"].lower()


def test_mortal_can_start_hunt_in_deadly_zone(session, player):
    player.realm_index = 0
    session.commit()

    pending, err = start_hunt_combat(session, player, "moonwell_ruins")
    assert err is None
    assert pending is not None
    assert pending.area_name == "Moonwell Ruins"


def test_mortal_hunt_in_moonwell_usually_fails(session, player):
    player.realm_index = 0
    session.commit()

    wins = 0
    for seed in range(50):
        res = run_hunt(session, player, "moonwell_ruins", rng=random.Random(seed))
        if res.success:
            wins += 1
    assert wins == 0


def test_underleveled_hunt_victory_grants_bonus_drops(session, player, monkeypatch):
    player.realm_index = 0
    session.commit()

    def fake_auto_combat(stats, beast, mod=None, rng=None):
        from src.auto_combat import AutoCombatResult

        return AutoCombatResult(
            victory=True,
            beast_name=beast.name,
            rounds_fought=1,
            player_hp_remaining=50,
            player_hp_start=stats.hp,
            beast_hp_remaining=0,
            beast_hp_start=beast.hp,
            log_lines=["Forced victory for test."],
            message="Victory.",
        )

    monkeypatch.setattr("src.auto_combat.resolve_auto_combat", fake_auto_combat)

    res = run_hunt(session, player, "ashen_cliff", rng=random.Random(1))
    assert res.success is True
    assert res.drops
    assert any("spoils for your daring" in m.lower() for m in res.messages)


def test_mortal_can_start_adventure_in_deadly_zone(session, player):
    player.realm_index = 0
    session.commit()

    pending, err = start_adventure_session(session, player, "moonwell_ruins", "balanced")
    assert err is None
    assert pending is not None


def test_adventure_realm_gap_reduces_success_floor():
    penalty, min_chance = adventure_realm_modifiers(2)
    assert penalty >= 0.40
    assert min_chance <= 0.05


def test_underleveled_drop_bonus_scales_with_gap():
    assert underleveled_drop_bonus(0) == 1.0
    assert underleveled_drop_bonus(1) > 1.0
    assert underleveled_drop_bonus(2) > underleveled_drop_bonus(1)


def test_realm_gap_for_mortal_in_moonwell(player):
    area = get_area("moonwell_ruins")
    assert area is not None
    player.realm_index = 0
    assert realm_gap(player, area) == 2
