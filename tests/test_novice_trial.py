from __future__ import annotations

import random

import pytest

from src.adventure import start_adventure_session
from src.game import cultivate, qi_cap
from src.models import Player
from src.novice_trial import (
    NOVICE_MORTAL_EARLY_CAP,
    SAGE_TRIAL_ENCOUNTER,
    apply_novice_cultivate_boost,
    apply_origin_starter_gifts,
    get_origin_starter_gift,
    is_first_adventure,
    on_breakthrough_success,
    on_cultivated,
    on_daily_claimed,
    on_hunt_victory,
    trial_complete,
)
from src.combat.loadout import get_learned_technique_ids, learn_technique
from tests.conftest import cfg


def _novice_player(session, **overrides) -> Player:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    data = dict(
        guild_id="g1",
        discord_id="u1",
        discord_username="Novice",
        dao_name="NoviceDao",
        origin="Mountain Rises",
        spirit_root="Pure Jade Root",
        karma=0,
        novice_trial_step=0,
        novice_cultivates=0,
        adventures_completed=0,
        realm_index=0,
        substage=0,
        qi=0,
        spirit_stones=0,
        last_active_at=now,
    )
    data.update(overrides)
    player = Player(**data)
    session.add(player)
    session.commit()
    session.refresh(player)
    return player


def test_novice_mortal_early_qi_cap(session):
    player = _novice_player(session)
    assert qi_cap(0, 0, player) == NOVICE_MORTAL_EARLY_CAP
    player.novice_trial_step = 6
    assert qi_cap(0, 0, player) == 100


def test_origin_starter_gifts_applied(session):
    player = _novice_player(session, origin="River Dragon\u2019s Gift")
    gift = get_origin_starter_gift(player.origin)
    assert gift is not None
    msgs = apply_origin_starter_gifts(session, player)
    assert player.spirit_stones >= 8
    assert msgs
    session.commit()


def test_trial_daily_and_cultivate_steps(session):
    player = _novice_player(session)
    daily_msgs = on_daily_claimed(player)
    assert player.novice_trial_step == 1
    assert player.spirit_stones == 5
    assert daily_msgs

    cult_msgs = on_cultivated(player)
    assert player.novice_trial_step == 2
    assert player.novice_cultivates == 1
    assert cult_msgs


def test_novice_cultivate_boost(session):
    player = _novice_player(session)
    boosted = apply_novice_cultivate_boost(player, 10)
    assert boosted == 15
    player.novice_cultivates = 3
    assert apply_novice_cultivate_boost(player, 10) == 10


def test_first_adventure_uses_sage_encounter(session):
    player = _novice_player(session)
    assert is_first_adventure(player)
    pending, err = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=random.Random(1))
    assert err is None
    assert pending is not None
    assert pending.prompt.startswith("A white-robed")


def test_sage_encounter_has_no_catastrophic_fail():
    for choice in SAGE_TRIAL_ENCOUNTER.choices:
        assert choice.fail_chance == 0.0


def test_trial_breakthrough_completion_reward(session):
    player = _novice_player(session, novice_trial_step=5, qi=60)
    msgs = on_breakthrough_success(session, player, random.Random(1))
    assert trial_complete(player)
    assert player.spirit_stones >= 15
    assert msgs


def test_learn_second_technique_advances_trial(session):
    player = _novice_player(session, novice_trial_step=3)
    learn_technique(session, player.id, "basic_strike")
    ok, msg = learn_technique(session, player.id, "swift_slash")
    assert ok
    assert player.novice_trial_step == 4
    assert "Step 4" in msg


def test_first_cultivate_forces_meridian_event(session, player):
    from src.cultivate_events import roll_cultivate_event
    from src.novice_trial import should_force_first_cultivate_event

    player.novice_trial_step = 0
    player.novice_cultivates = 0
    assert should_force_first_cultivate_event(player)
    event = roll_cultivate_event(random.Random(99), force_event_id="meridian_awakening")
    assert event is not None
    assert event.event_id == "meridian_awakening"


def test_hunt_victory_advances_trial(session):
    player = _novice_player(session, novice_trial_step=2)
    msgs = on_hunt_victory(player)
    assert player.novice_trial_step == 3
    assert msgs


def test_failed_first_adventure_does_not_block_sage(session):
    from src.adventure import SEGMENTS_PER_RUN
    from src.novice_trial import (
        heal_stuck_novice_adventure,
        on_adventure_completed,
        requires_sage_trial,
    )

    player = _novice_player(session, novice_trial_step=4, adventures_completed=0)
    on_adventure_completed(session, player, segments_cleared=0)
    assert player.adventures_completed == 0
    assert player.novice_trial_step == 4
    assert requires_sage_trial(player)

    player.adventures_completed = 1
    assert requires_sage_trial(player)
    assert heal_stuck_novice_adventure(player)
    assert player.adventures_completed == 0

    player.novice_trial_step = 4
    player.adventures_completed = 0
    msgs, waive = on_adventure_completed(session, player, segments_cleared=SEGMENTS_PER_RUN)
    assert player.adventures_completed == 1
    assert player.novice_trial_step == 5
    assert waive
    assert msgs
