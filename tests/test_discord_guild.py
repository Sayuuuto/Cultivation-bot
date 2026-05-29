from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import discord

from src.discord_guild import (
    _abode_overwrites,
    _everyone_overwrite_target,
    abode_channel_slug,
    ensure_realm_role,
    realm_role_name,
)
from src.guidance import get_abode_welcome_intro
from src.realms import get_realm_names


def test_abode_channel_slug_uses_dao_name():
    assert abode_channel_slug("Cloud Walker") == "abode-cloud-walker"


def test_abode_channel_slug_strips_invalid_characters():
    assert abode_channel_slug("Li *Mei*!!!") == "abode-li-mei"


def test_abode_channel_slug_fallback_when_empty():
    assert abode_channel_slug("   ") == "abode-cultivator"


def test_abode_channel_slug_respects_max_length():
    long_name = "a" * 120
    slug = abode_channel_slug(long_name, max_len=100)
    assert slug.startswith("abode-")
    assert len(slug) <= 100


def test_realm_role_name_matches_config():
    assert realm_role_name(0) == "Mortal"
    assert realm_role_name(1) == "Qi Refining"
    assert set(get_realm_names()) == {
        "Mortal",
        "Qi Refining",
        "Foundation Establishment",
        "Core Formation",
        "Nascent Soul",
        "Spirit Severing",
        "Void Refinement",
        "Immortal Ascension",
        "Heavenly Transcendence",
        "Immortal Monarch",
    }


def test_everyone_overwrite_target_when_role_uncached():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1509267117336432640
    guild.default_role = None
    target = _everyone_overwrite_target(guild)
    assert isinstance(target, discord.Object)
    assert target.id == guild.id


def test_abode_overwrites_never_use_none_targets():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1509267117336432640
    guild.default_role = None
    guild.me = MagicMock()
    guild.me.id = 1509850678703423608
    member = MagicMock(spec=discord.Member)
    member.id = 329262189678559233
    overwrites = _abode_overwrites(guild, member)
    assert None not in overwrites
    assert all(getattr(target, "id", None) is not None for target in overwrites)


def test_ensure_realm_role_creates_only_target():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1
    guild.roles = []
    created: list[str] = []

    async def _create_role(*, name: str, **kwargs):
        created.append(name)
        role = MagicMock(spec=discord.Role)
        role.name = name
        return role

    guild.create_role = _create_role
    role = asyncio.run(ensure_realm_role(guild, 0))
    assert role is not None
    assert created == ["Mortal"]

    guild.roles = [role]
    again = asyncio.run(ensure_realm_role(guild, 0))
    assert again is role
    assert created == ["Mortal"]


def test_abode_welcome_intro_is_in_world():
    text = get_abode_welcome_intro("Cloud Walker").lower()
    assert "cloud walker" in text
    for phrase in ("mvp", "scaffold", "legacy", "not chosen", "not at"):
        assert phrase not in text
    assert "/daily" in text
    assert "/profile" in text
