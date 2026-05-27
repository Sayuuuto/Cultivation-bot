from __future__ import annotations

from src.character import get_character_modifiers
from src.combat_stats import compute_combat_stats, gather_quantity_bonus


def test_combat_stats_scale_with_realm(session, player):
    mod = get_character_modifiers(session, player)
    low = compute_combat_stats(player, session, mod)

    player.realm_index = 2
    player.substage = 2
    session.commit()

    high = compute_combat_stats(player, session, mod)
    assert high.hp > low.hp
    assert high.internal_strength > low.internal_strength
    assert high.defense > low.defense


def test_gather_bonus_from_comprehension():
    low = gather_quantity_bonus(10)
    high = gather_quantity_bonus(30)
    assert high > low


def test_combat_stats_include_gear(session, player):
    from src.forge import forge_equipment
    from src.inventory import add_item

    add_item(session, player.id, "spirit_iron_shard", 3)
    add_item(session, player.id, "minor_beast_core", 2)
    session.commit()
    forge_equipment(session, player.id, "weapon", rng=__import__("random").Random(1))
    session.commit()

    mod = get_character_modifiers(session, player)
    bare = compute_combat_stats(player, session, mod)
    assert bare.internal_strength >= 10
