from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from .area_risk import format_area_choice_label_for_realm
from .content import get_areas
from .db import get_session
from .models import Player

if TYPE_CHECKING:
    pass

_AREA_OPTIONS_TTL_SEC = 8.0
# guild_id, discord_id -> (monotonic_ts, realm_index, options)
_area_options_cache: dict[tuple[str, str], tuple[float, int, list[tuple[str, str]]]] = {}


def _build_area_options(realm_index: int) -> list[tuple[str, str]]:
    return [
        (area_id, format_area_choice_label_for_realm(realm_index, area))
        for area_id, area in get_areas().items()
    ]


def _fetch_player_realm_index(guild_id: str, discord_id: str) -> int | None:
    session = get_session()
    try:
        stmt = (
            select(Player.realm_index)
            .where(Player.guild_id == guild_id, Player.discord_id == discord_id)
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()
    finally:
        session.close()


def invalidate_area_autocomplete_cache(guild_id: str, discord_id: str) -> None:
    _area_options_cache.pop((guild_id, discord_id), None)


async def get_area_autocomplete_options(
    guild_id: str,
    discord_id: str,
) -> list[tuple[str, str]] | None:
    """
    Return area (id, label) pairs for autocomplete, or None if the player does not exist.
    Uses a short TTL cache and runs DB I/O in a worker thread.
    """
    key = (guild_id, discord_id)
    now = time.monotonic()
    cached = _area_options_cache.get(key)
    if cached is not None and now - cached[0] < _AREA_OPTIONS_TTL_SEC:
        return cached[2]

    realm_index = await asyncio.to_thread(_fetch_player_realm_index, guild_id, discord_id)
    if realm_index is None:
        return None

    options = _build_area_options(realm_index)
    _area_options_cache[key] = (now, realm_index, options)
    return options
