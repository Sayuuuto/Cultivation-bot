from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Clan, ClanInvitation, Player


def get_clan_by_name(session: Session, guild_id: str, name: str) -> Clan | None:
    stmt = select(Clan).where(Clan.guild_id == guild_id, Clan.name == name)
    return session.execute(stmt).scalar_one_or_none()


def get_clan_top_contributors(
    session: Session,
    guild_id: str,
    clan_id: int,
    *,
    limit: int = 5,
) -> list[Player]:
    stmt = (
        select(Player)
        .where(Player.guild_id == guild_id, Player.clan_id == clan_id)
        .order_by(Player.clan_contribution_qi_total.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def has_clan_invitation(session: Session, guild_id: str, invitee_discord_id: str, clan_id: int) -> bool:
    stmt = select(ClanInvitation).where(
        ClanInvitation.guild_id == guild_id,
        ClanInvitation.invitee_discord_id == invitee_discord_id,
        ClanInvitation.clan_id == clan_id,
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def list_clan_invitations_for_player(
    session: Session,
    guild_id: str,
    invitee_discord_id: str,
) -> list[tuple[ClanInvitation, Clan]]:
    stmt = select(ClanInvitation).where(
        ClanInvitation.guild_id == guild_id,
        ClanInvitation.invitee_discord_id == invitee_discord_id,
    )
    invites = list(session.execute(stmt).scalars().all())
    rows: list[tuple[ClanInvitation, Clan]] = []
    for invite in invites:
        clan = session.get(Clan, invite.clan_id)
        if clan is not None:
            rows.append((invite, clan))
    return rows


def create_clan_invitation(
    session: Session,
    *,
    clan: Clan,
    invitee_discord_id: str,
    invited_by_discord_id: str,
) -> tuple[bool, str]:
    if has_clan_invitation(session, clan.guild_id, invitee_discord_id, clan.id):
        return False, "That cultivator already has a pending invite to this clan."

    existing_member = session.execute(
        select(Player).where(
            Player.guild_id == clan.guild_id,
            Player.discord_id == invitee_discord_id,
            Player.clan_id == clan.id,
        )
    ).scalar_one_or_none()
    if existing_member is not None:
        return False, "They are already a member of this clan."

    session.add(
        ClanInvitation(
            clan_id=clan.id,
            guild_id=clan.guild_id,
            invitee_discord_id=invitee_discord_id,
            invited_by_discord_id=invited_by_discord_id,
        )
    )
    return True, f"Invitation sent to join **{clan.name}**."


def consume_clan_invitation(
    session: Session,
    guild_id: str,
    invitee_discord_id: str,
    clan_id: int,
) -> None:
    stmt = select(ClanInvitation).where(
        ClanInvitation.guild_id == guild_id,
        ClanInvitation.invitee_discord_id == invitee_discord_id,
        ClanInvitation.clan_id == clan_id,
    )
    for row in session.execute(stmt).scalars().all():
        session.delete(row)


def can_join_clan(session: Session, player: Player, clan: Clan) -> tuple[bool, str]:
    if player.clan_id is not None:
        return False, "You are already in a clan. Use `/clan-leave` first."

    if clan.invite_only and not has_clan_invitation(session, clan.guild_id, player.discord_id, clan.id):
        return False, (
            f"**{clan.name}** accepts members by invitation only. "
            "Ask the founder for `/clan-invite`."
        )
    return True, ""


def set_clan_invite_only(session: Session, clan: Clan, invite_only: bool) -> str:
    clan.invite_only = invite_only
    session.add(clan)
    if invite_only:
        return f"**{clan.name}** now requires an invitation to join."
    return f"**{clan.name}** is open — anyone may `/clan-join`."
