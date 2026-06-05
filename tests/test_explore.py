from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from src.explore import (
    EXPLORE_COOLDOWN_HOURS,
    ExploreArea,
    ExploreChoice,
    ExploreEncounter,
    ExploreState,
    StepResult,
    _deserialize_state,
    _serialize_state,
    calculate_affinity,
    check_explore_cooldown,
    generate_encounter_sequence,
    get_active_explore,
    load_explore_areas,
    resolve_stat_check,
    start_explore,
)
from src.models import ExploreSession, Player


def test_explore_areas_loaded():
    areas = load_explore_areas()
    assert "mistwood_village_outskirts" in areas
    assert "heavenfall_peak" in areas
    assert len(areas) == 10


def test_calculate_affinity_root_match(player):
    player.spirit_root = "Pure Jade Root"
    area = ExploreArea(
        area_id="test", name="Test", theme="",
        danger=1.0, reward_multiplier=1.0,
        roots=["jade"], paths=[],
        manual_bias=[], pill_bias=[],
    )
    aff = calculate_affinity(player, area)
    assert aff == 1.15


def test_calculate_affinity_no_match(player):
    player.spirit_root = "Flame Ember Root"
    player.moral_path = "righteous"
    area = ExploreArea(
        area_id="test", name="Test", theme="",
        danger=1.0, reward_multiplier=1.0,
        roots=["jade"], paths=["demonic"],
        manual_bias=[], pill_bias=[],
    )
    aff = calculate_affinity(player, area)
    assert aff == 1.0


def test_calculate_affinity_capped_at_135(player):
    player.spirit_root = "Moonlit Sword Root"
    player.moral_path = "neutral"
    area = ExploreArea(
        area_id="test", name="Test", theme="",
        danger=1.0, reward_multiplier=1.0,
        roots=["moon", "sword"], paths=["neutral"],
        manual_bias=[], pill_bias=[],
    )
    aff = calculate_affinity(player, area)
    assert aff <= 1.35


def test_state_serialization_roundtrip():
    state = ExploreState(
        current_hp=150,
        max_hp=200,
        rewards=[{"item_id": "spirit_stones", "quantity": 10, "tier": "common"}],
        flags={"dao_triggered": True},
        encounter_sequence=["a", "b", "c"],
    )
    serialized = _serialize_state(state)
    deserialized = _deserialize_state(type("Row", (), {"state_json": serialized})())
    assert deserialized.current_hp == 150
    assert deserialized.max_hp == 200
    assert len(deserialized.rewards) == 1
    assert deserialized.rewards[0]["item_id"] == "spirit_stones"
    assert deserialized.flags["dao_triggered"] is True
    assert deserialized.encounter_sequence == ["a", "b", "c"]


def test_generate_encounter_sequence():
    area = ExploreArea(
        area_id="mistwood_village_outskirts", name="Mistwood", theme="",
        danger=0.85, reward_multiplier=0.9,
        roots=["wood", "water"], paths=["internal", "hp"],
        manual_bias=["utility", "body"], pill_bias=["healing", "qi"],
    )
    seq = generate_encounter_sequence(area, 10)
    assert len(seq) <= 10
    assert len(seq) > 0


def test_check_explore_cooldown_fresh(player):
    player.last_explore_at = None
    on_cooldown, msg = check_explore_cooldown(player)
    assert on_cooldown is False
    assert msg == ""


def test_check_explore_cooldown_active(player):
    from datetime import timedelta
    player.last_explore_at = datetime.now(timezone.utc) - timedelta(hours=1)
    on_cooldown, msg = check_explore_cooldown(player)
    assert on_cooldown is True
    assert "Cooldown" in msg


def test_check_explore_cooldown_expired(player):
    from datetime import timedelta
    player.last_explore_at = datetime.now(timezone.utc) - timedelta(hours=EXPLORE_COOLDOWN_HOURS + 1)
    on_cooldown, msg = check_explore_cooldown(player)
    assert on_cooldown is False


def test_resolve_stat_check_baseline(session, player):
    state = ExploreState(current_hp=200, max_hp=200)
    area = load_explore_areas().get("mistwood_village_outskirts")

    success, total = resolve_stat_check(
        player, session, state,
        ["agility", "speed"], 1,
        area=area,
    )
    assert total >= 1
    assert isinstance(success, bool)


def test_start_explore_creates_session(session, player):
    row, err = start_explore(session, player, "test-guild", "mistwood_village_outskirts")
    assert err == ""
    assert row is not None
    assert row.area_id == "mistwood_village_outskirts"
    assert row.current_step == 0
    assert row.completed is False
    assert row.failed is False


def test_start_explore_unknown_area(session, player):
    row, err = start_explore(session, player, "test-guild", "nonexistent_area")
    assert err != ""
    assert row is None


def test_get_active_explore_returns_session(session, player):
    row, err = start_explore(session, player, "test-guild", "mistwood_village_outskirts")
    session.commit()

    active = get_active_explore(session, "test-guild", player.discord_id)
    assert active is not None
    assert active.id == row.id


def test_get_active_explore_none_when_completed(session, player):
    row, err = start_explore(session, player, "test-guild", "mistwood_village_outskirts")
    row.completed = True
    session.commit()

    active = get_active_explore(session, "test-guild", player.discord_id)
    assert active is None


def test_start_explore_sets_current_hp(session, player):
    player.current_hp = None
    session.commit()

    row, err = start_explore(session, player, "test-guild", "mistwood_village_outskirts")
    assert player.current_hp is not None
    assert player.current_hp > 0
    assert player.last_explore_at is not None


def test_start_explore_uses_existing_current_hp(session, player):
    player.current_hp = 42
    session.commit()

    row, err = start_explore(session, player, "test-guild", "mistwood_village_outskirts")
    state = _deserialize_state(row)
    assert state.current_hp == 42
