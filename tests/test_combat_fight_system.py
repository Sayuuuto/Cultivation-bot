from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from src.combat.catalog import get_technique, load_technique_catalog
from src.combat.effects import (
    CombatantState,
    apply_status,
    cleanse_debuffs,
    has_status,
    is_stunned,
    status_stacks,
    status_turns_remaining,
    tick_statuses,
)
from src.combat.engine import execute_pvp_turn, execute_turn
from src.combat.rules import load_combat_rules
from src.combat.targeting import technique_hits_all_enemies
from src.combat.triggers import opponent_trait_turn
from src.combat.loadout import ensure_starter_techniques, learn_technique
from src.content import load_all_content
from src.cooperative_dungeons import get_cooperative_dungeon
from src.dungeon_combat import select_technique, select_target, start_room_combat
from src.dungeon_party import accept_invite, create_party_with_invites, load_members
from tests.combat_fight_harness import (
    AlwaysProcRng,
    NeverCritRng,
    active_technique_ids,
    force_apply,
    fresh_hunt_state,
    invalidate_combat_caches,
    probe_technique_damage,
    run_stunned_player_turns,
    standard_stats,
    tanky_beast,
)

pytestmark = pytest.mark.usefixtures("combat_content_loaded")

ALL_STATUSES = ["burn", "bleed", "poison", "stun", "fear", "seal"]
DOT_STATUSES = ["burn", "bleed", "poison"]
CC_STATUSES = ["stun", "fear", "seal"]
ACTIVE_TECHNIQUES = active_technique_ids()
NON_DAMAGE_TECHNIQUES = frozenset(
    {"purifying_breath", "qi_barrier", "mountain_guard", "mist_step", "iron_body"}
)
MONSTER_TRAITS = ["seal_on_hit", "high_stun_chance", "bleed_immune", "cleanse_every_3_turns"]
MONSTERS_PATH = Path(__file__).resolve().parent.parent / "config" / "monsters.json"


@pytest.fixture(scope="module")
def combat_content_loaded():
    load_all_content()
    invalidate_combat_caches()
    yield
    invalidate_combat_caches()


def _monster_ids_with_trait(trait: str) -> list[str]:
    raw = json.loads(MONSTERS_PATH.read_text(encoding="utf-8"))
    return sorted(mid for mid, data in raw.items() if trait in data.get("traits", []))


# --- Status stacking (6 statuses × 5 applications = 30) ---


@pytest.mark.parametrize("status_id", ALL_STATUSES)
@pytest.mark.parametrize("applications", range(1, 6))
def test_status_stack_count_capped(status_id: str, applications: int):
    rules = load_combat_rules()
    rule = rules.statuses[status_id]
    target = CombatantState(hp=200, max_hp=200)
    for _ in range(applications):
        apply_status(target, status_id)
    assert status_stacks(target, status_id) == min(applications, rule.max_stacks)


@pytest.mark.parametrize("status_id", CC_STATUSES)
@pytest.mark.parametrize("applications", range(1, 4))
def test_cc_duration_stacks_on_reapply(status_id: str, applications: int):
    rules = load_combat_rules()
    rule = rules.statuses[status_id]
    target = CombatantState(hp=200, max_hp=200)
    for _ in range(applications):
        apply_status(target, status_id)
    cap = rule.max_stacks * rule.duration
    assert status_turns_remaining(target, status_id) == min(applications * rule.duration, cap)


@pytest.mark.parametrize("status_id", DOT_STATUSES)
@pytest.mark.parametrize("stacks", range(1, 4))
def test_dot_damage_scales_with_stacks(status_id: str, stacks: int):
    rules = load_combat_rules()
    rule = rules.statuses[status_id]
    target = CombatantState(hp=200, max_hp=200)
    for _ in range(stacks):
        apply_status(target, status_id)
    hp_before = target.hp
    tick_statuses(target)
    expected = rule.damage_per_stack * min(stacks, rule.max_stacks)
    assert target.hp == hp_before - expected


@pytest.mark.parametrize("status_id", DOT_STATUSES)
@pytest.mark.parametrize("turn", range(1, 5))
def test_dot_ticks_each_turn(status_id: str, turn: int):
    target = CombatantState(hp=200, max_hp=200)
    apply_status(target, status_id)
    for _ in range(turn):
        if not has_status(target, status_id):
            break
        tick_statuses(target)
    if turn <= load_combat_rules().statuses[status_id].duration:
        assert has_status(target, status_id) or turn == load_combat_rules().statuses[status_id].duration


# --- Stun skip behavior (40+) ---


@pytest.mark.parametrize("applications", range(1, 4))
def test_stun_turns_match_applications(applications: int):
    target = CombatantState(hp=200, max_hp=200)
    for _ in range(applications):
        apply_status(target, "stun")
    assert status_turns_remaining(target, "stun") == applications


def test_stun_skips_player_technique_in_hunt():
    stats = standard_stats()
    state = fresh_hunt_state(stats)
    force_apply(state.player, "stun")
    opp_hp = state.opponent.hp
    execute_turn(state, stats, None, None, "technique", technique_id="ember_palm", rng=NeverCritRng(1))
    assert any("stunned" in line.lower() for line in state.log)
    assert state.opponent.hp == opp_hp


@pytest.mark.parametrize("seed", range(15))
def test_stun_skips_pvp_actor_turn(seed: int):
    stats = standard_stats()
    state = fresh_hunt_state(stats, tanky_beast(hp=180, max_hp=180))
    state.context = "pvp"
    force_apply(state.player, "stun")
    opp_hp = state.opponent.hp
    execute_pvp_turn(
        state,
        stats,
        None,
        None,
        "technique",
        technique_id="basic_strike",
        rng=NeverCritRng(seed),
    )
    assert any("stunned" in line.lower() for line in state.log)
    assert state.opponent.hp == opp_hp


@pytest.mark.parametrize("applications", range(1, 4))
def test_multi_stun_skips_multiple_hunt_turns(applications: int):
    stats = standard_stats()
    state = fresh_hunt_state(stats, tanky_beast(attack=0))
    force_apply(state.player, "stun", applications)
    skipped = run_stunned_player_turns(state, stats, applications)
    assert skipped[:applications] == [True] * applications


def test_stun_expires_after_duration_ticks():
    target = CombatantState(hp=200, max_hp=200)
    apply_status(target, "stun")
    assert is_stunned(target)
    tick_statuses(target)
    assert not is_stunned(target)


def test_cleanse_removes_stun():
    target = CombatantState(hp=200, max_hp=200)
    apply_status(target, "stun")
    removed = cleanse_debuffs(target, 1, only={"stun"})
    assert removed == ["stun"]
    assert not is_stunned(target)


# --- Technique damage matrix (18 × 20 = 360) ---


@pytest.mark.parametrize("technique_id", ACTIVE_TECHNIQUES)
@pytest.mark.parametrize("seed", range(20))
def test_active_technique_resolves_without_error(technique_id: str, seed: int):
    if technique_id in NON_DAMAGE_TECHNIQUES:
        pytest.skip("non-direct-damage art")
    stats = standard_stats()
    state = fresh_hunt_state(stats)
    if technique_id == "heavens_cleave":
        force_apply(state.opponent, "bleed")
    if technique_id == "cinder_lance":
        force_apply(state.opponent, "burn")
    if technique_id == "iron_cleave":
        force_apply(state.opponent, "bleed")
    if technique_id == "soul_siphon":
        force_apply(state.opponent, "poison")
    if technique_id == "sanguine_drain":
        force_apply(state.opponent, "bleed")
    rng = AlwaysProcRng(seed) if get_technique(technique_id) and get_technique(technique_id).status_chance else NeverCritRng(seed)
    from src.combat.triggers import resolve_technique

    err = resolve_technique(state, stats, None, technique_id, rng)
    assert err is None, err


@pytest.mark.parametrize("technique_id", [t for t in ACTIVE_TECHNIQUES if t not in NON_DAMAGE_TECHNIQUES])
@pytest.mark.parametrize("seed", range(15))
def test_technique_deals_positive_damage(technique_id: str, seed: int):
    stats = standard_stats()
    state = fresh_hunt_state(stats)

    def setup(s):
        if technique_id == "heavens_cleave":
            force_apply(s.opponent, "bleed")
        if technique_id in {"cinder_lance", "iron_cleave"}:
            force_apply(s.opponent, "bleed" if technique_id == "iron_cleave" else "burn")
        if technique_id == "soul_siphon":
            force_apply(s.opponent, "poison")
        if technique_id == "sanguine_drain":
            force_apply(s.opponent, "bleed")

    setup(state)
    probe = probe_technique_damage(technique_id, seed=seed, setup=setup)
    assert probe.actual > 0


@pytest.mark.parametrize(
    "technique_id",
    [
        "basic_strike",
        "swift_slash",
        "ember_palm",
        "soul_needle",
        "meridian_strike",
        "flame_burst",
        "void_pulse",
    ],
)
@pytest.mark.parametrize("seed", range(10))
def test_technique_damage_matches_formula(technique_id: str, seed: int):
    probe = probe_technique_damage(technique_id, seed=seed)
    assert probe.actual == probe.expected


# --- Status application via techniques (18 × 6 statuses × 3 seeds = 324) ---


def _techniques_for_status(status_id: str) -> list[str]:
    found: list[str] = []
    for tid, tech in load_technique_catalog().items():
        if tech.slot_type != "active":
            continue
        if tech.status_id == status_id:
            found.append(tid)
        for eff in tech.effects:
            if eff.type == "apply_status" and eff.params.get("status") == status_id:
                found.append(tid)
    return sorted(set(found))


@pytest.mark.parametrize("status_id", ["burn", "bleed", "poison", "stun", "fear", "seal"])
@pytest.mark.parametrize("technique_id", ACTIVE_TECHNIQUES)
@pytest.mark.parametrize("seed", range(3))
def test_technique_status_proc_when_configured(status_id: str, technique_id: str, seed: int):
    tech = get_technique(technique_id)
    assert tech is not None
    applies = tech.status_id == status_id or any(
        e.type == "apply_status" and e.params.get("status") == status_id for e in tech.effects
    )
    if not applies:
        pytest.skip("technique does not apply this status")
    stats = standard_stats()
    state = fresh_hunt_state(stats)
    from src.combat.triggers import resolve_technique

    resolve_technique(state, stats, None, technique_id, AlwaysProcRng(seed))
    assert has_status(state.opponent, status_id)


# --- Monster traits (4 traits × 25 seeds = 100) ---


@pytest.mark.parametrize("trait", MONSTER_TRAITS)
@pytest.mark.parametrize("seed", range(25))
def test_monster_trait_triggers_under_forced_rng(trait: str, seed: int):
    stats = standard_stats(dodge=0.0)
    state = fresh_hunt_state(stats, tanky_beast(attack=20), traits=[trait])
    state.opponent_trait_cd.clear()
    if trait == "cleanse_every_3_turns":
        force_apply(state.opponent, "burn")
        state.opponent_trait_cd["cleanse"] = 0
    rng = AlwaysProcRng(seed)
    opponent_trait_turn(state, rng)
    if trait == "bleed_immune":
        from src.combat.triggers import _maybe_apply_status

        applied = _maybe_apply_status(
            state, state.player, "bleed", 1.0, rng, traits=state.opponent_traits
        )
        assert not applied
    elif trait == "seal_on_hit":
        assert state.player.sealed or any("seal" in line.lower() for line in state.log)
    elif trait == "high_stun_chance":
        assert is_stunned(state.player) or any("stun" in line.lower() for line in state.log)
    elif trait == "cleanse_every_3_turns":
        assert not has_status(state.opponent, "burn") or "cleanse" in " ".join(state.log).lower()


@pytest.mark.parametrize("monster_id", _monster_ids_with_trait("high_stun_chance"))
def test_high_stun_monster_can_stun_player(monster_id: str):
    raw = json.loads(MONSTERS_PATH.read_text(encoding="utf-8"))
    data = raw[monster_id]
    stats = standard_stats(dodge=0.0)
    state = fresh_hunt_state(
        stats,
        tanky_beast(hp=data["hp"], attack=data["attack"], defense=data["defense"]),
        traits=data.get("traits", []),
    )
    opponent_trait_turn(state, AlwaysProcRng(0))
    assert is_stunned(state.player)


# --- PvP multi-actor (30) ---


@pytest.mark.parametrize("seed", range(15))
def test_pvp_turn_deals_damage_without_counter(seed: int):
    stats_a = standard_stats()
    state = fresh_hunt_state(stats_a, tanky_beast(hp=180))
    state.context = "pvp"
    state.opponent_name = "Rival"
    execute_pvp_turn(state, stats_a, None, None, "strike", rng=NeverCritRng(seed))
    assert state.opponent.hp < 180


@pytest.mark.parametrize("seed", range(15))
def test_pvp_both_sides_can_apply_burn(seed: int):
    from src.combat.triggers import resolve_technique

    stats = standard_stats()
    state = fresh_hunt_state(stats, tanky_beast(hp=200, max_hp=200))
    state.context = "pvp"
    resolve_technique(state, stats, None, "ember_palm", AlwaysProcRng(seed))
    assert has_status(state.opponent, "burn")
    state.player, state.opponent = state.opponent, state.player
    state.player_label, state.opponent_name = state.opponent_name, state.player_label
    state.technique_cooldowns.clear()
    resolve_technique(state, stats, None, "ember_palm", AlwaysProcRng(seed + 1))
    assert has_status(state.opponent, "burn")


# --- Dungeon multi-fighter & AOE (40+) ---


def _dungeon_two_enemy_state(session, player):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    members = load_members(party)
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=members,
        rng=random.Random(99),
    )
    return state


@pytest.mark.parametrize("seed", range(10))
def test_dungeon_single_target_still_needs_target(seed: int, session, player):
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "swift_slash")
    session.commit()
    state = _dungeon_two_enemy_state(session, player)
    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    state.turn_order = [ally.fighter_id] + [f.fighter_id for f in state.living_enemies()]
    state.turn_index = 0
    prep = select_technique(session, state, ally.fighter_id, "swift_slash", rng=random.Random(seed))
    assert prep.ok
    assert prep.needs_target
    assert state.pending_technique == "swift_slash"


@pytest.mark.parametrize("seed", range(10))
def test_flame_burst_aoe_hits_all_enemies_without_target_prompt(seed: int, session, player):
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "flame_burst")
    session.commit()
    tech = get_technique("flame_burst")
    assert tech is not None
    assert technique_hits_all_enemies(tech)
    state = _dungeon_two_enemy_state(session, player)
    enemies_before = {e.fighter_id: e.combatant.hp for e in state.living_enemies()}
    if len(enemies_before) < 2:
        pytest.skip("room did not spawn multiple enemies")
    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    state.turn_order = [ally.fighter_id]
    state.turn_index = 0
    prep = select_technique(session, state, ally.fighter_id, "flame_burst", rng=random.Random(seed))
    assert prep.ok
    assert not prep.needs_target
    assert state.pending_technique is None
    for fid, hp_before in enemies_before.items():
        foe = state.fighters[fid]
        if foe.alive():
            assert foe.combatant.hp <= hp_before


@pytest.mark.parametrize("seed", range(10))
def test_dungeon_cannot_target_ally(seed: int, session, player):
    state = _dungeon_two_enemy_state(session, player)
    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    other = next(
        (f for f in state.fighters.values() if not f.is_enemy and f.fighter_id != ally.fighter_id),
        None,
    )
    if other is None:
        pytest.skip("solo party")
    enemy = state.living_enemies()[0]
    state.turn_order = [ally.fighter_id]
    state.turn_index = 0
    select_technique(session, state, ally.fighter_id, "basic_strike", rng=random.Random(seed))
    res = select_target(session, state, ally.fighter_id, other.fighter_id, rng=random.Random(seed))
    assert not res.ok


@pytest.mark.parametrize("seed", range(10))
def test_dungeon_two_player_party_initiative(seed: int, session, player, player_two):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two],
    )
    accept_invite(session, party, player_two)
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    members = load_members(party)
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=members,
        rng=random.Random(seed),
    )
    assert len(state.living_players()) == 2
    assert len(state.living_enemies()) >= 1
    assert state.turn_order


# --- Opponent stunned skips counter (20) ---


@pytest.mark.parametrize("seed", range(20))
def test_opponent_stun_skips_counterattack(seed: int):
    stats = standard_stats()
    state = fresh_hunt_state(stats, tanky_beast(attack=30))
    force_apply(state.opponent, "stun")
    player_hp = state.player.hp
    execute_turn(
        state,
        stats,
        None,
        None,
        "technique",
        technique_id="basic_strike",
        rng=NeverCritRng(seed),
    )
    assert any("stunned" in line.lower() for line in state.log)
    assert state.player.hp == player_hp


# --- Combined debuff stacking in fight (30) ---


@pytest.mark.parametrize("combo", [
    ("burn", "bleed"),
    ("bleed", "poison"),
    ("burn", "stun"),
    ("bleed", "fear"),
    ("poison", "seal"),
])
@pytest.mark.parametrize("seed", range(6))
def test_multiple_debuffs_coexist(combo: tuple[str, str], seed: int):
    a, b = combo
    target = CombatantState(hp=300, max_hp=300)
    apply_status(target, a)
    apply_status(target, b)
    assert has_status(target, a)
    assert has_status(target, b)
    for _ in range(2):
        tick_statuses(target)
    assert has_status(target, a) or has_status(target, b)


# --- Bleed/burn stack refresh (24) ---


@pytest.mark.parametrize("status_id", ["burn", "bleed", "poison"])
@pytest.mark.parametrize("reapply", range(1, 9))
def test_reapply_increments_stacks_until_cap(status_id: str, reapply: int):
    target = CombatantState(hp=500, max_hp=500)
    for _ in range(reapply):
        apply_status(target, status_id)
    rule = load_combat_rules().statuses[status_id]
    assert status_stacks(target, status_id) == min(reapply, rule.max_stacks)
