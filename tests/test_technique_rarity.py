from __future__ import annotations

import random

import pytest

from src.combat.catalog import get_technique, invalidate_technique_catalog_cache, load_technique_catalog
from src.combat.rarity import RARITY_DAMAGE_MULT, rarity_at_most, rarity_rank
from src.combat.triggers import _compute_base_damage
from src.combat_stats import PlayerCombatStats
from src.manuals import load_manual_pools, pick_manual_from_pool, roll_shop_unidentified_manual


@pytest.fixture(autouse=True)
def reload_catalog():
    invalidate_technique_catalog_cache()
    load_manual_pools()
    yield
    invalidate_technique_catalog_cache()


def test_every_technique_has_rarity():
    catalog = load_technique_catalog()
    assert len(catalog) >= 29
    for tech in catalog.values():
        assert tech.rarity in RARITY_DAMAGE_MULT
        assert tech.rarity in {"common", "uncommon", "rare", "legendary"}


def test_legendary_power_exceeds_common():
    common = get_technique("swift_slash")
    legendary = get_technique("heavens_cleave")
    assert common is not None and legendary is not None

    stats = PlayerCombatStats(
        hp=100,
        max_hp=100,
        internal_strength=20,
        external_strength=20,
        agility=10,
        spiritual_sense=10,
        defense=5,
        comprehension=10,
        luck=10,
        crit_chance=0.05,
        dodge=0.05,
    )
    common_dmg = _compute_base_damage(common, stats, 5, None, crit=False)
    legendary_dmg = _compute_base_damage(legendary, stats, 5, None, crit=False)
    assert legendary_dmg > common_dmg * 1.5


def test_shop_gamble_never_grants_rare_or_legendary(session, player):
    legendary_and_rare = {
        tech.manual_item_id
        for tech in load_technique_catalog().values()
        if tech.manual_item_id and not rarity_at_most(tech.rarity, "uncommon")
    }
    assert legendary_and_rare

    seen: set[str] = set()
    for seed in range(300):
        manual_id, _ = roll_shop_unidentified_manual(session, player, random.Random(seed))
        if manual_id:
            seen.add(manual_id)
            assert manual_id not in legendary_and_rare
    assert seen  # at least one roll succeeded


def test_shop_pool_excludes_legendary_entries():
    from src.combat.catalog import get_technique_by_manual

    pool = load_manual_pools()["shop_unidentified"]
    for item_id, _weight in pool:
        tech = get_technique_by_manual(item_id)
        assert tech is not None
        assert rarity_rank(tech.rarity) <= rarity_rank("uncommon")


def test_pick_manual_respects_max_rarity(session, player):
    manual_id = pick_manual_from_pool(
        "dungeon_earth",
        random.Random(1),
        session=session,
        player_id=player.id,
        max_rarity="uncommon",
    )
    if manual_id is not None:
        from src.combat.catalog import get_technique_by_manual

        tech = get_technique_by_manual(manual_id)
        assert tech is not None
        assert rarity_rank(tech.rarity) <= rarity_rank("uncommon")


def test_new_techniques_exist():
    assert get_technique("venom_weave") is not None
    assert get_technique("heavens_cleave") is not None
    assert get_technique("heavens_cleave").rarity == "legendary"
    assert get_technique("venom_weave").rarity == "rare"
