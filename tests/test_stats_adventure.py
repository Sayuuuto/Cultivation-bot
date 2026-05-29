from __future__ import annotations

import random

import pytest

from src.adventure import apply_adventure_choice, start_adventure_session
from src.consumables import use_item
from src.content import load_all_content
from src.cooldown_haste import get_haste_reduction_seconds
from src.forge import forge_and_equip
from src.inventory import add_item, load_item_catalog
from src.stats import get_total_equipment_stats


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_forge_equipment_rolls_stats(session, player):
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    res = forge_and_equip(session, player, "weapon", grade="external", rng=random.Random(7))
    session.commit()
    assert res.success is True
    stats = get_total_equipment_stats(session, player.id, player_realm_index=player.realm_index)
    assert stats.power > 0


def test_flow_pill_grants_adventure_haste(session, player):
    add_item(session, player.id, "flow_pill", 1)
    session.commit()
    ok, _ = use_item(session, player, "flow_pill", rng=random.Random(1))
    session.commit()
    assert ok is True
    assert get_haste_reduction_seconds(session, player.id, "adventure") == 900


def test_interactive_adventure_choice_flow(session, player):
    from src.adventure import get_encounters_for_area
    from tests.rng_helpers import ScriptedRNG

    choice_encounters = [e for e in get_encounters_for_area("bamboo_grove") if e.encounter_type == "choice"]
    rng = ScriptedRNG(encounter_queue=[choice_encounters[0], choice_encounters[0]])
    pending, err = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    session.commit()
    assert err is None
    assert pending is not None
    assert pending.choices

    choice_id = pending.choices[0].id
    result, err = apply_adventure_choice(session, player, pending.active_id, choice_id, rng=rng)
    session.commit()
    assert err is None
    assert result is not None
