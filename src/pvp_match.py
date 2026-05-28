from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .config import Config
from .cooldown_haste import consume_haste_for_activity
from .game import collect_passive_qi, to_utc, utcnow
from .models import ActivePvpMatch, PendingDuel, Player
from .pvp_combat import PvpCombatState, create_pvp_combat_state, deserialize_pvp_state, serialize_pvp_state

PVP_MATCH_EXPIRY_MINUTES = 20


@dataclass(frozen=True)
class StartedPvpMatch:
    match: ActivePvpMatch
    state: PvpCombatState
    challenger: Player
    opponent: Player


@dataclass(frozen=True)
class FinalizedPvpMatch:
    match: ActivePvpMatch
    state: PvpCombatState
    challenger: Player
    opponent: Player
    winner: Player
    loser: Player
    stones_gain: int


def _stones_reward(winner: Player, winner_mod) -> int:
    base_stones = 10 + winner.realm_index * 2
    mult = 1.0 if winner_mod is None else getattr(winner_mod, "pvp_stones_mult", 1.0)
    return int(base_stones * mult)


def get_active_pvp_match_for_player(
    session: Session,
    guild_id: str,
    discord_id: str,
) -> ActivePvpMatch | None:
    stmt = (
        select(ActivePvpMatch)
        .where(
            ActivePvpMatch.guild_id == guild_id,
            ActivePvpMatch.status == "active",
            (
                (ActivePvpMatch.challenger_discord_id == discord_id)
                | (ActivePvpMatch.opponent_discord_id == discord_id)
            ),
        )
        .order_by(ActivePvpMatch.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def begin_pvp_match(
    session: Session,
    challenge: PendingDuel,
    challenger: Player,
    opponent: Player,
    rng: random.Random,
    now: datetime | None = None,
) -> StartedPvpMatch:
    now = to_utc(now or utcnow())
    match = ActivePvpMatch(
        guild_id=challenge.guild_id,
        pending_duel_id=challenge.id,
        challenger_player_id=challenger.id,
        opponent_player_id=opponent.id,
        challenger_discord_id=challenger.discord_id,
        opponent_discord_id=opponent.discord_id,
        challenger_dao_name=challenger.dao_name,
        opponent_dao_name=opponent.dao_name,
        status="active",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=PVP_MATCH_EXPIRY_MINUTES),
    )
    session.add(match)
    session.flush()

    state = create_pvp_combat_state(session, match.id, challenger, opponent, rng)
    match.state_json = serialize_pvp_state(state)
    challenge.status = "active"
    challenge.resolved_at = now
    session.add(match)
    session.add(challenge)
    return StartedPvpMatch(match=match, state=state, challenger=challenger, opponent=opponent)


def save_pvp_match_state(session: Session, match: ActivePvpMatch, state: PvpCombatState) -> None:
    match.state_json = serialize_pvp_state(state)
    match.updated_at = utcnow()
    session.add(match)


def load_pvp_match_state(match: ActivePvpMatch) -> PvpCombatState:
    return deserialize_pvp_state(match.state_json)


def finalize_pvp_match(
    session: Session,
    match: ActivePvpMatch,
    cfg: Config,
    now: datetime | None = None,
) -> FinalizedPvpMatch:
    now = to_utc(now or utcnow())
    state = load_pvp_match_state(match)
    if not state.finished or state.winner_discord_id is None:
        raise ValueError("PvP match is not finished.")

    challenger = session.get(Player, match.challenger_player_id)
    opponent = session.get(Player, match.opponent_player_id)
    if challenger is None or opponent is None:
        raise ValueError("PvP match players missing.")

    from .character import get_character_modifiers

    collect_passive_qi(
        challenger,
        now,
        cap_mult=get_character_modifiers(session, challenger).offline_cap_mult,
    )
    challenger.last_active_at = now

    winner = challenger if state.winner_discord_id == challenger.discord_id else opponent
    loser = opponent if winner is challenger else challenger
    winner_mod = get_character_modifiers(session, winner)
    stones_gain = _stones_reward(winner, winner_mod)
    winner.spirit_stones += stones_gain

    challenger.last_pvp_at = now
    opponent.last_pvp_at = now
    consume_haste_for_activity(session, challenger.id, "duel")
    consume_haste_for_activity(session, opponent.id, "duel")
    if winner is challenger:
        challenger.pvp_wins += 1
        opponent.pvp_losses += 1
    else:
        opponent.pvp_wins += 1
        challenger.pvp_losses += 1
    challenger.last_active_at = now
    opponent.last_active_at = now

    match.status = "completed"
    match.winner_discord_id = winner.discord_id
    match.updated_at = now
    session.add(challenger)
    session.add(opponent)
    session.add(match)

    if match.pending_duel_id is not None:
        challenge = session.get(PendingDuel, match.pending_duel_id)
        if challenge is not None:
            challenge.status = "completed"
            challenge.resolved_at = now
            session.add(challenge)

    return FinalizedPvpMatch(
        match=match,
        state=state,
        challenger=challenger,
        opponent=opponent,
        winner=winner,
        loser=loser,
        stones_gain=stones_gain,
    )
