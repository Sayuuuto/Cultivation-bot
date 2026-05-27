from __future__ import annotations

import pytest

from src.config import Config
from src.effects import add_void_pulse_haste
from src.cooldown_haste import get_haste_reduction_seconds


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


def test_void_pulse_applies_all_haste(session, player, cfg):
    add_void_pulse_haste(session, player.id)
    session.commit()

    assert get_haste_reduction_seconds(session, player.id, "cultivate") == 420
    assert get_haste_reduction_seconds(session, player.id, "adventure") == 600
    assert get_haste_reduction_seconds(session, player.id, "dungeon") == 1800
    assert get_haste_reduction_seconds(session, player.id, "duel") == 3600
    assert get_haste_reduction_seconds(session, player.id, "gather") == 180
    assert get_haste_reduction_seconds(session, player.id, "hunt") == 180
