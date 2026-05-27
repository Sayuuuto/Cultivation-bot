from __future__ import annotations

from src.inventory import add_item, build_inventory_embed, get_player_inventory
from src.item_info import (
    build_item_detail_embed,
    format_manual_bind_progress,
    get_item_effect_text,
    get_item_quick_action,
    list_inventory_item_options,
    resolve_inventory_item_id,
)


def test_manual_bind_progress_with_fragments_only(session, player):
    add_item(session, player.id, "technique_fragment", 3)
    session.commit()

    progress = format_manual_bind_progress(session, player.id)
    assert progress is not None
    assert "3/3" in progress
    assert "Blank Scroll" in progress
    assert "Spirit Ink" in progress
    assert "/gather" in progress


def test_item_detail_for_fragment(session, player):
    add_item(session, player.id, "technique_fragment", 3)
    session.commit()

    embed = build_item_detail_embed("technique_fragment", session=session, player_id=player.id)
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    joined = " ".join(field_names)
    assert "Manual binding" in joined
    assert "Used in crafting" in joined
    assert "Quick action" in joined
    assert "How to obtain more" in joined


def test_inventory_does_not_show_bind_hints(session, player):
    add_item(session, player.id, "technique_fragment", 3)
    session.commit()

    stacks = get_player_inventory(session, player.id)
    embed = build_inventory_embed(player, stacks)
    body = (embed.description or "") + "\n".join(f.value for f in embed.fields)

    assert "Technique Fragment" in body
    assert "Manual binding" not in body
    assert "/craft" not in body


def test_item_detail_shows_pill_effect(session, player):
    add_item(session, player.id, "qi_gathering_pill", 2)
    session.commit()

    effect = get_item_effect_text("qi_gathering_pill")
    assert effect is not None
    assert "qi" in effect.lower()

    action = get_item_quick_action("qi_gathering_pill")
    assert action is not None
    assert action[0] == "/use"

    embed = build_item_detail_embed("qi_gathering_pill", session=session, player_id=player.id)
    assert embed is not None
    assert any(f.name == "⚡ Effect" for f in embed.fields)
    assert any("Quick action" in f.name for f in embed.fields)


def test_autocomplete_labels_are_names_only(session, player):
    add_item(session, player.id, "technique_fragment", 3)
    add_item(session, player.id, "green_dew_herb", 1)
    session.commit()

    options = list_inventory_item_options(session, player.id)
    assert options
    for _item_id, label in options:
        assert "—" not in label
        assert "/craft" not in label
        assert "×" in label


def test_resolve_inventory_item_by_name(session, player):
    add_item(session, player.id, "technique_fragment", 1)
    session.commit()

    assert resolve_inventory_item_id(session, player.id, "Technique Fragment") == "technique_fragment"
    assert resolve_inventory_item_id(session, player.id, "technique_fragment") == "technique_fragment"
