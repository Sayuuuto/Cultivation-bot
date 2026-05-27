from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.character import get_character_modifiers
from src.config import Config
from src.cultivation_preview import preview_cultivate_qi, preview_passive_qi
from src.effects import add_effect
from src.game import compute_breakthrough_preview, passive_qi_per_minute, qi_cap
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
        dungeon_category_id=None,
        arena_category_id=None,
        pvp_results_channel_id=None,
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
    mod = get_character_modifiers(session, player)
    preview = preview_cultivate_qi(player, mod, cfg, now)

    assert preview.active_qi_min <= preview.active_qi_typical <= preview.active_qi_max
    assert preview.qi_cap == qi_cap(player.realm_index, player.substage)
    assert preview.passive_qi_per_minute == pytest.approx(passive_qi_per_minute(player.realm_index))


def test_passive_qi_per_minute_matches_offline_formula(player, cfg, now):
    player.last_active_at = now - timedelta(minutes=30)
    per_min = passive_qi_per_minute(player.realm_index)
    qi, minutes, _ = preview_passive_qi(player, now, cfg.offline_cap_minutes)
    assert minutes == 30
    assert qi == int(per_min * 30)


def test_mortal_breakthrough_base_is_ninety_percent(session, player, cfg):
    from src.realms import invalidate_realms_cache, realm_breakthrough_base_success

    invalidate_realms_cache()
    assert realm_breakthrough_base_success(0, 0) == pytest.approx(0.90, abs=0.001)

    player.qi = qi_cap(player.realm_index, player.substage)
    preview = compute_breakthrough_preview(player, get_character_modifiers(session, player))
    assert preview.base_success == pytest.approx(0.90, abs=0.001)
    assert preview.success_chance >= 0.90


def test_breakthrough_harder_in_higher_realms(session, player, cfg):
    player.qi = 50_000
    mod = get_character_modifiers(session, player)

    player.realm_index = 0
    player.substage = 0
    early = compute_breakthrough_preview(player, mod)

    player.realm_index = 8
    player.substage = 0
    late = compute_breakthrough_preview(player, mod)

    assert early.base_success >= 0.88
    assert late.base_success <= 0.50
    assert late.success_chance < early.success_chance - 0.25


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
    after = compute_breakthrough_preview(
        player, get_character_modifiers(session, player), session=session, player_id=player.id
    )

    assert after.clarity_bonus > 0
    assert after.clarity_charges == 1
    assert after.success_chance > before.success_chance


def test_breakthrough_clarity_stacks_near_cap(session, player, cfg):
    player.qi = qi_cap(player.realm_index, player.substage)
    add_effect(session, player.id, "clarity", charges=3)
    session.commit()
    preview = compute_breakthrough_preview(
        player, get_character_modifiers(session, player), session=session, player_id=player.id
    )
    assert preview.success_chance >= 0.90
    assert preview.clarity_charges == 3


def test_qi_gathering_pill_stacks_charges(session, player):
    add_effect(session, player.id, "qi_gathering", charges=3)
    add_effect(session, player.id, "qi_gathering", charges=3)
    session.commit()
    from src.effects import get_effect_charges

    assert get_effect_charges(session, player.id, "qi_gathering") == 6


def test_breakthrough_preview_when_qi_not_full(session, player, cfg):
    preview = compute_breakthrough_preview(player, get_character_modifiers(session, player))
    assert preview.can_attempt is False
    assert preview.qi_required > player.qi
