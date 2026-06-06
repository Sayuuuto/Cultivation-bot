from __future__ import annotations

from src.character import get_character_modifiers
from src.combat_stats import compute_combat_stats, gather_quantity_bonus, scale_monster_stats
from src.stats import format_gear_summary, format_stats_summary


def test_combat_stats_scale_with_realm(session, player):
    mod = get_character_modifiers(session, player)
    low = compute_combat_stats(player, session, mod)

    player.realm_index = 2
    player.substage = 2
    session.commit()

    high = compute_combat_stats(player, session, mod)
    assert high.hp > low.hp
    assert high.qi_power > low.qi_power
    assert high.armor > low.armor


def test_realm_breakthrough_is_decisive(session, player):
    mod = get_character_modifiers(session, player)
    player.realm_index = 0
    player.substage = 2
    mortal_late = compute_combat_stats(player, session, mod)

    player.realm_index = 1
    player.substage = 0
    qi_early = compute_combat_stats(player, session, mod)

    assert qi_early.hp > mortal_late.hp * 5
    assert qi_early.might > mortal_late.might * 5
    assert qi_early.armor > mortal_late.armor * 5


def test_immortal_monarch_stats_reach_billions(session, player):
    mod = get_character_modifiers(session, player)
    player.realm_index = 9
    player.substage = 0

    stats = compute_combat_stats(player, session, mod)
    monarch_beast = scale_monster_stats(55, 12, 4, realm_index=9)

    assert stats.hp >= 1_000_000_000
    assert monarch_beast["hp"] >= 1_000_000_000


def test_gather_bonus_from_comprehension():
    low = gather_quantity_bonus(10)
    high = gather_quantity_bonus(30)
    assert high > low


def test_combat_stats_include_gear(session, player):
    from src.forge import forge_and_equip
    from src.inventory import add_item

    add_item(session, player.id, "minor_beast_core", 2)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()
    forge_and_equip(session, player, "weapon", rng=__import__("random").Random(1))
    session.commit()

    mod = get_character_modifiers(session, player)
    bare = compute_combat_stats(player, session, mod)
    assert bare.qi_power >= 10


def test_stats_summary_shows_realm_and_gear_columns(session, player):
    text = format_stats_summary(session, player.id)

    assert "Final" in text
    assert "Realm" in text
    assert "Gear" in text
    assert "Qi Power" in text
    assert "Crit" in text


def test_gear_summary_handles_empty_gear(session, player):
    text = format_gear_summary(session, player.id)

    assert "Empty" in text or "Nothing worn" in text
    assert "/forge" in text
    assert "/equip" in text
