from __future__ import annotations

import random

import pytest

from src.auto_combat import BeastTemplate
from src.combat.catalog import get_technique, load_technique_catalog
from src.combat.effects import (
    CombatantState,
    apply_status,
    has_status,
    is_stunned,
    spread_burn,
    status_stacks,
    tick_statuses,
    turn_skip_message,
)
from src.combat.engine import (
    CombatState,
    _opponent_damage,
    create_combat_state,
    execute_turn,
    opponent_from_beast,
)
from src.combat.triggers import _compute_base_damage, _maybe_apply_status
from src.combat.loadout import (
    equip_technique,
    ensure_starter_techniques,
    get_equipped_active_techniques,
    get_learned_technique_ids,
    learn_technique,
)
from src.combat.learn import learn_technique_from_manual
from src.combat.rules import load_combat_rules
from src.combat.session import (
    COMBAT_BUSY_MESSAGE,
    abandon_active_combat,
    create_active_combat,
    get_active_combat,
    load_combat_state,
    process_combat_action,
)
from src.combat_stats import PlayerCombatStats
from src.hunt import finalize_hunt_combat, start_hunt_combat
from src.inventory import add_item


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


def test_combat_rules_load():
    rules = load_combat_rules()
    assert rules.max_turns >= 6
    assert "burn" in rules.statuses
    assert "bleed" in rules.statuses


def test_technique_catalog_has_starter_set():
    catalog = load_technique_catalog()
    assert len(catalog) >= 24
    assert "basic_strike" in catalog
    assert get_technique("swift_slash") is not None


def test_status_burn_ticks_damage():
    target = CombatantState(hp=50, max_hp=50)
    apply_status(target, "burn")
    lines = tick_statuses(target)
    assert lines
    assert target.hp < 50


def test_burn_stacks_increase_dot():
    target = CombatantState(hp=100, max_hp=100)
    apply_status(target, "burn")
    apply_status(target, "burn")
    assert status_stacks(target, "burn") == 2
    hp_before = target.hp
    tick_statuses(target)
    assert target.hp == hp_before - 8


def test_status_stun_blocks_action():
    player = CombatantState(hp=100, max_hp=100)
    apply_status(player, "stun")
    assert is_stunned(player)


def test_execute_turn_deals_damage(session, player):
    stats = _stats()
    beast = BeastTemplate("hare", "Hare", hp=40, attack=5, defense=2)
    opponent = opponent_from_beast(beast)
    state = create_combat_state(stats, opponent)
    result = execute_turn(
        state, stats, None, None, "technique", technique_id="basic_strike", rng=random.Random(42)
    )
    assert result.state.opponent.hp < opponent.hp


def test_basic_strike_weaker_than_starter_arts():
    stats = _stats(internal_strength=20, external_strength=20)
    basic = get_technique("basic_strike")
    ember = get_technique("ember_palm")
    swift = get_technique("swift_slash")
    defense = 4
    basic_dmg = _compute_base_damage(basic, stats, defense, None, crit=False)
    ember_dmg = _compute_base_damage(ember, stats, defense, None, crit=False)
    swift_dmg = _compute_base_damage(swift, stats, defense, None, crit=False)
    assert basic_dmg < ember_dmg
    assert basic_dmg < swift_dmg


def test_basic_strike_works_while_sealed():
    stats = _stats()
    beast = BeastTemplate("hare", "Hare", hp=40, attack=5, defense=2)
    state = create_combat_state(stats, opponent_from_beast(beast))
    apply_status(state.player, "seal")
    state.player.sealed = True

    blocked = execute_turn(
        state, stats, None, None, "technique", technique_id="ember_palm", rng=random.Random(1)
    )
    assert blocked.error is not None

    state.opponent.hp = beast.hp
    allowed = execute_turn(
        state, stats, None, None, "technique", technique_id="basic_strike", rng=random.Random(1)
    )
    assert allowed.error is None
    assert allowed.state.opponent.hp < beast.hp


def test_combat_state_roundtrip_json():
    stats = _stats()
    beast = BeastTemplate("hare", "Hare", hp=40, attack=5, defense=2)
    state = create_combat_state(stats, opponent_from_beast(beast))
    state.player.hp = 90
    restored = CombatState.from_dict(state.to_dict())
    assert restored.player.hp == 90
    assert restored.opponent_name == "Hare"


def test_equipped_active_techniques_dedupe_same_art_in_two_slots(session, player):
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "swift_slash")
    equip_technique(session, player, "swift_slash", "2")
    equip_technique(session, player, "swift_slash", "3")
    session.commit()
    equipped = get_equipped_active_techniques(session, player.id)
    ids = [t.technique_id for t in equipped]
    assert ids.count("swift_slash") == 1
    assert ids.count("basic_strike") == 1


def test_learn_and_equip_technique(session, player):
    ensure_starter_techniques(session, player.id)
    session.commit()
    assert "basic_strike" in get_learned_technique_ids(session, player.id)

    ok, _ = learn_technique(session, player.id, "swift_slash")
    assert ok
    ok, msg = equip_technique(session, player, "swift_slash", "2")
    assert ok
    assert "Swift Slash" in msg
    session.commit()


def test_learn_from_manual_consumes_item(session, player):
    add_item(session, player.id, "manual_ember_palm", 1)
    session.commit()
    ok, msg = learn_technique_from_manual(session, player.id, "manual_ember_palm")
    assert ok
    assert "Ember Palm" in msg
    session.commit()
    assert "ember_palm" in get_learned_technique_ids(session, player.id)


def test_start_hunt_blocked_while_combat_active(session, player):
    player.realm_index = 1
    session.commit()
    start, err = start_hunt_combat(session, player, "bamboo_grove", rng=random.Random(5))
    assert err is None and start is not None
    again, err2 = start_hunt_combat(session, player, "bamboo_grove", rng=random.Random(6))
    assert again is None
    assert err2 == COMBAT_BUSY_MESSAGE


def test_abandon_active_combat_clears_row(session, player):
    player.realm_index = 1
    session.commit()
    start, err = start_hunt_combat(session, player, "bamboo_grove", rng=random.Random(5))
    assert err is None and start is not None
    cleared, msg = abandon_active_combat(session, player.id)
    assert cleared
    assert "cleared" in msg.lower()
    session.commit()
    assert get_active_combat(session, player.id) is None
    retry, err3 = start_hunt_combat(session, player, "bamboo_grove", rng=random.Random(7))
    assert err3 is None and retry is not None


def test_active_combat_persistence(session, player):
    player.realm_index = 1
    session.commit()
    start, err = start_hunt_combat(session, player, "bamboo_grove", rng=random.Random(5))
    assert err is None and start is not None
    active = get_active_combat(session, player.id)
    assert active is not None
    loaded = load_combat_state(active)
    assert loaded.opponent_name == start.beast_name
    session.commit()


def test_hunt_combat_finish_grants_drops_on_victory(session, player):
    player.realm_index = 3
    player.substage = 2
    session.commit()
    start, err = start_hunt_combat(session, player, "bamboo_grove", rng=random.Random(9))
    assert err is None and start is not None
    active = get_active_combat(session, player.id)
    assert active is not None
    state = load_combat_state(active)
    state.opponent.hp = 1
    from src.combat.session import save_combat_state

    save_combat_state(active, state)
    from src.character import get_character_modifiers
    from src.combat_stats import compute_combat_stats

    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    result, err = process_combat_action(
        session,
        player,
        active.id,
        "technique",
        technique_id="basic_strike",
        stats=stats,
        mod=mod,
        rng=random.Random(1),
    )
    assert err is None
    assert result is not None
    assert result.state.finished
    if result.state.victory:
        hunt_res = finalize_hunt_combat(
            session, player, start.area_id, start.beast_id, True, rng=random.Random(2)
        )
        session.commit()
        assert hunt_res.success


@pytest.mark.parametrize("status_id", ["burn", "bleed", "poison", "stun", "fear", "seal"])
def test_all_mvp_statuses_apply(status_id: str):
    target = CombatantState(hp=80, max_hp=80)
    applied = apply_status(target, status_id)
    assert applied == status_id
    assert has_status(target, status_id)


def test_technique_turn_with_equipped(session, player):
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "ember_palm")
    equip_technique(session, player, "ember_palm", "2")
    session.commit()

    stats = _stats()
    beast = BeastTemplate("wolf", "Wolf", hp=80, attack=10, defense=4)
    state = create_combat_state(stats, opponent_from_beast(beast))
    tech = get_technique("ember_palm")
    result = execute_turn(state, stats, None, None, "technique", technique_id="ember_palm", rng=random.Random(99))
    assert result.error is None
    assert result.state.opponent.hp < beast.hp


def test_fear_can_skip_opponent_turn():
    stats = _stats()
    beast = BeastTemplate("hare", "Spirit Hare", 35, 7, 2, ())
    state = create_combat_state(stats, opponent_from_beast(beast))
    apply_status(state.opponent, "fear")

    class _AlwaysFear:
        def random(self) -> float:
            return 0.0

    execute_turn(state, stats, None, None, "strike", rng=_AlwaysFear())
    assert any("fear" in line.lower() for line in state.log)


def test_bleed_hits_harder_per_stack():
    rules = load_combat_rules()
    bleed = rules.statuses["bleed"]
    assert bleed.damage_per_stack >= 5
    assert bleed.max_stacks >= 4
    assert not bleed.propagates


def test_burn_propagates_config():
    rules = load_combat_rules()
    burn = rules.statuses["burn"]
    assert burn.propagates
    assert burn.spread_chance > 0


def test_stun_always_skips_turn():
    actor = CombatantState(hp=80, max_hp=80)
    apply_status(actor, "stun")
    assert turn_skip_message(actor, "Foe", random.Random(99)) is not None


def test_spread_burn_jumps_to_second_foe():
    carrier = CombatantState(hp=50, max_hp=50)
    other = CombatantState(hp=50, max_hp=50)
    apply_status(carrier, "burn")

    class _AlwaysSpread:
        def random(self) -> float:
            return 0.0

    lines = spread_burn(carrier, "Alpha", [(other, "Beta")], _AlwaysSpread())
    assert has_status(other, "burn")
    assert lines


def test_ember_palm_burn_proc_forced_rng():
    stats = _stats()
    beast = BeastTemplate("hare", "Spirit Hare", 35, 7, 2, ())
    state = create_combat_state(stats, opponent_from_beast(beast))
    state.opponent_traits = []

    class _AlwaysProc:
        def random(self) -> float:
            return 0.0

    execute_turn(state, stats, None, None, "technique", technique_id="ember_palm", rng=_AlwaysProc())
    assert has_status(state.opponent, "burn")
    assert any("afflicted" in line.lower() for line in state.log)


def test_bleed_immune_logs_resist():
    stats = _stats()
    beast = BeastTemplate("serpent", "Serpent", 55, 12, 4, ("bleed_immune",))
    state = create_combat_state(stats, opponent_from_beast(beast))
    applied = _maybe_apply_status(
        state,
        state.opponent,
        "bleed",
        1.0,
        random.Random(0),
        traits=state.opponent_traits,
    )
    assert not applied
    assert any("resists" in line.lower() for line in state.log)
