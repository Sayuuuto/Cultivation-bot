from __future__ import annotations

import random

import pytest
from sqlalchemy import func, select

from src.adventure import (
    AdventureResult,
    abandon_adventure,
    apply_adventure_choice,
    get_active_adventure,
    get_encounters_for_area,
    resume_adventure_session,
    run_adventure,
    start_adventure_session,
)
from tests.rng_helpers import ScriptedRNG, safe_adventure_segment_floats
from src.character import get_character_modifiers
from src.consumables import use_item
from src.content import load_all_content
from src.cooldown_haste import (
    consume_haste_for_activity,
    cooldown_remaining_with_haste,
    get_haste_reduction_seconds,
)
from src.crafting import craft_recipe
from src.effects import add_haste_effect
from src.equipment import apply_affix_stone, get_or_create_slot, get_player_equipment
from src.forge import forge_equipment
from src.inventory import add_item, get_item_quantity, load_item_catalog
from src.models import AdventureRun, PlayerEffect
from src.stats import equipment_stats_to_modifiers, get_total_equipment_stats


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def _safe_segment_floats() -> list[float]:
    return safe_adventure_segment_floats()


def _complete_interactive_adventure(session, player, area_id: str = "bamboo_grove") -> AdventureResult:
    encounters = get_encounters_for_area(area_id)
    rng = ScriptedRNG(
        floats=_safe_segment_floats(),
        encounter_queue=[encounters[0], encounters[1]],
        randint_queue=[1, 1, 1, 1],
    )
    pending, err = start_adventure_session(session, player, area_id, "balanced", rng=rng)
    assert err is None and pending is not None

    choice_one = pending.choices[1].id
    result, err = apply_adventure_choice(session, player, pending.active_id, choice_one, rng=rng)
    assert err is None and result is not None

    if isinstance(result, AdventureResult):
        return result

    choice_two = result.choices[1].id
    final, err = apply_adventure_choice(session, player, result.active_id, choice_two, rng=rng)
    assert err is None and isinstance(final, AdventureResult)
    return final


def test_full_interactive_adventure_two_segments_grants_inventory(session, player):
    player.qi = 40
    session.commit()

    result = _complete_interactive_adventure(session, player)
    session.commit()

    assert result.outcome in {"success", "partial"}
    assert result.segments_cleared == 2
    assert result.qi_delta <= 0
    assert get_active_adventure(session, player.id) is None
    assert sum(get_item_quantity(session, player.id, item_id) for item_id in result.drops) > 0

    run_count = session.scalar(
        select(func.count()).select_from(AdventureRun).where(AdventureRun.player_id == player.id)
    )
    assert run_count == 1


def test_catastrophic_choice_ends_run_early_with_qi_loss(session, player):
    player.realm_index = 1
    player.qi = 30
    session.commit()

    encounters = get_encounters_for_area("ashen_cliff")
    charge_encounter = next(e for e in encounters if e.id == "bandit_ambush")
    rng = ScriptedRNG(
        floats=[0.05],
        encounter_queue=[charge_encounter],
    )

    pending, err = start_adventure_session(session, player, "ashen_cliff", "reckless", rng=rng)
    assert err is None and pending is not None

    result, err = apply_adventure_choice(session, player, pending.active_id, "charge", rng=rng)
    session.commit()

    assert err is None
    assert isinstance(result, AdventureResult)
    assert result.failed_run is True
    assert result.outcome == "fail"
    assert result.segments_cleared == 0
    assert player.qi < 30
    assert get_active_adventure(session, player.id) is None


def test_cannot_start_second_adventure_while_active(session, player):
    rng = ScriptedRNG(floats=[0.99], encounter_queue=[get_encounters_for_area("bamboo_grove")[0]])
    pending, err = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    session.commit()
    assert pending is not None

    again, err2 = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    assert again is None
    assert err2 is not None
    assert "already" in err2.lower()


def test_abandon_clears_active_adventure_without_rewards(session, player):
    rng = ScriptedRNG(floats=[0.99], encounter_queue=[get_encounters_for_area("bamboo_grove")[0]])
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    session.commit()
    assert pending is not None

    ok, msg = abandon_adventure(session, player.id)
    session.commit()
    assert ok is True
    assert get_active_adventure(session, player.id) is None
    assert "withdraw" in msg.lower()


def test_apply_choice_rejects_invalid_choice_and_wrong_player(
    session, player, player_two
):
    rng = ScriptedRNG(floats=[0.99], encounter_queue=[get_encounters_for_area("bamboo_grove")[0]])
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    session.commit()
    assert pending is not None

    result, err = apply_adventure_choice(session, player_two, pending.active_id, "fight", rng=rng)
    assert result is None
    assert err is not None

    result2, err2 = apply_adventure_choice(session, player, pending.active_id, "not_a_real_choice", rng=rng)
    assert result2 is None
    assert err2 is not None


def test_resume_adventure_restores_pending_state(session, player):
    rng = ScriptedRNG(floats=[0.99], encounter_queue=[get_encounters_for_area("bamboo_grove")[0]])
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "cautious", rng=rng)
    session.commit()

    resumed, err = resume_adventure_session(session, player)
    assert err is None and resumed is not None
    assert resumed.active_id == pending.active_id
    assert resumed.segment == 1
    assert resumed.prompt


def test_forge_replaces_slot_and_aggregates_four_piece_stats(session, player):
    recipes = {
        "weapon": {"spirit_iron_shard": 2, "minor_beast_core": 1},
        "armor": {"bamboo_resin": 3, "green_dew_herb": 2},
        "accessory": {"bandit_token": 2, "ember_moss": 2},
        "talisman": {"moonlotus": 1, "ancient_dust": 2},
    }
    for inputs in recipes.values():
        for item_id, qty in inputs.items():
            add_item(session, player.id, item_id, qty)
    session.commit()

    for idx, slot in enumerate(recipes):
        res = forge_equipment(session, player.id, slot, rng=random.Random(idx + 10))
        assert res.success is True

    session.commit()
    totals = get_total_equipment_stats(session, player.id)
    assert totals.power > 0
    assert totals.defense > 0
    assert totals.fortune > 0
    assert totals.insight > 0
    assert len(get_player_equipment(session, player.id)) == 4


def test_forge_same_slot_overwrites_previous_piece(session, player):
    add_item(session, player.id, "spirit_iron_shard", 4)
    add_item(session, player.id, "minor_beast_core", 2)
    session.commit()

    first = forge_equipment(session, player.id, "weapon", rng=random.Random(1))
    second = forge_equipment(session, player.id, "weapon", rng=random.Random(99))
    session.commit()

    assert first.success and second.success
    row = get_or_create_slot(session, player.id, "weapon")
    assert row.stat_power == second.stats["power"]


def test_affix_stone_blocked_until_gear_is_forged(session, player):
    add_item(session, player.id, "affix_stone", 1)
    session.commit()

    ok, message, affix = apply_affix_stone(session, player.id, "weapon", rng=random.Random(1))
    assert ok is False
    assert affix is None
    assert "forge" in message.lower()
    assert get_item_quantity(session, player.id, "affix_stone") == 1


def test_forged_stats_increase_character_modifiers(session, player):
    baseline = get_character_modifiers(session, player)

    add_item(session, player.id, "moonlotus", 1)
    add_item(session, player.id, "ancient_dust", 2)
    session.commit()
    forge_equipment(session, player.id, "talisman", rng=random.Random(3))
    session.commit()

    geared = get_character_modifiers(session, player)
    totals = get_total_equipment_stats(session, player.id)
    expected = equipment_stats_to_modifiers(totals)

    assert geared.adventure_success > baseline.adventure_success
    assert geared.adventure_success >= baseline.adventure_success + expected["adventure_success"] * 0.9
    assert geared.rare_event_mult >= baseline.rare_event_mult


def test_meridian_surge_haste_consumes_charges_independently(session, player):
    add_item(session, player.id, "meridian_surge_pill", 1)
    use_item(session, player, "meridian_surge_pill", rng=random.Random(1))
    session.commit()

    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 420

    shaved = consume_haste_for_activity(session, player.id, "cultivate")
    session.commit()
    assert shaved == 420
    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 420

    consume_haste_for_activity(session, player.id, "cultivate")
    session.commit()
    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 0


def test_stacking_haste_effects_add_charges(session, player):
    add_haste_effect(session, player.id, "flow_pill")
    session.flush()
    add_haste_effect(session, player.id, "flow_pill")
    session.commit()

    effect = session.execute(
        select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.effect_id == "haste_adventure",
        )
    ).scalar_one()
    assert effect.charges == 2
    assert effect.value_int == 600


def test_cooldown_haste_can_zero_out_remaining_timer(session):
    assert cooldown_remaining_with_haste(900, 600) == 300
    assert cooldown_remaining_with_haste(400, 600) == 0
    assert cooldown_remaining_with_haste(0, 600) == 0


def test_craft_batch_partial_success_tracks_byproducts(session, player):
    add_item(session, player.id, "minor_beast_core", 6)
    session.commit()

    rng = ScriptedRNG(floats=[0.99, 0.01, 0.99])  # fail, success, fail (tempering is 88%)
    res = craft_recipe(session, player, "tempering_pill", amount=3, rng=rng)
    session.commit()

    assert res.success is True
    assert res.crafted.get("tempering_pill", 0) == 1
    assert res.byproducts.get("pill_ash", 0) == 2
    assert get_item_quantity(session, player.id, "minor_beast_core") == 0


def test_craft_batch_stops_when_materials_exhausted(session, player):
    add_item(session, player.id, "green_dew_herb", 2)
    session.commit()

    res = craft_recipe(session, player, "qi_gathering_pill", amount=5, rng=ScriptedRNG(floats=[0.0]))
    assert res.success is False
    assert "enough materials" in res.message.lower()


def test_underleveled_player_can_start_interactive_adventure(session, player):
    player.realm_index = 0
    session.commit()

    pending, err = start_adventure_session(session, player, "moonwell_ruins", "balanced")
    assert pending is not None
    assert err is None


def test_run_adventure_auto_path_allows_underleveled_entry(session, player):
    player.realm_index = 0
    session.commit()
    res = run_adventure(session, player, "moonwell_ruins", "balanced")
    assert res.outcome != "invalid"
    assert any("beasts here could end you" in m.lower() for m in res.messages)


def test_high_insight_gear_increases_rare_event_multiplier(session, player):
    row = get_or_create_slot(session, player.id, "talisman")
    row.item_id = "moonwell_pendant"
    row.stat_insight = 10
    row.stat_power = 0
    row.stat_defense = 0
    row.stat_fortune = 0
    session.add(row)
    session.commit()

    mod = get_character_modifiers(session, player)
    assert mod.rare_event_mult >= 1.2


def test_gatebreaker_dust_grants_dungeon_haste(session, player):
    add_item(session, player.id, "gatebreaker_dust", 1)
    session.commit()
    ok, msg = use_item(session, player, "gatebreaker_dust", rng=random.Random(1))
    session.commit()

    assert ok is True
    assert get_haste_reduction_seconds(session, player.id, "dungeon") == 1800
    assert "dungeon" in msg.lower() or "gate" in msg.lower()


def test_forge_fails_without_consuming_materials(session, player):
    add_item(session, player.id, "spirit_iron_shard", 1)
    session.commit()

    res = forge_equipment(session, player.id, "weapon", rng=random.Random(1))
    assert res.success is False
    assert get_item_quantity(session, player.id, "spirit_iron_shard") == 1


def test_full_gear_affix_loadout_pipeline(session, player):
    forge_inputs = {
        "weapon": {"spirit_iron_shard": 2, "minor_beast_core": 1},
        "armor": {"bamboo_resin": 3, "green_dew_herb": 2},
    }
    for inputs in forge_inputs.values():
        for item_id, qty in inputs.items():
            add_item(session, player.id, item_id, qty)
    add_item(session, player.id, "affix_stone", 1)
    session.commit()

    baseline = get_character_modifiers(session, player)
    forge_equipment(session, player.id, "weapon", rng=random.Random(2))
    forge_equipment(session, player.id, "armor", rng=random.Random(3))
    ok, _, affix_id = apply_affix_stone(session, player.id, "weapon", rng=random.Random(4))
    session.commit()

    assert ok is True and affix_id is not None
    geared = get_character_modifiers(session, player)
    assert geared.adventure_success > baseline.adventure_success
    assert geared.pvp_power >= baseline.pvp_power


def test_partial_adventure_keeps_first_segment_loot_on_second_segment_catastrophe(session, player):
    """Segment 1 succeeds and grants loot; segment 2 catastrophic fail still deposits segment 1 drops."""
    player.realm_index = 1
    player.qi = 50
    session.commit()

    encounters = get_encounters_for_area("bamboo_grove")
    beast_enc = next(e for e in encounters if e.id == "beast_on_path")
    rng = ScriptedRNG(
        floats=[
            0.99,
            0.01,
            0.99,  # segment 1: no catastrophe, success, no rare
            0.01,  # segment 2: catastrophe on risky choice
        ],
        encounter_queue=[beast_enc, beast_enc],
        randint_queue=[1],
    )
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    assert pending is not None

    mid, _ = apply_adventure_choice(session, player, pending.active_id, "detour", rng=rng)
    assert mid is not None and not isinstance(mid, AdventureResult)

    final, _ = apply_adventure_choice(session, player, mid.active_id, "bait", rng=rng)
    session.commit()

    assert isinstance(final, AdventureResult)
    assert final.failed_run is True
    assert final.segments_cleared >= 1
    assert len(final.drops) >= 1
    assert get_item_quantity(session, player.id, list(final.drops.keys())[0]) >= 1


def test_rare_event_during_interactive_segment_can_grant_bonus_loot(session, player):
    encounters = get_encounters_for_area("bamboo_grove")
    rng = ScriptedRNG(
        floats=[
            0.99,
            0.01,
            0.001,  # force rare event roll on segment 1
            0.99,
            0.01,
            0.99,
        ],
        encounter_queue=[encounters[0], encounters[1]],
        randint_queue=[1, 1, 1, 1, 1],
    )
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "reckless", rng=rng)
    safe_one = min(pending.choices, key=lambda c: c.fail_chance).id
    result, err = apply_adventure_choice(session, player, pending.active_id, safe_one, rng=rng)
    assert err is None and result is not None

    if isinstance(result, AdventureResult):
        final = result
    else:
        safe_two = min(result.choices, key=lambda c: c.fail_chance).id
        final, err = apply_adventure_choice(session, player, result.active_id, safe_two, rng=rng)
        assert err is None
    session.commit()

    assert isinstance(final, AdventureResult)
    assert len(final.rare_events) >= 1


def test_swiftwind_effect_consumed_when_adventure_completes(session, player):
    from src.effects import add_effect

    add_effect(session, player.id, "swiftwind", charges=1)
    session.commit()
    assert "swiftwind" in get_character_modifiers(session, player).active_effects

    _complete_interactive_adventure(session, player)
    session.commit()

    assert "swiftwind" not in get_character_modifiers(session, player).active_effects


def test_double_flow_pill_use_after_commit_stacks_haste_charges(session, player):
    add_item(session, player.id, "flow_pill", 2)
    session.commit()

    use_item(session, player, "flow_pill", rng=random.Random(1))
    session.commit()
    use_item(session, player, "flow_pill", rng=random.Random(2))
    session.commit()

    effect = session.execute(
        select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.effect_id == "haste_adventure",
        )
    ).scalar_one()
    assert effect.charges == 2

    consume_haste_for_activity(session, player.id, "adventure")
    session.commit()
    effect = session.execute(
        select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.effect_id == "haste_adventure",
        )
    ).scalar_one()
    assert effect.charges == 1
