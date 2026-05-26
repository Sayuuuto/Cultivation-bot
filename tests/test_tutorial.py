from __future__ import annotations

import pytest

from src.tutorial import build_tutorial_pages, validate_tutorial_pages


def test_tutorial_pages_validate_under_discord_limits():
    pages = build_tutorial_pages()
    assert len(pages) >= 10
    validate_tutorial_pages(pages)


def test_tutorial_covers_core_topics():
    pages = build_tutorial_pages()
    combined = " ".join((p.title or "") + (p.description or "") for p in pages).lower()
    for keyword in ("adventure", "forge", "breakthrough", "dungeon", "cooldown", "craft"):
        assert keyword in combined
