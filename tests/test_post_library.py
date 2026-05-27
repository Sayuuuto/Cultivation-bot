from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from src.post_library import clear_bot_messages, post_library


async def async_iter(items):
    for item in items:
        yield item


def test_clear_bot_messages_deletes_only_bot_authored_messages():
    bot_user = MagicMock()
    bot_user.id = 111

    other_message = MagicMock(author=MagicMock(id=222), pinned=False)
    bot_message = MagicMock(author=MagicMock(id=111), pinned=True)
    bot_message.unpin = AsyncMock()
    bot_message.delete = AsyncMock()

    async def history(limit=None):
        for message in (other_message, bot_message):
            yield message

    channel = MagicMock(spec=discord.TextChannel)
    channel.history = history

    deleted = asyncio.run(clear_bot_messages(channel, me=bot_user))

    assert deleted == 1
    bot_message.unpin.assert_awaited_once()
    bot_message.delete.assert_awaited_once()


def test_post_library_clears_before_posting_when_enabled():
    channel = AsyncMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=MagicMock(pinned=False))
    channel.history = MagicMock(return_value=async_iter([]))

    me = MagicMock()
    me.id = 999

    result = asyncio.run(post_library(channel, clear_existing=True, me=me, pin_intro=False))

    assert result.deleted == 0
    assert result.posted >= 2
    assert channel.send.await_count >= 2
