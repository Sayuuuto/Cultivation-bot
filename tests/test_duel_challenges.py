from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from src.config import Config
from src.duel_challenges import (
    DUEL_CHALLENGE_TIMEOUT_SECONDS,
    accept_duel_challenge,
    create_duel_challenge,
    decline_duel_challenge,
    expire_duel_challenge,
    find_active_pending_between,
    get_valid_pending_challenge,
)
from src.models import Player


class FixedRoll:
    def __init__(self, roll: float):
        self._roll = roll

    def random(self) -> float:
        return self._roll


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


@pytest.fixture
def opponent_player(session, player) -> Player:
    opp = Player(
        guild_id=player.guild_id,
        discord_id="opponent-discord-id",
        discord_username="Opponent",
        dao_name="RivalDao",
        origin="Waterside Vow",
        spirit_root="Pure Jade Root",
        moral_path="neutral",
        realm_index=0,
        substage=0,
        qi=80,
        spirit_stones=5,
        stamina=100,
        stamina_last_updated_at=player.last_active_at,
        last_active_at=player.last_active_at,
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def test_create_duel_challenge(session, player, opponent_player, cfg, now):
    challenge, err = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    assert err is None
    assert challenge is not None
    assert challenge.status == "pending"
    assert challenge.challenger_discord_id == player.discord_id
    assert challenge.opponent_discord_id == opponent_player.discord_id
    assert challenge.expires_at == now + timedelta(seconds=DUEL_CHALLENGE_TIMEOUT_SECONDS)


def test_create_blocks_duplicate_pending(session, player, opponent_player, cfg, now):
    create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()

    _, err = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    assert err is not None
    assert "pending" in err.lower()


def test_create_blocks_when_challenger_on_cooldown(session, player, opponent_player, cfg, now):
    player.last_pvp_at = now - timedelta(minutes=30)
    session.add(player)
    session.commit()

    _, err = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    assert err is not None
    assert "cooling down" in err.lower()


def test_accept_runs_duel_and_marks_completed(session, player, opponent_player, cfg, now):
    challenge, _ = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()
    assert challenge is not None

    before_opp_stones = opponent_player.spirit_stones
    executed, err = accept_duel_challenge(
        session,
        challenge.id,
        opponent_player.discord_id,
        player,
        opponent_player,
        cfg,
        FixedRoll(0.99),
        now,
    )
    assert err is None
    assert executed is not None
    assert executed.result.success is False
    assert opponent_player.spirit_stones > before_opp_stones
    session.commit()
    session.refresh(challenge)
    assert challenge.status == "completed"


def test_decline_marks_challenge_declined(session, player, opponent_player, cfg, now):
    challenge, _ = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()
    assert challenge is not None

    updated, err = decline_duel_challenge(session, challenge.id, opponent_player.discord_id, now)
    assert err is None
    assert updated is not None
    assert updated.status == "declined"


def test_only_opponent_can_accept(session, player, opponent_player, cfg, now):
    challenge, _ = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()
    assert challenge is not None

    _, err = accept_duel_challenge(
        session,
        challenge.id,
        player.discord_id,
        player,
        opponent_player,
        cfg,
        random.Random(1),
        now,
    )
    assert err is not None
    assert "challenged daoist" in err.lower()


def test_expired_challenge_cannot_be_accepted(session, player, opponent_player, cfg, now):
    challenge, _ = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()
    assert challenge is not None

    later = now + timedelta(seconds=DUEL_CHALLENGE_TIMEOUT_SECONDS + 1)
    _, err = accept_duel_challenge(
        session,
        challenge.id,
        opponent_player.discord_id,
        player,
        opponent_player,
        cfg,
        random.Random(1),
        later,
    )
    assert err is not None
    assert "expired" in err.lower()


def test_expire_duel_challenge(session, player, opponent_player, cfg, now):
    challenge, _ = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()
    assert challenge is not None

    expired = expire_duel_challenge(session, challenge.id, now + timedelta(minutes=5))
    assert expired is not None
    assert expired.status == "expired"


def test_find_active_pending_between(session, player, opponent_player, cfg, now):
    challenge, _ = create_duel_challenge(session, player.guild_id, player, opponent_player, cfg, now)
    session.commit()
    assert challenge is not None

    found = find_active_pending_between(
        session,
        player.guild_id,
        player.discord_id,
        opponent_player.discord_id,
        now,
    )
    assert found is not None
    assert found.id == challenge.id

    valid, err = get_valid_pending_challenge(session, challenge.id, now)
    assert err is None
    assert valid is not None
