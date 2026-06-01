from __future__ import annotations

import random

import pytest

from src.combat_stats import compute_combat_stats, gear_combat_contribution
from src.content import get_recipe, load_all_content
from src.equipment_tiers import (
    GEAR_PATHS,
    _catalog_index,
    gear_is_active,
    normalize_gear_path,
    resolve_gear_entry,
    roll_forge_rarity,
)
from src.forge import forge_and_equip, forge_equipment_for_player
from src.foundation import apply_foundation_bonuses, body_stack_value
from src.inventory import add_item
from src.models import PlayerEquipment, PlayerGearItem
from src.pill_recipes import resolve_recipe_inputs
from src.stats import EquipmentStats, get_total_equipment_stats, stats_from_gear_view


EXTERNAL_ARMOR_COMMON_ANCHORS = {
    0: {"power": [5, 15], "defense": [15, 50], "fortune": [1, 5], "insight": [1, 5]},
    1: {"power": [50, 150], "defense": [150, 500], "fortune": [10, 50], "insight": [10, 50]},
    3: {"power": [500, 1500], "defense": [1500, 5000], "fortune": [100, 500], "insight": [100, 500]},
}


def test_catalog_has_360_unique_keys():
    load_all_content()
    index = _catalog_index()
    assert len(index) == 360
    paths = {key[2] for key in index}
    assert paths == set(GEAR_PATHS)


@pytest.mark.parametrize("realm_index", [0, 1, 3])
def test_external_armor_common_matches_anchors(realm_index):
    load_all_content()
    entry = resolve_gear_entry(realm_index, "armor", "external", "common")
    assert entry is not None
    assert entry.stat_ranges == EXTERNAL_ARMOR_COMMON_ANCHORS[realm_index]


def test_higher_realm_ranges_exceed_lower_realm():
    load_all_content()
    low = resolve_gear_entry(0, "weapon", "external", "common")
    high = resolve_gear_entry(4, "weapon", "external", "common")
    assert low is not None and high is not None
    assert high.stat_ranges["power"][1] > low.stat_ranges["power"][1]
    assert high.stat_ranges["defense"][1] > low.stat_ranges["defense"][1]


def test_forge_paths_have_distinct_stat_profiles():
    load_all_content()
    internal = resolve_gear_entry(0, "weapon", "internal", "common")
    external = resolve_gear_entry(0, "weapon", "external", "common")
    hp = resolve_gear_entry(0, "accessory", "hp", "common")
    assert internal is not None and external is not None and hp is not None
    assert external.stat_ranges["power"][1] > internal.stat_ranges["power"][1]
    assert hp.stat_ranges.get("hp", [0, 0])[1] > external.stat_ranges["fortune"][1]


def test_legacy_grade_aliases_map_to_paths():
    load_all_content()
    assert normalize_gear_path("fine") == "external"
    assert normalize_gear_path("common") == "internal"
    assert normalize_gear_path("exalted") == "hp"
    assert normalize_gear_path("crit") == "hp"


def test_roll_forge_rarity_respects_weights():
    load_all_content()
    rng = random.Random(42)
    counts = {"common": 0, "rare": 0, "legendary": 0}
    for _ in range(1000):
        counts[roll_forge_rarity(rng)] += 1
    assert counts["common"] > counts["rare"] > counts["legendary"]
    assert counts["legendary"] >= 1


def test_forge_stores_rarity_and_stats(session, player):
    load_all_content()
    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    res = forge_equipment_for_player(session, player, "weapon", grade="external", rng=random.Random(1))
    assert res.success
    assert res.rarity in {"common", "rare", "legendary"}
    assert res.gear_item_id is not None
    item = session.get(PlayerGearItem, res.gear_item_id)
    assert item is not None
    assert item.gear_rarity == res.rarity


def test_external_forge_applies_one_to_one_power(session, player):
    load_all_content()
    from src.gear_stash import equip_gear_item

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    forged = forge_equipment_for_player(session, player, "weapon", grade="external", rng=random.Random(1))
    assert forged.gear_item_id is not None
    item = session.get(PlayerGearItem, forged.gear_item_id)
    equip_gear_item(session, player.id, forged.gear_item_id)
    session.commit()

    gear = stats_from_gear_view(item)
    contrib = gear_combat_contribution(gear, "external")
    assert contrib["external_strength"] == item.stat_power
    assert contrib["internal_strength"] == 0


def test_outgrown_gear_does_not_apply_to_combat(session, player):
    load_all_content()
    from src.equipment import get_player_equipment

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    res = forge_and_equip(session, player, "weapon", grade="external", rng=random.Random(1))
    assert res.success
    session.commit()

    active_stats = get_total_equipment_stats(session, player.id, player_realm_index=0)
    assert active_stats.power > 0

    player.realm_index = 2
    session.commit()
    inactive_stats = get_total_equipment_stats(session, player.id, player_realm_index=2)
    assert inactive_stats.power == 0
    rows = {eq.slot: eq for eq in get_player_equipment(session, player.id)}
    assert rows["weapon"].gear_realm == 0


def test_gear_is_active_only_at_matching_realm():
    eq = PlayerEquipment(slot="weapon", item_id="spirit_blade", gear_realm=0, gear_grade="external")
    assert gear_is_active(eq, 0)
    assert not gear_is_active(eq, 2)


@pytest.mark.parametrize("realm_index", [0, 1, 4, 9])
def test_foundation_stack_values_scale(realm_index):
    load_all_content()
    low = body_stack_value("external_strength", 0)
    high = body_stack_value("external_strength", realm_index)
    if realm_index == 0:
        assert high == low
    else:
        assert high > low


def test_foundation_hp_stacks(session, player):
    load_all_content()
    player.foundation_body_json = '{"hp": 2}'
    stats = {"hp": 100, "internal_strength": 10, "external_strength": 10}
    apply_foundation_bonuses(player, stats)
    per = body_stack_value("hp", player.realm_index)
    assert stats["hp"] == 100 + 2 * per


def test_pill_recipe_inputs_scale_with_realm():
    load_all_content()
    recipe = get_recipe("qi_gathering_pill")
    assert recipe is not None
    mortal_inputs = resolve_recipe_inputs(recipe, 0)
    high_inputs = resolve_recipe_inputs(recipe, 9)
    assert mortal_inputs != high_inputs
    assert "green_dew_herb" in mortal_inputs
    assert "monarch_jade" in high_inputs


def test_hp_path_gear_adds_max_hp(session, player):
    load_all_content()
    from src.character import get_character_modifiers
    from src.gear_stash import equip_gear_item

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    forged = forge_equipment_for_player(session, player, "armor", grade="hp", rng=random.Random(5))
    assert forged.success and forged.gear_item_id is not None
    item = session.get(PlayerGearItem, forged.gear_item_id)
    assert item.stat_hp > 0
    equip_gear_item(session, player.id, forged.gear_item_id)
    session.commit()

    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    gear = EquipmentStats(hp=item.stat_hp, defense=item.stat_defense, fortune=item.stat_fortune, insight=item.stat_insight)
    contrib = gear_combat_contribution(gear, "hp")
    assert contrib["hp"] == item.stat_hp
    assert stats.max_hp >= 220 + item.stat_hp
