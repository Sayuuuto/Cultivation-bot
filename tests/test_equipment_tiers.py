from __future__ import annotations



import pytest



from src.content import get_recipe, load_all_content

from src.equipment_tiers import gear_is_active, normalize_gear_path, resolve_equipment_tier

from src.forge import forge_and_equip, forge_equipment_for_player

from src.foundation import apply_foundation_bonuses, body_stack_value

from src.inventory import add_item

from src.models import PlayerEquipment

from src.pill_recipes import resolve_recipe_inputs

from src.stats import get_total_equipment_stats





@pytest.mark.parametrize("realm_index", [0, 1, 4, 9])

def test_equipment_tier_stat_ranges_scale_with_realm(realm_index):

    load_all_content()

    entry = resolve_equipment_tier(realm_index, "weapon", "external")

    assert entry is not None

    mortal = resolve_equipment_tier(0, "weapon", "external")

    assert mortal is not None

    if realm_index > 0:

        assert entry.stat_ranges["power"][1] > mortal.stat_ranges["power"][1]





def test_forge_paths_have_distinct_stat_profiles():

    load_all_content()

    internal = resolve_equipment_tier(0, "weapon", "internal")

    external = resolve_equipment_tier(0, "weapon", "external")

    crit = resolve_equipment_tier(0, "accessory", "crit")

    assert internal is not None and external is not None and crit is not None

    assert external.stat_ranges["power"][1] > internal.stat_ranges["power"][1]

    assert crit.stat_ranges["fortune"][1] > external.stat_ranges["fortune"][1]





def test_legacy_grade_aliases_map_to_paths():

    load_all_content()

    assert normalize_gear_path("fine") == "external"

    assert normalize_gear_path("common") == "internal"

    assert normalize_gear_path("exalted") == "crit"





def test_outgrown_gear_does_not_apply_to_combat(session, player):

    load_all_content()

    from src.equipment import get_player_equipment



    add_item(session, player.id, "minor_beast_core", 2)

    add_item(session, player.id, "green_dew_herb", 1)

    session.commit()



    res = forge_and_equip(session, player, "weapon", grade="external", rng=__import__("random").Random(1))

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

