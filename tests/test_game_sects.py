from __future__ import annotations

from datetime import timedelta

from src.game_sects import (
    grant_sect_invitation,
    join_eligibility,
    join_game_sect,
    leave_game_sect,
    load_game_sects,
)
from src.karma import KARMA_DEMONIC_THRESHOLD, KARMA_RIGHTEOUS_THRESHOLD
from src.models import utcnow


def test_load_eight_game_sects():
    sects = load_game_sects()
    assert len(sects) == 8
    assert "wudang" in sects
    assert "imperial_guard" in sects
    assert sects["imperial_guard"].join_type == "secret"


def test_wudang_join_requires_neutral_or_righteous(session, player):
    player.karma = KARMA_DEMONIC_THRESHOLD
    ok, msg = join_eligibility(session, player, "wudang")
    assert not ok
    assert "righteous" in msg.lower() or "neutral" in msg.lower()

    player.karma = 0
    ok, _ = join_eligibility(session, player, "wudang")
    assert ok


def test_kunlun_requires_neutral_only(session, player):
    player.realm_index = 1
    player.karma = KARMA_RIGHTEOUS_THRESHOLD
    ok, _ = join_eligibility(session, player, "kunlun")
    assert not ok

    player.karma = 0
    ok, _ = join_eligibility(session, player, "kunlun")
    assert ok


def test_secret_sect_requires_invitation(session, player):
    player.realm_index = 3
    player.karma = 50
    ok, _ = join_eligibility(session, player, "imperial_guard")
    assert not ok

    grant_sect_invitation(session, player.id, "imperial_guard")
    session.flush()
    ok, _ = join_eligibility(session, player, "imperial_guard")
    assert ok


def test_join_and_leave_game_sect(session, player):
    player.karma = 0
    player.sect_merit = 100
    ok, msg = join_game_sect(session, player, "wudang")
    assert ok
    assert player.game_sect_id == "wudang"
    assert player.sect_merit == 0

    player.sect_merit = 80
    ok, leave_msg, lost = leave_game_sect(session, player)
    assert ok
    assert lost == 40
    assert player.sect_merit == 40
    assert player.game_sect_id is None
    assert player.sect_leave_cooldown_until is not None


def test_rejoin_blocked_during_cooldown(session, player):
    player.karma = 0
    join_game_sect(session, player, "wudang")
    leave_game_sect(session, player)
    ok, msg = join_eligibility(session, player, "shaolin")
    assert not ok
    assert "wait" in msg.lower()

    player.sect_leave_cooldown_until = utcnow() - timedelta(hours=1)
    ok, _ = join_eligibility(session, player, "shaolin")
    assert ok
