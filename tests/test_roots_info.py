from __future__ import annotations

import pytest

from src.content import load_all_content
from src.game import SPIRIT_ROOTS
from src.roots_info import ROOT_TIERS, build_roots_embed, build_roots_tutorial_pages
from src.tutorial import build_tutorial_pages, validate_tutorial_pages


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()


def test_every_spirit_root_has_tier_entry():
    for root in SPIRIT_ROOTS:
        assert root in ROOT_TIERS


def test_build_roots_embed_respects_field_limits():
    embed = build_roots_embed(root_name=None)
    for field in embed.fields:
        assert len(field.value) <= 1024


def test_build_single_root_embed():
    embed = build_roots_embed(root_name="Pure Jade Root")
    assert "Pure Jade Root" in embed.title
    assert embed.fields


def test_tutorial_includes_roots_pages():
    pages = build_tutorial_pages()
    titles = " ".join(p.title or "" for p in pages).lower()
    assert "spirit root" in titles
    validate_tutorial_pages(pages)


def test_roots_tutorial_pages_validate():
    for page in build_roots_tutorial_pages():
        if page.description:
            assert len(page.description) <= 4096
        for field in page.fields:
            assert len(field.value) <= 1024
