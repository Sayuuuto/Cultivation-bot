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
    assert "Whispering Bamboo Grove" in embed.fields[0].name
    assert "Ashen Cliff Pass" in embed.fields[1].name
    assert "Moonwell Ruins" in embed.fields[2].name
    assert "Green Dew Herb" in embed.fields[0].value


def test_areas_embed_single_area_shows_rare_events(player: Player):
    load_all_content()
    load_item_catalog()
    embed = build_areas_embed(player, area_id="bamboo_grove")
    assert embed.title == "Whispering Bamboo Grove"
    rare_field = next(f for f in embed.fields if f.name == "Possible rare encounters")
    assert "wandering elder" in rare_field.value.lower() or "elder" in rare_field.value.lower()
