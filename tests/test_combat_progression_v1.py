from __future__ import annotations

from src.combat.effects import CombatantState, apply_status, status_application_chance
from src.combat.learn import learn_technique_from_manual
from src.combat.loadout import (
    equip_technique,
    learn_technique,
    list_pvp_loadout_violations,
    validate_loadout_budget,
)
from src.combat.catalog import load_technique_catalog
from src.duel_challenges import create_duel_challenge
from src.drop_sources import get_drop_sources
from src.notifications import count_unlockable_sealed_manuals, refresh_sealed_manual_notification
from src.combat.ranks import upgrade_technique_rank
from src.inventory import add_item, get_item_quantity
from src.manuals import grant_manual_drop, sealed_manual_item_id
from scripts.balance_simulator import build_report
from scripts.extract_skill_ideas import normalize_source


def test_skill_source_normalization_maps_existing_sects():
    source = normalize_source("SECT-BLOOD", {"blood_lotus", "tang"})
    assert source["taxonomy"] == "sect"
    assert source["sect_id"] == "blood_lotus"

    unknown = normalize_source("SECT-BEGGAR", {"blood_lotus", "tang"})
    assert unknown["taxonomy"] == "backlog"
    assert unknown["reason"] == "unknown_sect"


def test_load_budget_blocks_overloaded_realm(session, player):
    learn_technique(session, player.id, "heavens_cleave")
    learn_technique(session, player.id, "undying_vow")
    player.realm_index = 9
    ok, _ = equip_technique(session, player, "heavens_cleave", "2")
    assert ok
    ok, _ = equip_technique(session, player, "undying_vow", "passive")
    assert ok

    player.realm_index = 0
    ok, msg = validate_loadout_budget(session, player)
    assert not ok
    assert "load cap" in msg.lower()


def test_sealed_manual_unseals_at_required_realm(session, player):
    drops: dict[str, int] = {}
    msg = grant_manual_drop(session, player.id, "manual_iron_cleave", drops)
    sealed_id = sealed_manual_item_id("manual_iron_cleave")
    assert sealed_id in drops
    assert "higher realm" in msg.lower()

    add_item(session, player.id, sealed_id, 1)
    ok, locked_msg = learn_technique_from_manual(session, player.id, sealed_id)
    assert not ok
    assert "higher realm" in locked_msg.lower()

    player.realm_index = 1
    ok, _ = learn_technique_from_manual(session, player.id, sealed_id)
    assert ok
    assert get_item_quantity(session, player.id, sealed_id) == 0


def test_technique_rank_upgrade_spends_cost(session, player):
    learn_technique(session, player.id, "swift_slash")
    player.spirit_stones = 1000
    add_item(session, player.id, "spirit_iron_shard", 10)
    ok, msg = upgrade_technique_rank(session, player, "swift_slash")
    assert ok
    assert "rank **2**" in msg


def test_control_status_diminishing_returns_reduces_chance():
    target = CombatantState(hp=100, max_hp=100)
    before = status_application_chance(target, "stun", 1.0)
    apply_status(target, "stun")
    after = status_application_chance(target, "stun", 1.0)
    assert before == 1.0
    assert after < before


def test_balance_report_smoke():
    report = build_report(0, rounds=5, seed=1, limit=3)
    assert report["builds"]
    assert report["realm_index"] == 0


def test_balance_report_fragment_economy():
    report = build_report(0, rounds=3, seed=1, limit=2, include_economy=True)
    assert "fragment_economy" in report
    assert "craft_manual" in report["fragment_economy"]["sinks"]


def test_duel_create_blocks_illegal_loadout(session, player, player_two):
    from src.config import get_config

    player.realm_index = 9
    for tid in ("heavens_cleave", "undying_vow", "iron_will", "lotus_revival"):
        learn_technique(session, player.id, tid)
    equip_technique(session, player, "heavens_cleave", "1")
    equip_technique(session, player, "iron_will", "2")
    equip_technique(session, player, "lotus_revival", "3")
    equip_technique(session, player, "undying_vow", "passive")
    session.flush()
    violations = list_pvp_loadout_violations(session, player)
    assert violations
    cfg = get_config()
    challenge, err = create_duel_challenge(
        session, player.guild_id, player, player_two, cfg
    )
    assert challenge is None
    assert err is not None
    assert "legendary" in err.lower() or "loadout" in err.lower() or "healing" in err.lower()


def test_sealed_manual_unlock_notification(session, player):
    sealed_id = sealed_manual_item_id("manual_iron_cleave")
    add_item(session, player.id, sealed_id, 1)
    session.flush()
    player.realm_index = 0
    assert count_unlockable_sealed_manuals(session, player) == 0
    player.realm_index = 1
    session.add(player)
    session.flush()
    assert count_unlockable_sealed_manuals(session, player) == 1
    line = refresh_sealed_manual_notification(session, player)
    assert line is not None
    assert "learn" in line.lower()


def test_rank_materials_have_drop_sources():
    for item_id in ("spirit_iron_shard", "minor_beast_core", "ember_moss", "ancient_dust", "moonlotus"):
        assert get_drop_sources(item_id), f"missing drop sources for {item_id}"


def test_techniques_without_karma_on_use_tag():
    for tech in load_technique_catalog().values():
        for effect in tech.effects:
            if effect.type == "adjust_karma":
                assert "karma_on_use" in tech.tags, f"{tech.technique_id} adjust_karma needs karma_on_use tag"
