from __future__ import annotations

import random

import pytest
from sqlalchemy import func, select

from src.adventure import (
    AdventureResult,
    abandon_adventure,
    apply_adventure_combat_outcome,
    apply_adventure_choice,
    get_active_adventure,
    get_encounters_for_area,
    resume_adventure_session,
    run_adventure,
    start_adventure_session,
)
from tests.rng_helpers import ScriptedRNG, adventure_start_floats, safe_adventure_segment_floats
from src.character import get_character_modifiers
from src.consumables import use_item
from src.content import load_all_content
from src.cooldown_haste import (
    HASTE_UNIVERSAL_EFFECT,
    consume_haste_for_activity,
    cooldown_remaining_with_haste,
    get_haste_reduction_seconds,
)
from src.crafting import craft_recipe
from src.effects import add_haste_effect
from src.equipment import apply_affix_stone, get_or_create_slot, get_player_equipment
from src.forge import forge_and_equip, forge_equipment_for_player
from src.inventory import add_item, get_item_quantity, load_item_catalog
from src.models import AdventureRun, PlayerEffect
from src.stats import equipment_stats_to_modifiers, get_total_equipment_stats


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def _safe_segment_floats() -> list[float]:
    return safe_adventure_segment_floats()


def _safest_choice_id(choices) -> str:
    return min(choices, key=lambda c: c.fail_chance).id


def _complete_interactive_adventure(session, player, area_id: str = "bamboo_grove") -> AdventureResult:
    encounters = get_encounters_for_area(area_id)
    beast = next(e for e in encounters if e.id == "beast_on_path")
    mist = next(e for e in encounters if e.id == "mist_crossroads")
    rng = ScriptedRNG(
        floats=safe_adventure_segment_floats() * 3,
        encounter_queue=[beast, mist, beast],
        randint_queue=[3, 1, 1, 1],
    )
    pending, err = start_adventure_session(session, player, area_id, "balanced", rng=rng)
    assert err is None and pending is not None

    current = pending
    for _ in range(8):
        preferred = "detour" if any(c.id == "detour" for c in current.choices) else None
        if preferred is None:
            preferred = "wait" if any(c.id == "wait" for c in current.choices) else None
        choice_id = preferred or _safest_choice_id(current.choices)
        result, err = apply_adventure_choice(session, player, current.active_id, choice_id, rng=rng)
        assert err is None and result is not None
        if isinstance(result, AdventureResult):
            return result
        current = result

    raise AssertionError("adventure did not finish within expected segments")


def test_full_interactive_adventure_two_segments_grants_inventory(session, player):
    player.qi = 40
    session.commit()

    result = _complete_interactive_adventure(session, player)
    session.commit()

    assert result.outcome in {"success", "partial"}
    assert result.segments_cleared >= 1
    assert result.qi_delta <= 0
    assert get_active_adventure(session, player.id) is None
    assert sum(get_item_quantity(session, player.id, item_id) for item_id in result.drops) > 0

    run_count = session.scalar(
        select(func.count()).select_from(AdventureRun).where(AdventureRun.player_id == player.id)
    )
    assert run_count == 1


def test_catastrophic_choice_ends_run_early_without_qi_loss(session, player):
    player.realm_index = 1
    player.qi = 30
    session.commit()

    encounters = get_encounters_for_area("ashen_cliff")
    charge_encounter = next(e for e in encounters if e.id == "bandit_ambush")
    rng = ScriptedRNG(
        floats=[0.99, 0.99, 0.01],
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
    assert player.qi == 30
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


def _stock_mortal_forge_materials(session, player_id: int, *, sets: int = 4) -> None:
    add_item(session, player_id, "minor_beast_core", 2 * sets)
    add_item(session, player_id, "green_dew_herb", sets)
    add_item(session, player_id, "bamboo_resin", sets)


def test_forge_replaces_slot_and_aggregates_four_piece_stats(session, player):
    _stock_mortal_forge_materials(session, player.id, sets=4)
    session.commit()

    for idx, slot in enumerate(("weapon", "armor", "accessory", "talisman")):
        res = forge_and_equip(session, player, slot, rng=random.Random(idx + 10))
        assert res.success is True

    session.commit()
    totals = get_total_equipment_stats(session, player.id, player_realm_index=player.realm_index)
    assert totals.power > 0
    assert totals.defense > 0
    assert totals.fortune > 0
    assert totals.insight > 0
    assert len(get_player_equipment(session, player.id)) == 4


def test_forge_same_slot_adds_second_stash_piece(session, player):
    _stock_mortal_forge_materials(session, player.id, sets=2)
    session.commit()

    first = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    second = forge_equipment_for_player(session, player, "weapon", rng=random.Random(99))
    session.commit()

    assert first.success and second.success
    from src.gear_stash import list_stash

    stash = list_stash(session, player.id, slot="weapon")
    assert len(stash) == 2


def test_affix_stone_blocked_until_gear_is_forged(session, player):
    add_item(session, player.id, "affix_stone", 1)
    session.commit()

    ok, message, affix = apply_affix_stone(session, player.id, 99999, rng=random.Random(1))
    assert ok is False
    assert affix is None
    assert "forge" in message.lower() or "pick" in message.lower()
    assert get_item_quantity(session, player.id, "affix_stone") == 1


def test_forged_stats_increase_character_modifiers(session, player):
    baseline = get_character_modifiers(session, player)

    _stock_mortal_forge_materials(session, player.id, sets=1)
    session.commit()
    forge_and_equip(session, player, "talisman", rng=random.Random(3))
    session.commit()

    geared = get_character_modifiers(session, player)
    totals = get_total_equipment_stats(session, player.id, player_realm_index=player.realm_index)
    expected = equipment_stats_to_modifiers(totals)

    assert geared.adventure_success > baseline.adventure_success
    assert geared.adventure_success >= baseline.adventure_success + expected["adventure_success"] * 0.9
    assert geared.rare_event_mult >= baseline.rare_event_mult


def test_meridian_surge_haste_consumes_charges_independently(session, player):
    add_item(session, player.id, "meridian_surge_pill", 1)
    use_item(session, player, "meridian_surge_pill", rng=random.Random(1))
    session.commit()

    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 900

    shaved = consume_haste_for_activity(session, player.id, "cultivate")
    session.commit()
    assert shaved == 900
    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 900

    consume_haste_for_activity(session, player.id, "cultivate")
    session.commit()
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
            PlayerEffect.effect_id == HASTE_UNIVERSAL_EFFECT,
        )
    ).scalar_one()
    assert effect.charges == 4
    assert effect.value_int == 900


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
    assert get_haste_reduction_seconds(session, player.id, "dungeon") == 2400
    assert "dungeon" in msg.lower() or "gate" in msg.lower()


def test_forge_fails_without_consuming_materials(session, player):
    add_item(session, player.id, "spirit_iron_shard", 1)
    session.commit()

    res = forge_equipment_for_player(session, player, "weapon", rng=random.Random(1))
    assert res.success is False
    assert get_item_quantity(session, player.id, "spirit_iron_shard") == 1


def test_full_gear_affix_loadout_pipeline(session, player):
    _stock_mortal_forge_materials(session, player.id, sets=2)
    add_item(session, player.id, "affix_stone", 1)
    session.commit()

    baseline = get_character_modifiers(session, player)
    weapon = forge_and_equip(session, player, "weapon", rng=random.Random(2))
    forge_and_equip(session, player, "armor", rng=random.Random(3))
    ok, _, affix_id = apply_affix_stone(session, player.id, weapon.gear_item_id, rng=random.Random(4))
    session.commit()

    assert ok is True and affix_id is not None
    geared = get_character_modifiers(session, player)
    assert geared.adventure_success > baseline.adventure_success
    assert geared.pvp_power >= baseline.pvp_power


def test_partial_adventure_keeps_first_segment_loot_on_second_segment_setback(session, player):
    """Segment 1 succeeds and grants loot; a rough segment 2 still leaves that loot on the run."""
    player.realm_index = 1
    player.qi = 50
    session.commit()

    encounters = get_encounters_for_area("bamboo_grove")
    beast_enc = next(e for e in encounters if e.id == "beast_on_path")
    rng = ScriptedRNG(
        floats=[*safe_adventure_segment_floats(), 0.99, 0.01],
        encounter_queue=[beast_enc, beast_enc],
        randint_queue=[3],
    )
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    assert pending is not None

    mid, _ = apply_adventure_choice(session, player, pending.active_id, "detour", rng=rng)
    assert mid is not None and not isinstance(mid, AdventureResult)

    final, _ = apply_adventure_choice(session, player, mid.active_id, "bait", rng=rng)
    if not isinstance(final, AdventureResult) and final is not None and final.encounter_type == "combat":
        final, _ = apply_adventure_combat_outcome(
            session, player, final.active_id, victory=False, rng=rng
        )
    session.commit()

    assert isinstance(final, AdventureResult)
    assert final.outcome in {"partial", "fail"}
    assert final.segments_cleared >= 1
    assert len(final.drops) >= 1
    assert any(
        "forced back" in m or "backfires" in m or "driven back" in m
        for m in final.messages
    )
    assert get_item_quantity(session, player.id, list(final.drops.keys())[0]) >= 1


def test_rare_event_during_interactive_segment_can_grant_bonus_loot(session, player):
    encounters = get_encounters_for_area("bamboo_grove")
    beast = next(e for e in encounters if e.id == "beast_on_path")
    mist = next(e for e in encounters if e.id == "mist_crossroads")
    rng = ScriptedRNG(
        floats=[
            *safe_adventure_segment_floats(trigger_rare=True),
            *safe_adventure_segment_floats(),
            *safe_adventure_segment_floats(),
        ],
        encounter_queue=[beast, mist, beast],
        randint_queue=[3, 1, 1, 1, 1],
    )
    pending, _ = start_adventure_session(session, player, "bamboo_grove", "balanced", rng=rng)
    current = pending
    for _ in range(6):
        safe_choice = _safest_choice_id(current.choices)
        result, err = apply_adventure_choice(session, player, current.active_id, safe_choice, rng=rng)
        assert err is None
        if isinstance(result, AdventureResult):
            final = result
            break
        current = result
    else:
        raise AssertionError("adventure did not finish")
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
            PlayerEffect.effect_id == HASTE_UNIVERSAL_EFFECT,
        )
    ).scalar_one()
    assert effect.charges == 4

    consume_haste_for_activity(session, player.id, "adventure")
    session.commit()
    effect = session.execute(
        select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.effect_id == HASTE_UNIVERSAL_EFFECT,
        )
    ).scalar_one()
    assert effect.charges == 3
