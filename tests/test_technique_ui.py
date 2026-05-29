from __future__ import annotations

from src.combat.loadout import (
    equip_technique,
    ensure_starter_techniques,
    get_loadout,
    learn_technique,
    unequip_slot,
)
from src.combat.technique_ui import build_my_skills_embed, build_my_skills_embeds
from src.combat.catalog import get_technique
from src.content import load_all_content
from src.technique_info import (
    format_technique_effect_plain,
    technique_base_power,
)
from src.ui.combat_skills_card import build_combat_skills_card_data, render_combat_skills_card


def test_combat_skills_card_shows_slots(session, player):
    load_all_content()
    ensure_starter_techniques(session, player.id)
    data = build_combat_skills_card_data(session, player)
    labels = [s.slot_label for s in data.slots]
    assert labels == ["Slot 1", "Slot 2", "Slot 3", "Slot 4", "Passive"]
    png = render_combat_skills_card(data)
    assert len(png) > 500
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_format_technique_effect_plain_active_damage(session, player):
    load_all_content()
    tech = get_technique("basic_strike")
    assert tech is not None
    text = format_technique_effect_plain(tech)
    assert "Base power 4" in text
    assert "External Strength" in text
    assert technique_base_power(tech) == 4


def test_technique_base_power_reflects_rarity_and_multi_hit():
    load_all_content()
    basic = get_technique("basic_strike")
    swift = get_technique("swift_slash")
    iron = get_technique("iron_cleave")
    flurry = get_technique("rending_flurry")
    assert basic is not None and swift is not None and iron is not None and flurry is not None
    assert technique_base_power(basic) == 4
    assert technique_base_power(swift) == 10
    assert technique_base_power(iron) == 20  # 18 × rare multiplier
    assert technique_base_power(flurry) == 10  # 8 × 1.12 × 2 × 0.55
    assert technique_base_power(basic) < technique_base_power(swift) < technique_base_power(iron)


def test_replace_slot_keeps_old_technique_learned(session, player):
    load_all_content()
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "swift_slash")
    learn_technique(session, player.id, "soul_needle")
    session.commit()

    ok, msg = equip_technique(session, player, "swift_slash", "2")
    assert ok
    ok2, msg2 = equip_technique(session, player, "soul_needle", "2")
    assert ok2
    assert "swift" in msg2.lower() or "Swift" in msg2
    assert "My Skills" in msg2 or "still" in msg2.lower()

    loadout = get_loadout(session, player.id)
    assert loadout.get("2") == "soul_needle"


def test_build_my_skills_embed_ready_to_equip(session, player):
    load_all_content()
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "swift_slash")
    session.commit()
    embed = build_my_skills_embed(session, player)
    assert embed.title and "My Skills" in embed.title
    assert "active" in embed.description.lower()


def test_build_my_skills_embeds_groups_active_passive_by_realm(session, player):
    load_all_content()
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "swift_slash")
    learn_technique(session, player.id, "ember_heart")
    learn_technique(session, player.id, "iron_cleave")
    session.commit()

    embeds = build_my_skills_embeds(session, player)
    assert len(embeds) >= 2
    assert "My Skills" in embeds[0].title

    mortal = next(e for e in embeds[1:] if e.title == "Mortal")
    assert "⚔️ Active" in mortal.description
    assert "💠 Passive" in mortal.description
    assert "Swift Slash" in mortal.description
    assert "Ember Heart" in mortal.description

    qi_refining = next(e for e in embeds[1:] if e.title == "Qi Refining")
    assert "⚔️ Active" in qi_refining.description
    assert "Iron Cleave" in qi_refining.description
    assert "💠 Passive" not in qi_refining.description or "Passive (0)" not in qi_refining.description

    assert mortal.color.value != qi_refining.color.value


def test_unequip_slot_clears_loadout(session, player):
    load_all_content()
    ensure_starter_techniques(session, player.id)
    session.commit()
    loadout = get_loadout(session, player.id)
    assert loadout.get("1") == "basic_strike"
    ok, _ = unequip_slot(session, player.id, "1")
    assert ok
    session.commit()
    assert get_loadout(session, player.id).get("1") is None
