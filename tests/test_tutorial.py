from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from src.post_tutorial import post_tutorial
from src.tutorial import build_tutorial_intro_markdown, build_tutorial_pages, validate_tutorial_pages


def test_tutorial_pages_validate_under_discord_limits():
    pages = build_tutorial_pages()
    assert len(pages) >= 16
    validate_tutorial_pages(pages)


def test_tutorial_covers_core_topics():
    pages = build_tutorial_pages()
    parts: list[str] = []
    for page in pages:
        parts.append(page.title or "")
        parts.append(page.description or "")
        for field in page.fields:
            parts.append(field.name)
            parts.append(field.value)
    combined = " ".join(parts).lower()
    for keyword in (
        "adventure",
        "forge",
        "breakthrough",
        "dungeon",
        "cooldown",
        "craft",
        "techniques",
        "karma",
        "button combat",
        "post-library",
        "outer disciple trial",
        "remind",
        "offline",
        "sage of the bamboo",
        "4 active",
    ):
        assert keyword in combined


def test_tutorial_intro_uses_rich_formatting():
    intro = build_tutorial_intro_markdown()
    assert intro.startswith("# ")
    assert "-# " in intro
    assert ">" in intro
    assert len(intro) <= 2000


def test_tutorial_pages_use_visual_structure():
    pages = build_tutorial_pages()
    assert any(p.title and "Profile Dashboard" in p.title for p in pages)
    assert any(p.title and "Reminders" in p.title for p in pages)
    assert any(p.author and "Chapter" in p.author.name for p in pages if p.author)


def test_post_tutorial_clears_before_posting():
    channel = AsyncMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=MagicMock(pinned=False))
    channel.history = MagicMock(return_value=_async_iter([]))

    me = MagicMock()
    me.id = 999

    result = asyncio.run(post_tutorial(channel, clear_existing=True, me=me, pin_intro=False))

    assert result.deleted == 0
    assert result.posted >= 2
    assert channel.send.await_count >= 2


async def _async_iter(items):
    for item in items:
        yield item
