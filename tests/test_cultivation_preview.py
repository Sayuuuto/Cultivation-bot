from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.character import get_character_modifiers
from src.config import Config
from src.cultivation_preview import preview_cultivate_qi, preview_passive_qi
from src.effects import add_effect
from src.game import compute_breakthrough_preview, qi_cap
from src.karma import karma_breakthrough_modifiers
from src.models import Player


@pytest.fixture
def cfg() -> Config:
    return Config(
        discord_token="test",
        guild_id="test-guild",
        database_path=":memory:",
        announce_channel_id=None,
        tutorial_channel_id=None,
        library_channel_id=None,
        abode_category_id=None,
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)


def test_preview_passive_qi_after_inactivity(session, player, cfg, now):
    player.last_active_at = now - timedelta(minutes=45)
    session.add(player)
    session.commit()

    qi, minutes, cap = preview_passive_qi(player, now, cfg.offline_cap_minutes)
    assert qi > 0
    assert minutes == 45
    assert cap == cfg.offline_cap_minutes


def test_preview_cultivate_qi_range(session, player, cfg, now):
    player.stamina = 100
    mod = get_character_modifiers(session, player)
    preview = preview_cultivate_qi(player, mod, cfg, now)

    assert preview.active_qi_min <= preview.active_qi_typical <= preview.active_qi_max
    assert preview.qi_cap == qi_cap(player.realm_index, player.substage)


def test_breakthrough_chance_righteous_higher_than_neutral(session, player, cfg):
    player.karma = 0
    player.qi = qi_cap(player.realm_index, player.substage)
    neutral = compute_breakthrough_preview(player, get_character_modifiers(session, player))

    player.karma = 40
    righteous = compute_breakthrough_preview(player, get_character_modifiers(session, player))

    assert righteous.success_chance > neutral.success_chance
    assert righteous.karma_bonus == karma_breakthrough_modifiers(40)[0]


def test_breakthrough_clarity_pill_increases_chance(session, player, cfg):
    player.qi = qi_cap(player.realm_index, player.substage)
    before = compute_breakthrough_preview(player, get_character_modifiers(session, player))

    add_effect(session, player.id, "clarity", charges=1)
    session.commit()
    after = compute_breakthrough_preview(player, get_character_modifiers(session, player))

    assert after.clarity_bonus > 0
    assert after.success_chance > before.success_chance


def test_breakthrough_preview_when_qi_not_full(session, player, cfg):
    preview = compute_breakthrough_preview(player, get_character_modifiers(session, player))
    assert preview.can_attempt is False
    assert preview.qi_required > player.qi
