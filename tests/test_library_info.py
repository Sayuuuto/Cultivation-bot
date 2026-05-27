from __future__ import annotations

from src.library_info import (
    _technique_bullet,
    _technique_card,
    build_library_embeds,
    build_manual_catalog,
    build_master_catalog_text,
)


def test_technique_card_uses_rich_discord_formatting():
    entry = build_manual_catalog()[0]
    card = _technique_card(entry)
    assert "**Tags**" in card
    assert "**Description**" in card
    assert "**Obtain**" in card
    assert card.startswith("**Tags**") or "**Tags**" in card
    assert ">" in card  # block quote
    assert "-# " in card  # subtext
    assert "`" in card  # inline code chips
    assert "```" not in card


def test_technique_bullet_uses_visual_marker_and_quote():
    entry = build_manual_catalog()[0]
    bullet = _technique_bullet(entry)
    assert bullet.startswith("▸ **")
    assert ">" in bullet
    assert "```" not in bullet


def test_manual_catalog_covers_all_technique_manuals():
    entries = build_manual_catalog()
    assert len(entries) == 28
    assert all(entry.technique.manual_item_id for entry in entries)


def test_master_catalog_text_lists_every_manual_with_sections():
    text = build_master_catalog_text()
    for entry in build_manual_catalog():
        assert entry.technique.name in text
    assert "SWORD" in text or "⚔️" in text
    assert "-# " in text
    assert "```" not in text


def test_library_embeds_have_category_colors_and_rich_sections():
    embeds = build_library_embeds()
    titles = [embed.title for embed in embeds]
    assert any(title and "Master Catalog" in title for title in titles)
    assert any(title and "Sword Manuals" in title for title in titles)
    assert any(title and "Passives Manuals" in title for title in titles)
    assert any(title and "Elite Hunt" in title for title in titles)
    assert any(embed.color.value == 0xE74C3C for embed in embeds)  # sword red
    combined = "\n".join((embed.description or "") for embed in embeds)
    assert ">" in combined
    assert "```" not in combined
