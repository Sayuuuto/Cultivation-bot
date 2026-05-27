from __future__ import annotations

import random

from src.auto_combat import BeastTemplate, resolve_auto_combat
from src.combat_stats import PlayerCombatStats


def _strong_stats() -> PlayerCombatStats:
    return PlayerCombatStats(
        hp=500,
        max_hp=500,
        internal_strength=80,
        external_strength=80,
        agility=40,
        spiritual_sense=30,
        defense=40,
        comprehension=20,
        luck=20,
        crit_chance=0.2,
        dodge=0.1,
    )


def _weak_beast() -> BeastTemplate:
    return BeastTemplate(beast_id="test_hare", name="Test Hare", hp=30, attack=5, defense=2)


def test_auto_combat_victory_against_weak_beast():
    result = resolve_auto_combat(_strong_stats(), _weak_beast(), rng=random.Random(42))
    assert result.victory is True
    assert result.beast_hp_remaining <= 0
    assert len(result.log_lines) >= 1


def test_auto_combat_loss_against_overwhelming_beast():
    stats = PlayerCombatStats(
        hp=20,
        max_hp=20,
        internal_strength=5,
        external_strength=5,
        agility=5,
        spiritual_sense=5,
        defense=2,
        comprehension=5,
        luck=5,
        crit_chance=0.0,
        dodge=0.0,
    )
    beast = BeastTemplate(beast_id="boss", name="Ruin Devourer", hp=500, attack=50, defense=20)
    result = resolve_auto_combat(stats, beast, rng=random.Random(1))
    assert result.victory is False
