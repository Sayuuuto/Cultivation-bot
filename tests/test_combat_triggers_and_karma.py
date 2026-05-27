from __future__ import annotations

import random

import pytest

from src.combat.catalog import get_technique, invalidate_technique_catalog_cache, load_technique_catalog
from src.combat.effects import apply_status, has_status
from src.combat.engine import create_combat_state, execute_turn, opponent_from_beast
from src.combat.loadout import learn_technique
from src.combat.triggers import resolve_technique
from src.combat_stats import PlayerCombatStats
from src.auto_combat import BeastTemplate
from src.karma import (
    clamp_karma,
    karma_breakthrough_modifiers,
    karma_tier,
    karma_tier_label,
    manual_weight_multiplier,
)


def _stats(**overrides) -> PlayerCombatStats:
    base = dict(
        hp=120,
        max_hp=120,
        internal_strength=30,
        external_strength=30,
        agility=20,
        spiritual_sense=15,
        defense=15,
        comprehension=10,
        luck=10,
        crit_chance=0.15,
        dodge=0.1,
    )
    base.update(overrides)
    return PlayerCombatStats(**base)


@pytest.fixture(autouse=True)
def reload_techniques():
    invalidate_technique_catalog_cache()
    yield
    invalidate_technique_catalog_cache()


def test_technique_catalog_expanded():
    catalog = load_technique_catalog()
    assert len(catalog) >= 24
    assert get_technique("sanguine_drain") is not None
    assert get_technique("hemorrhage_art") is not None


def test_karma_tiers():
    assert karma_tier(40) == "righteous"
    assert karma_tier(-40) == "demonic"
    assert karma_tier(0) == "neutral"
    assert "Righteous" in karma_tier_label(45)


def test_karma_clamp():
    assert clamp_karma(200) == 100
    assert clamp_karma(-200) == -100


def test_manual_weight_multiplier_favors_alignment():
    assert manual_weight_multiplier(50, "righteous") > manual_weight_multiplier(50, "demonic")
    assert manual_weight_multiplier(-50, "demonic") > manual_weight_multiplier(-50, "righteous")


def test_sanguine_drain_requires_bleed(session, player):
    learn_technique(session, player.id, "sanguine_drain")
    session.commit()
    stats = _stats()
    beast = BeastTemplate("hare", "Hare", hp=80, attack=5, defense=2)

    state = create_combat_state(stats, opponent_from_beast(beast))
    resolve_technique(state, stats, None, "sanguine_drain", random.Random(1))
    assert any("bleed" in line.lower() for line in state.log)

    state2 = create_combat_state(stats, opponent_from_beast(beast))
    apply_status(state2.opponent, "bleed")
    hp_before = state2.player.hp
    resolve_technique(state2, stats, None, "sanguine_drain", random.Random(2))
    assert state2.player.hp >= hp_before
    assert has_status(state2.opponent, "bleed")


def test_karma_breakthrough_modifiers_scale():
    righteous_bonus, righteous_setback = karma_breakthrough_modifiers(50)
    demonic_bonus, demonic_setback = karma_breakthrough_modifiers(-50)
    assert righteous_bonus > demonic_bonus
    assert demonic_setback > righteous_setback


def test_breakthrough_pool_for_karma():
    from src.manuals import breakthrough_pool_for_karma

    assert breakthrough_pool_for_karma(40, realm_index=1) == "righteous_breakthrough"
    assert breakthrough_pool_for_karma(-40, realm_index=1) == "demonic_breakthrough"
    assert breakthrough_pool_for_karma(0, realm_index=1) == "breakthrough_success"
    assert breakthrough_pool_for_karma(0, realm_index=0) == "cultivate_enlightenment"
