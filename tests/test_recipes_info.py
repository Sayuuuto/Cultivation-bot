from __future__ import annotations

import pytest

from src.content import load_all_content
from src.inventory import load_item_catalog
from src.recipes_info import (
    DISCORD_FIELD_CHAR_LIMIT,
    _chunk_lines,
    build_recipes_embed,
)


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_chunk_lines_splits_when_over_budget():
    lines = ["x" * 500, "y" * 500, "z" * 100]
    chunks = _chunk_lines(lines, max_len=600)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 600


def test_build_recipes_embed_all_category_respects_field_limit():
    embed = build_recipes_embed(recipe_type=None)
    assert embed.fields
    for field in embed.fields:
        assert len(field.value) <= DISCORD_FIELD_CHAR_LIMIT


def test_build_recipes_embed_pill_category_respects_field_limit():
    embed = build_recipes_embed(recipe_type="pill")
    for field in embed.fields:
        assert len(field.value) <= DISCORD_FIELD_CHAR_LIMIT


def test_build_recipes_embed_forge_category_includes_forge_only():
    embed = build_recipes_embed(recipe_type="forge")
    names = [f.name for f in embed.fields]
    assert any("forge" in n.lower() for n in names)
    assert not any(n == "Pills" for n in names)
