from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from src.combat.loadout import ensure_starter_techniques
from src.models import PendingDuel
from src.pvp_combat import (
    create_pvp_combat_state,
    process_pvp_action,
    roll_initiative,
    serialize_pvp_state,
)
from src.pvp_match import begin_pvp_match, finalize_pvp_match, load_pvp_match_state


class FixedRoll:
    def __init__(self, values: list[float]):
        self.values = list(values)
        self.index = 0

    def random(self) -> float:
        if self.index < len(self.values):
            value = self.values[self.index]
            self.index += 1
            return value
        return 0.5

    def randint(self, a: int, b: int) -> int:
        return a

    def choice(self, seq):
        return seq[0]


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def opponent_player(session, player):
    opp = player.__class__(
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
        stamina_last_updated_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)
    ensure_starter_techniques(session, opp.id)
    session.commit()
    return opp


def test_roll_initiative_uses_agility(session, player, opponent_player):
    rng = random.Random(7)
    player_state = create_pvp_combat_state(session, 1, player, opponent_player, rng).fighters[player.discord_id]
    opponent_state = create_pvp_combat_state(session, 2, player, opponent_player, rng).fighters[
        opponent_player.discord_id
    ]
    first, second, rolls = roll_initiative(player_state, opponent_state, rng)
    assert first in {player.discord_id, opponent_player.discord_id}
    assert second != first
    assert rolls[first] >= rolls[second] or first != second


def test_turns_alternate_without_double_actions(session, player, opponent_player):
    state = create_pvp_combat_state(session, 1, player, opponent_player, random.Random(1))
    actor_a = state.current_actor_id
    rng = FixedRoll([0.9, 0.9, 0.9, 0.9])
    result = process_pvp_action(
        session,
        state,
        actor_a,
        "technique",
        rng,
        technique_id="basic_strike",
    )
    assert result.ok
    assert result.state.current_actor_id != actor_a
    blocked = process_pvp_action(
        session,
        result.state,
        actor_a,
        "technique",
        rng,
        technique_id="basic_strike",
    )
    assert blocked.ok is False
    assert "turn" in blocked.message.lower()


def test_technique_can_end_match(session, player, opponent_player):
    state = create_pvp_combat_state(session, 1, player, opponent_player, random.Random(2))
    actor = state.current_actor_id
    defender = state.opponent_of(actor)
    defender.combatant.hp = 1
    rng = FixedRoll([0.9, 0.9])
    result = process_pvp_action(
        session,
        state,
        actor,
        "technique",
        rng,
        technique_id="basic_strike",
    )
    assert result.ok
    assert result.state.finished
    assert result.state.winner_discord_id == actor


def test_yield_ends_match(session, player, opponent_player):
    state = create_pvp_combat_state(session, 1, player, opponent_player, random.Random(2))
    actor = state.current_actor_id
    defender = state.opponent_of(actor)
    rng = FixedRoll([0.9])
    result = process_pvp_action(session, state, actor, "flee", rng)
    assert result.ok
    assert result.state.finished
    assert result.state.surrendered
    assert result.state.winner_discord_id == defender.discord_id


def test_accept_starts_active_match(session, player, opponent_player, now):
    challenge = PendingDuel(
        guild_id=player.guild_id,
        challenger_discord_id=player.discord_id,
        opponent_discord_id=opponent_player.discord_id,
        challenger_dao_name=player.dao_name,
        opponent_dao_name=opponent_player.dao_name,
        status="pending",
        created_at=now,
        expires_at=now,
    )
    session.add(challenge)
    session.commit()

    started = begin_pvp_match(session, challenge, player, opponent_player, random.Random(3), now)
    assert started.match.status == "active"
    assert challenge.status == "active"
    assert started.state.current_actor_id in {player.discord_id, opponent_player.discord_id}


def test_finalize_updates_records_and_rewards(session, player, opponent_player, cfg, now):
    challenge = PendingDuel(
        guild_id=player.guild_id,
        challenger_discord_id=player.discord_id,
        opponent_discord_id=opponent_player.discord_id,
        challenger_dao_name=player.dao_name,
        opponent_dao_name=opponent_player.dao_name,
        status="pending",
        created_at=now,
        expires_at=now,
    )
    session.add(challenge)
    session.commit()

    started = begin_pvp_match(session, challenge, player, opponent_player, random.Random(4), now)
    state = load_pvp_match_state(started.match)
    state.finished = True
    state.winner_discord_id = player.discord_id
    started.match.state_json = serialize_pvp_state(state)
    session.add(started.match)
    session.commit()

    before_stones = player.spirit_stones
    finalized = finalize_pvp_match(session, started.match, cfg, now)
    session.commit()

    assert finalized.winner is player
    assert player.spirit_stones > before_stones
    assert player.pvp_wins == 1
    assert opponent_player.pvp_losses == 1
    assert started.match.status == "completed"
