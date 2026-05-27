from __future__ import annotations

import pytest

from src.config import Config
from src.effects import add_haste_effect
from src.cooldown_haste import HASTE_UNIVERSAL_EFFECT, get_haste_reduction_seconds


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


def test_void_pulse_applies_universal_haste(session, player, cfg):
    add_haste_effect(session, player.id, "void_pulse_pill")
    session.commit()

    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 3600
    assert get_haste_reduction_seconds(session, player.id, "adventure") == 3600
    assert get_haste_reduction_seconds(session, player.id, "dungeon") == 3600
    assert get_haste_reduction_seconds(session, player.id, "daily") == 3600
    assert get_haste_reduction_seconds(session, player.id, "gather") == 3600
    assert get_haste_reduction_seconds(session, player.id, "hunt") == 3600

    from sqlalchemy import select
    from src.models import PlayerEffect

    eff = session.execute(
        select(PlayerEffect).where(
            PlayerEffect.player_id == player.id,
            PlayerEffect.effect_id == HASTE_UNIVERSAL_EFFECT,
        )
    ).scalar_one()
    assert eff.charges == 2
