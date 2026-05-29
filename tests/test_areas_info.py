from __future__ import annotations

from src.areas_info import build_areas_embed
from src.content import load_all_content
from src.inventory import load_item_catalog
from src.models import Player


def test_areas_embed_lists_all_zones(player: Player):
    load_all_content()
    load_item_catalog()
    embed = build_areas_embed(player)
    assert embed.title == "Adventure Areas"
    assert len(embed.fields) >= 10
    names = " ".join(f.name for f in embed.fields)
    assert "Mortal Grove" in names
    assert "Foundation Ruins" in names
    assert "Heavenly Transcendence Domain" in names
    assert "Immortal Monarch Court" in names


def test_areas_embed_single_area_shows_rare_events(player: Player):
    load_all_content()
    load_item_catalog()
    embed = build_areas_embed(player, area_id="mortal_grove")
    assert embed.title == "Mortal Grove"
    rare_field = next(f for f in embed.fields if f.name == "Possible rare encounters")
    assert "wandering elder" in rare_field.value.lower() or "elder" in rare_field.value.lower()
