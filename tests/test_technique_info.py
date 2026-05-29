from __future__ import annotations

from src.combat.catalog import get_technique
from src.combat.loadout import learn_technique
from src.inventory import add_item
from src.item_info import build_item_detail_embed
from src.technique_info import (
    build_technique_detail_embed,
    format_art_type_label,
    format_technique_combat_summary,
    format_technique_base_power,
    list_technique_inspect_options,
    resolve_technique_inspect_target,
    technique_base_power,
)


def test_technique_inspect_lists_manual_before_learn(session, player):
    add_item(session, player.id, "manual_swift_slash", 1)
    session.commit()

    options = dict(list_technique_inspect_options(session, player.id))
    assert "manual_swift_slash" in options
    assert "Active" in options["manual_swift_slash"]


def test_technique_inspect_lists_learned_art(session, player):
    learn_technique(session, player.id, "swift_slash")
    session.commit()

    options = dict(list_technique_inspect_options(session, player.id))
    assert "swift_slash" in options
    assert "Active" in options["swift_slash"]


def test_resolve_manual_by_name(session, player):
    add_item(session, player.id, "manual_swift_slash", 1)
    session.commit()

    tech, manual_id = resolve_technique_inspect_target(session, player.id, "Swift Slash")
    assert tech is not None
    assert tech.technique_id == "swift_slash"
    assert manual_id == "manual_swift_slash"


def test_technique_detail_shows_art_type_not_synergy(session, player):
    add_item(session, player.id, "manual_swift_slash", 1)
    session.commit()

    tech, manual_id = resolve_technique_inspect_target(session, player.id, "manual_swift_slash")
    assert tech is not None
    embed = build_technique_detail_embed(
        tech, session=session, player_id=player.id, manual_item_id=manual_id
    )
    field_names = [f.name for f in embed.fields]
    assert "Art type" in field_names
    assert "⚔️ In combat" in field_names
    assert "💡 Synergy & pairings" not in field_names
    assert "Active art" in format_art_type_label(tech)
    assert "bleed" in format_technique_combat_summary(tech).lower()
    assert "Base power **10**" in format_technique_combat_summary(tech)
    assert technique_base_power(tech) == 10


def test_technique_detail_shows_base_power_for_iron_cleave(session, player):
    learn_technique(session, player.id, "iron_cleave")
    session.commit()
    tech = get_technique("iron_cleave")
    assert tech is not None
    summary = format_technique_combat_summary(tech)
    assert "Base power **20**" in summary
    assert "bleeding" in summary.lower()
    assert format_technique_base_power(tech) == (
        "Base power **20** · +**0.55** per **External Strength** · **+25% vs bleeding**"
    )


def test_item_detail_manual_shows_art_type(session, player):
    add_item(session, player.id, "manual_swift_slash", 1)
    session.commit()

    embed = build_item_detail_embed("manual_swift_slash", session=session, player_id=player.id)
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "Art type" in field_names
    assert "⚔️ In combat" in field_names
    assert "💡 Synergy & pairings" not in field_names
