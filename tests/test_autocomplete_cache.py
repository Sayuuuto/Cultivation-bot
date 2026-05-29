from __future__ import annotations

import asyncio

from src.autocomplete_cache import get_area_autocomplete_options, invalidate_area_autocomplete_cache


def test_area_autocomplete_cache_returns_options(session, player, monkeypatch):
    def fake_fetch(guild_id: str, discord_id: str) -> int | None:
        if guild_id == player.guild_id and discord_id == player.discord_id:
            return player.realm_index
        return None

    monkeypatch.setattr("src.autocomplete_cache._fetch_player_realm_index", fake_fetch)

    async def run():
        opts = await get_area_autocomplete_options(player.guild_id, player.discord_id)
        assert opts is not None
        assert any(v == "mortal_grove" for v, _ in opts)
        cached = await get_area_autocomplete_options(player.guild_id, player.discord_id)
        assert cached == opts

    asyncio.run(run())


def test_area_autocomplete_unknown_player(session):
    async def run():
        opts = await get_area_autocomplete_options("test-guild", "nobody")
        assert opts is None

    asyncio.run(run())


def test_invalidate_cache(session, player):
    async def run():
        await get_area_autocomplete_options(player.guild_id, player.discord_id)
        invalidate_area_autocomplete_cache(player.guild_id, player.discord_id)

    asyncio.run(run())
