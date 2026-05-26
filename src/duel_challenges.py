from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .cooldown_haste import consume_haste_for_activity, cooldown_remaining_with_haste, get_haste_reduction_seconds
from .character import get_character_modifiers
from .config import Config
from .game import (
    DuelResult,
    apply_offline_progress,
    apply_stamina_regen,
    duel,
    to_utc,
    utcnow,
)
from .models import PendingDuel, Player

DUEL_CHALLENGE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ExecutedDuel:
    result: DuelResult
    challenger: Player
    opponent: Player


def _cooldown_remaining(
    session: Session | None,
    player: Player | None,
    activity: str,
    now: datetime,
    last: datetime | None,
    cooldown_seconds: int,
) -> int:
    if last is None:
        return 0
    elapsed = (to_utc(now) - to_utc(last)).total_seconds()
    remaining = int(cooldown_seconds - elapsed)
    remaining = max(0, remaining)
    if session is not None and player is not None:
        haste = get_haste_reduction_seconds(session, player.id, activity)
        remaining = cooldown_remaining_with_haste(remaining, haste)
    return remaining


def _format_cooldown_block(name: str, seconds: int) -> str:
    minutes = max(1, seconds // 60) if seconds >= 60 else 0
    if minutes:
        return f"**{name}** waits **{minutes} min**"
    return f"**{name}** waits **{seconds}s**"


def find_active_pending_for_player(
    session: Session,
    guild_id: str,
    discord_id: str,
    now: datetime | None = None,
) -> PendingDuel | None:
    now = to_utc(now or utcnow())
    stmt = (
        select(PendingDuel)
        .where(
            PendingDuel.guild_id == guild_id,
            PendingDuel.status == "pending",
            PendingDuel.expires_at > now,
            or_(
                PendingDuel.challenger_discord_id == discord_id,
                PendingDuel.opponent_discord_id == discord_id,
            ),
        )
        .order_by(PendingDuel.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def find_active_pending_between(
    session: Session,
    guild_id: str,
    discord_id_a: str,
    discord_id_b: str,
    now: datetime | None = None,
) -> PendingDuel | None:
    now = to_utc(now or utcnow())
    stmt = (
        select(PendingDuel)
        .where(
            PendingDuel.guild_id == guild_id,
            PendingDuel.status == "pending",
            PendingDuel.expires_at > now,
            or_(
                (
                    (PendingDuel.challenger_discord_id == discord_id_a)
                    & (PendingDuel.opponent_discord_id == discord_id_b)
                ),
                (
                    (PendingDuel.challenger_discord_id == discord_id_b)
                    & (PendingDuel.opponent_discord_id == discord_id_a)
                ),
            ),
        )
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def validate_duel_participants(
    session: Session,
    challenger: Player,
    opponent: Player,
    cfg: Config,
    now: datetime,
) -> str | None:
    if challenger.discord_id == opponent.discord_id:
        return "You cannot duel yourself."

    blocks: list[str] = []
    challenger_remaining = _cooldown_remaining(
        session, challenger, "duel", now, challenger.last_pvp_at, cfg.pvp_cooldown_seconds
    )
    opponent_remaining = _cooldown_remaining(
        session, opponent, "duel", now, opponent.last_pvp_at, cfg.pvp_cooldown_seconds
    )
    if challenger_remaining > 0:
        blocks.append(_format_cooldown_block(challenger.dao_name, challenger_remaining))
    if opponent_remaining > 0:
        blocks.append(_format_cooldown_block(opponent.dao_name, opponent_remaining))
    if blocks:
        return "Duel is cooling down. " + " · ".join(blocks) + "."
    return None


def create_duel_challenge(
    session: Session,
    guild_id: str,
    challenger: Player,
    opponent: Player,
    cfg: Config,
    now: datetime | None = None,
) -> tuple[PendingDuel | None, str | None]:
    now = to_utc(now or utcnow())
    cooldown_err = validate_duel_participants(session, challenger, opponent, cfg, now)
    if cooldown_err:
        return None, cooldown_err

    if find_active_pending_for_player(session, guild_id, challenger.discord_id, now):
        return None, "You already have a pending duel challenge."

    if find_active_pending_for_player(session, guild_id, opponent.discord_id, now):
        return None, f"**{opponent.dao_name}** already has a pending duel challenge."

    if find_active_pending_between(session, guild_id, challenger.discord_id, opponent.discord_id, now):
        return None, "A duel challenge is already pending between you two."

    challenge = PendingDuel(
        guild_id=guild_id,
        challenger_discord_id=challenger.discord_id,
        opponent_discord_id=opponent.discord_id,
        challenger_dao_name=challenger.dao_name,
        opponent_dao_name=opponent.dao_name,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(seconds=DUEL_CHALLENGE_TIMEOUT_SECONDS),
    )
    session.add(challenge)
    session.flush()
    return challenge, None


def attach_challenge_message(
    session: Session,
    challenge: PendingDuel,
    channel_id: str,
    message_id: str,
) -> None:
    challenge.channel_id = channel_id
    challenge.message_id = message_id
    session.add(challenge)


def get_valid_pending_challenge(
    session: Session,
    challenge_id: int,
    now: datetime | None = None,
) -> tuple[PendingDuel | None, str | None]:
    now = to_utc(now or utcnow())
    challenge = session.get(PendingDuel, challenge_id)
    if challenge is None:
        return None, "That duel challenge no longer exists."
    if challenge.status != "pending":
        return None, "That duel challenge is no longer active."
    if to_utc(challenge.expires_at) <= now:
        challenge.status = "expired"
        challenge.resolved_at = now
        session.add(challenge)
        return None, "That duel challenge has expired."
    return challenge, None


def expire_stale_challenges(session: Session, now: datetime | None = None) -> int:
    now = to_utc(now or utcnow())
    stmt = select(PendingDuel).where(
        PendingDuel.status == "pending",
        PendingDuel.expires_at <= now,
    )
    expired = session.execute(stmt).scalars().all()
    for challenge in expired:
        challenge.status = "expired"
        challenge.resolved_at = now
        session.add(challenge)
    return len(expired)


def execute_duel(
    session: Session,
    challenger: Player,
    opponent: Player,
    cfg: Config,
    rng: random.Random,
    now: datetime | None = None,
) -> ExecutedDuel:
    now = to_utc(now or utcnow())
    apply_stamina_regen(challenger, now)
    apply_stamina_regen(opponent, now)
    offline_qi = apply_offline_progress(challenger, now, cfg.offline_cap_minutes)
    if offline_qi > 0:
        challenger.qi += offline_qi
        challenger.last_active_at = now

    mod_a = get_character_modifiers(session, challenger)
    mod_b = get_character_modifiers(session, opponent)
    res = duel(challenger, opponent, cfg, rng=rng, challenger_mod=mod_a, opponent_mod=mod_b)

    challenger.last_pvp_at = now
    opponent.last_pvp_at = now
    consume_haste_for_activity(session, challenger.id, "duel")
    consume_haste_for_activity(session, opponent.id, "duel")
    challenger.pvp_wins = challenger.pvp_wins + (1 if res.success else 0)
    challenger.pvp_losses = challenger.pvp_losses + (0 if res.success else 1)
    opponent.pvp_wins = opponent.pvp_wins + (1 if not res.success else 0)
    opponent.pvp_losses = opponent.pvp_losses + (0 if not res.success else 1)
    challenger.last_active_at = now
    opponent.last_active_at = now

    session.add(challenger)
    session.add(opponent)
    return ExecutedDuel(result=res, challenger=challenger, opponent=opponent)


def accept_duel_challenge(
    session: Session,
    challenge_id: int,
    acceptor_discord_id: str,
    challenger: Player,
    opponent: Player,
    cfg: Config,
    rng: random.Random,
    now: datetime | None = None,
) -> tuple[ExecutedDuel | None, str | None]:
    now = to_utc(now or utcnow())
    challenge, err = get_valid_pending_challenge(session, challenge_id, now)
    if err:
        return None, err
    if acceptor_discord_id != challenge.opponent_discord_id:
        return None, "Only the challenged daoist may accept this duel."
    if challenger.discord_id != challenge.challenger_discord_id:
        return None, "This challenge does not match the challenger."
    if opponent.discord_id != challenge.opponent_discord_id:
        return None, "This challenge does not match the opponent."

    cooldown_err = validate_duel_participants(session, challenger, opponent, cfg, now)
    if cooldown_err:
        return None, cooldown_err

    executed = execute_duel(session, challenger, opponent, cfg, rng, now)
    challenge.status = "completed"
    challenge.resolved_at = now
    session.add(challenge)
    return executed, None


def decline_duel_challenge(
    session: Session,
    challenge_id: int,
    decliner_discord_id: str,
    now: datetime | None = None,
) -> tuple[PendingDuel | None, str | None]:
    now = to_utc(now or utcnow())
    challenge, err = get_valid_pending_challenge(session, challenge_id, now)
    if err:
        return None, err
    if decliner_discord_id != challenge.opponent_discord_id:
        return None, "Only the challenged daoist may decline this duel."

    challenge.status = "declined"
    challenge.resolved_at = now
    session.add(challenge)
    return challenge, None


def expire_duel_challenge(
    session: Session,
    challenge_id: int,
    now: datetime | None = None,
) -> PendingDuel | None:
    now = to_utc(now or utcnow())
    challenge = session.get(PendingDuel, challenge_id)
    if challenge is None or challenge.status != "pending":
        return None
    challenge.status = "expired"
    challenge.resolved_at = now
    session.add(challenge)
    return challenge
