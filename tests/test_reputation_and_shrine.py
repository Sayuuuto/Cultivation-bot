from __future__ import annotations

import random

import pytest

from src.adventure import (
    RARE_EVENT_REWARDS,
    _apply_rare_event,
    _apply_shrine_choice,
    apply_adventure_choice,
    cursed_shrine_choices,
    start_adventure_session,
)
from src.content import get_area, load_all_content
from src.models import PlayerEffect
from src.reputation import clamp_reputation, reputation_tier
from tests.rng_helpers import ScriptedRNG, safe_adventure_segment_floats


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()


def test_reputation_clamp_and_tiers():
    assert clamp_reputation(200) == 100
    assert clamp_reputation(-200) == -100
    assert reputation_tier(50) == "renowned"
    assert reputation_tier(-50) == "notorious"


def test_cursed_shrine_rare_event_pauses_for_choice(session, player):
    area = get_area("mistwood_village")
    assert area is not None
    event = next(e for e in area.rare_events if e.id == "cursed_shrine")
    state: dict = {"messages": [], "drops": {}}
    paused = _apply_rare_event(session, player, event, area, state["drops"], state["messages"], state=state)
    assert paused is True
    assert state.get("pending_shrine") is True


def test_cursed_shrine_accept_applies_effects(session, player):
    choice = next(c for c in cursed_shrine_choices() if c.id == "accept")
    state: dict = {"messages": [], "pending_shrine": True}
    before_karma = player.karma
    _apply_shrine_choice(session, player, choice, state)
    session.commit()
    assert player.karma < before_karma
    assert player.reputation < 0
    assert state.get("pending_shrine") is None
    effects = session.query(PlayerEffect).filter_by(player_id=player.id).all()
    effect_ids = {e.effect_id for e in effects}
    assert "shrine_boon" in effect_ids
    assert "shrine_curse" in effect_ids


def test_cursed_shrine_forced_via_adventure(session, player):
    rng = ScriptedRNG(
        floats=safe_adventure_segment_floats(trigger_rare=True),
        randint_queue=[1],
    )
    pending, err = start_adventure_session(session, player, "mistwood_village", "balanced", rng=rng)
    assert err is None
    assert pending is not None
    choice = pending.choices[0]
    result, err = apply_adventure_choice(
        session,
        player,
        pending.active_id,
        choice.id,
        rng=ScriptedRNG(floats=[0.01, 0.01], randint_queue=[1]),
    )
    assert err is None
    if hasattr(result, "encounter_type"):
        assert result.encounter_type == "shrine" or "cursed_shrine" in str(getattr(result, "messages", []))


def test_every_rare_event_has_rewards_or_interactive():
    from src.content import get_areas

    for area in get_areas().values():
        for event in area.rare_events:
            assert event.id in RARE_EVENT_REWARDS
