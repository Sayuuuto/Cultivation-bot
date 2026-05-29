"""Delete a cultivator record and all dependent rows (fresh start via /start)."""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .models import (
    ActiveAdventure,
    ActiveCombat,
    ActiveDungeonParty,
    ActivePvpMatch,
    AdventureRun,
    Clan,
    ClanInvitation,
    DungeonRun,
    InventoryItem,
    PendingDuel,
    Player,
    PlayerEffect,
    PlayerEquipment,
    PlayerGearItem,
    PlayerNotification,
    PlayerReminder,
    PlayerSectInvitation,
    PlayerTechnique,
    PvpTelemetry,
    TechniqueLoadout,
)


def _detach_clan(session: Session, player: Player) -> None:
    if player.clan_id is None:
        return
    clan = session.get(Clan, player.clan_id)
    if clan is None:
        return
    if player.clan_role == "founder" and clan.member_count <= 1:
        session.execute(delete(ClanInvitation).where(ClanInvitation.clan_id == clan.id))
        session.delete(clan)
    else:
        clan.member_count = max(0, clan.member_count - 1)
        session.add(clan)


def _clear_guild_activity(session: Session, guild_id: str, discord_id: str, player_id: int) -> None:
    session.execute(
        delete(PendingDuel).where(
            PendingDuel.guild_id == guild_id,
            or_(
                PendingDuel.challenger_discord_id == discord_id,
                PendingDuel.opponent_discord_id == discord_id,
            ),
        )
    )
    session.execute(
        delete(PvpTelemetry).where(
            or_(
                PvpTelemetry.winner_player_id == player_id,
                PvpTelemetry.loser_player_id == player_id,
            )
        )
    )
    session.execute(
        delete(ActivePvpMatch).where(
            or_(
                ActivePvpMatch.challenger_player_id == player_id,
                ActivePvpMatch.opponent_player_id == player_id,
            )
        )
    )
    session.execute(
        delete(ClanInvitation).where(
            ClanInvitation.guild_id == guild_id,
            ClanInvitation.invitee_discord_id == discord_id,
        )
    )
    parties = session.execute(
        select(ActiveDungeonParty).where(ActiveDungeonParty.guild_id == guild_id)
    ).scalars()
    for party in parties:
        if party.leader_discord_id == discord_id or discord_id in (party.members_json or ""):
            session.delete(party)


def wipe_player_character(session: Session, player: Player) -> str | None:
    """
    Delete the player row and all dependent data.

    Returns abode_channel_id if one was stored (for optional Discord cleanup).
    """
    player_id = player.id
    guild_id = player.guild_id
    discord_id = player.discord_id
    abode_channel_id = player.abode_channel_id

    _detach_clan(session, player)
    _clear_guild_activity(session, guild_id, discord_id, player_id)

    for model in (
        ActiveCombat,
        ActiveAdventure,
        AdventureRun,
        DungeonRun,
        PlayerTechnique,
        TechniqueLoadout,
        InventoryItem,
        PlayerGearItem,
        PlayerEquipment,
        PlayerEffect,
        PlayerNotification,
        PlayerReminder,
        PlayerSectInvitation,
    ):
        session.execute(delete(model).where(model.player_id == player_id))  # type: ignore[attr-defined]

    session.delete(player)
    session.flush()
    return abode_channel_id
