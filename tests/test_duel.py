from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from src.character import get_character_modifiers
from src.config import Config
from src.game import duel, player_strength_for_pvp, utcnow
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
def opponent_player(session, player) -> Player:
    now = datetime.now(timezone.utc)
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
        stamina_last_updated_at=now,
        last_active_at=now,
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def test_duel_challenger_wins_with_low_roll(session, player, opponent_player, cfg):
    player.qi = 100
    opponent_player.qi = 10
    mod_a = get_character_modifiers(session, player)
    mod_b = get_character_modifiers(session, opponent_player)

    res = duel(player, opponent_player, cfg, rng=FixedRoll(0.01), challenger_mod=mod_a, opponent_mod=mod_b)

    assert res.success is True
    assert res.winner_discord_id == player.discord_id
    assert player.spirit_stones > 0
    assert opponent_player.spirit_stones == 5


def test_duel_opponent_wins_with_high_roll(session, player, opponent_player, cfg):
    player.spirit_stones = 0
    mod_a = get_character_modifiers(session, player)
    mod_b = get_character_modifiers(session, opponent_player)

    res = duel(player, opponent_player, cfg, rng=FixedRoll(0.99), challenger_mod=mod_a, opponent_mod=mod_b)

    assert res.success is False
    assert res.winner_discord_id == opponent_player.discord_id
    assert opponent_player.spirit_stones > 5


def test_duel_stronger_realm_favored(session, player, opponent_player, cfg):
    player.realm_index = 2
    player.substage = 2
    player.qi = 500
    opponent_player.realm_index = 0
    opponent_player.qi = 50

    mod_a = get_character_modifiers(session, player)
    mod_b = get_character_modifiers(session, opponent_player)
    strength_a = player_strength_for_pvp(player, mod_a)
    strength_b = player_strength_for_pvp(opponent_player, mod_b)
    assert strength_a > strength_b

    wins = 0
    for seed in range(100):
        p = Player(
            guild_id=player.guild_id,
            discord_id=player.discord_id,
            realm_index=player.realm_index,
            substage=player.substage,
            qi=player.qi,
            spirit_stones=0,
        )
        o = Player(
            guild_id=opponent_player.guild_id,
            discord_id=opponent_player.discord_id,
            realm_index=opponent_player.realm_index,
            substage=opponent_player.substage,
            qi=opponent_player.qi,
            spirit_stones=0,
        )
        res = duel(p, o, cfg, rng=random.Random(seed), challenger_mod=mod_a, opponent_mod=mod_b)
        if res.success:
            wins += 1
    assert wins > 50


def test_duel_persists_winner_stones_in_session(session, player, opponent_player, cfg):
    before_opp = opponent_player.spirit_stones
    mod_a = get_character_modifiers(session, player)
    mod_b = get_character_modifiers(session, opponent_player)

    duel(player, opponent_player, cfg, rng=FixedRoll(0.99), challenger_mod=mod_a, opponent_mod=mod_b)
    session.commit()
    session.refresh(opponent_player)

    assert opponent_player.spirit_stones > before_opp
