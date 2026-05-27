from __future__ import annotations

import random

from src.loot import (
    LootDropEntry,
    effective_drop_chance,
    parse_loot_drop,
    roll_creature_loot,
)


def test_effective_drop_chance_scales_with_luck_and_tier():
    base = effective_drop_chance("common", combat_tier="normal", luck=0, drop_luck=0)
    lucky = effective_drop_chance("common", combat_tier="normal", luck=20, drop_luck=0.1)
    elite = effective_drop_chance("common", combat_tier="elite", luck=0, drop_luck=0)
    assert lucky > base
    assert elite > base


def test_creature_loot_varies_by_seed():
    table = (
        LootDropEntry("minor_beast_core", "common", 1, 1),
        LootDropEntry("technique_fragment", "legendary", 1, 1),
    )

    def loot_set(seed: int) -> frozenset[str]:
        return frozenset(
            roll_creature_loot(table, random.Random(seed), combat_tier="normal", luck=15).keys()
        )

    seen = {loot_set(seed) for seed in range(80)}
    assert len(seen) >= 2


def test_legacy_weight_maps_to_rarity():
    entry = parse_loot_drop({"item_id": "herb", "weight": 50, "min": 1, "max": 2})
    assert entry is not None
    assert entry.rarity == "common"
