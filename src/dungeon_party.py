from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cooperative_dungeons import get_cooperative_dungeon
from .game import to_utc, utcnow
from .models import ActiveDungeonParty, Player

MAX_PARTY_SIZE = 4  # leader + up to 3 allies
MIN_PARTY_SIZE = 1
MAX_INVITES = 3
PARTY_LOBBY_TIMEOUT_SECONDS = 300
PARTY_INVITE_TIMEOUT_SECONDS = 300
DUNGEON_COMBAT_STALE_SECONDS = 7200


@dataclass(frozen=True)
class PartyMember:
    discord_id: str
    dao_name: str
    player_id: int

    def to_dict(self) -> dict:
        return {
            "discord_id": self.discord_id,
            "dao_name": self.dao_name,
            "player_id": self.player_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PartyMember:
        return cls(
            discord_id=str(data["discord_id"]),
            dao_name=str(data.get("dao_name", "Daoist")),
            player_id=int(data["player_id"]),
        )


@dataclass(frozen=True)
class PartyInvite:
    discord_id: str
    dao_name: str
    expires_at: str

    def to_dict(self) -> dict:
        return {
            "discord_id": self.discord_id,
            "dao_name": self.dao_name,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PartyInvite:
        return cls(
            discord_id=str(data["discord_id"]),
            dao_name=str(data.get("dao_name", "Daoist")),
            expires_at=str(data["expires_at"]),
        )


def load_members(party: ActiveDungeonParty) -> list[PartyMember]:
    try:
        raw = json.loads(party.members_json or "[]")
    except json.JSONDecodeError:
        return []
    return [PartyMember.from_dict(entry) for entry in raw if isinstance(entry, dict)]


def save_members(party: ActiveDungeonParty, members: list[PartyMember]) -> None:
    party.members_json = json.dumps([m.to_dict() for m in members])


def load_invites(party: ActiveDungeonParty, now: datetime | None = None) -> list[PartyInvite]:
    now = to_utc(now or utcnow())
    invites: list[PartyInvite] = []
    try:
        raw = json.loads(party.invites_json or "[]")
    except json.JSONDecodeError:
        return []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        inv = PartyInvite.from_dict(entry)
        exp = datetime.fromisoformat(inv.expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=now.tzinfo)
        if exp > now:
            invites.append(inv)
    return invites


def save_invites(party: ActiveDungeonParty, invites: list[PartyInvite]) -> None:
    party.invites_json = json.dumps([i.to_dict() for i in invites])


def member_discord_ids(party: ActiveDungeonParty) -> set[str]:
    return {m.discord_id for m in load_members(party)}


def invited_discord_ids(party: ActiveDungeonParty) -> set[str]:
    return {i.discord_id for i in load_invites(party)}


def cancel_party(party: ActiveDungeonParty, *, now: datetime | None = None) -> None:
    party.status = "cancelled"
    party.updated_at = to_utc(now or utcnow())


def party_is_stale(party: ActiveDungeonParty, now: datetime | None = None) -> bool:
    """True when a waiting or in-progress expedition should no longer block the player."""
    now = to_utc(now or utcnow())
    if party.status in ("completed", "cancelled"):
        return False
    if to_utc(party.expires_at) <= now:
        return True
    if party.status == "lobby":
        lobby_deadline = to_utc(party.created_at) + timedelta(seconds=PARTY_LOBBY_TIMEOUT_SECONDS)
        return lobby_deadline <= now
    if party.status == "in_combat":
        combat_deadline = to_utc(party.updated_at) + timedelta(seconds=DUNGEON_COMBAT_STALE_SECONDS)
        return combat_deadline <= now
    # Unknown status from older builds — treat like an abandoned lobby.
    fallback = to_utc(party.created_at) + timedelta(seconds=PARTY_LOBBY_TIMEOUT_SECONDS)
    return fallback <= now


def expire_stale_dungeon_parties(session: Session, now: datetime | None = None) -> int:
    """Mark abandoned lobby invites and stuck runs as cancelled."""
    now = to_utc(now or utcnow())
    stmt = select(ActiveDungeonParty).where(
        ActiveDungeonParty.status.not_in(("completed", "cancelled")),
    )
    expired = 0
    for party in session.execute(stmt).scalars().all():
        if party_is_stale(party, now):
            cancel_party(party, now=now)
            session.add(party)
            expired += 1
    return expired


def find_party_for_player(
    session: Session,
    guild_id: str,
    discord_id: str,
    *,
    statuses: tuple[str, ...] = ("lobby", "in_combat"),
) -> ActiveDungeonParty | None:
    expire_stale_dungeon_parties(session)
    now = utcnow()
    stmt = (
        select(ActiveDungeonParty)
        .where(
            ActiveDungeonParty.guild_id == guild_id,
            ActiveDungeonParty.status.in_(statuses),
            ActiveDungeonParty.expires_at > now,
        )
        .order_by(ActiveDungeonParty.created_at.desc())
    )
    for party in session.execute(stmt).scalars().all():
        if discord_id in member_discord_ids(party) or discord_id in invited_discord_ids(party):
            return party
    return None


def create_party_with_invites(
    session: Session,
    *,
    guild_id: str,
    leader: Player,
    dungeon_id: str,
    invitees: list[Player],
) -> tuple[ActiveDungeonParty | None, str]:
    dungeon = get_cooperative_dungeon(dungeon_id)
    if dungeon is None:
        return None, "That dungeon does not exist."
    if len(invitees) > MAX_INVITES:
        return None, f"You may invite at most **{MAX_INVITES}** other daoists."

    expire_stale_dungeon_parties(session)
    existing = find_party_for_player(session, guild_id, leader.discord_id)
    if existing is not None:
        if party_is_stale(existing):
            cancel_party(existing)
            session.add(existing)
            session.flush()
        else:
            return None, expedition_busy_message(existing, leader.discord_id)

    seen: set[str] = set()
    for invitee in invitees:
        if invitee.discord_id == leader.discord_id:
            return None, "You cannot invite yourself."
        if invitee.discord_id in seen:
            return None, "Each ally may only be listed once."
        seen.add(invitee.discord_id)
        if find_party_for_player(session, guild_id, invitee.discord_id):
            return None, f"**{invitee.dao_name}** is already in another dungeon expedition."

    if 1 + len(invitees) > MAX_PARTY_SIZE:
        return None, f"A party holds at most **{MAX_PARTY_SIZE}** daoists."

    now = utcnow()
    party = ActiveDungeonParty(
        guild_id=guild_id,
        leader_discord_id=leader.discord_id,
        dungeon_id=dungeon_id,
        status="lobby",
        created_at=now,
        expires_at=now + timedelta(seconds=PARTY_LOBBY_TIMEOUT_SECONDS),
        updated_at=now,
    )
    save_members(
        party,
        [
            PartyMember(
                discord_id=leader.discord_id,
                dao_name=leader.dao_name or "Daoist",
                player_id=leader.id,
            )
        ],
    )
    invite_rows = [
        PartyInvite(
            discord_id=inv.discord_id,
            dao_name=inv.dao_name or "Daoist",
            expires_at=(now + timedelta(seconds=PARTY_INVITE_TIMEOUT_SECONDS)).isoformat(),
        )
        for inv in invitees
    ]
    save_invites(party, invite_rows)
    session.add(party)
    session.flush()
    return party, ""


def accept_invite(
    session: Session,
    party: ActiveDungeonParty,
    player: Player,
) -> tuple[bool, str]:
    """Returns (accepted, message). accepted=False if already member or no invite."""
    if party.status != "lobby":
        return False, "This expedition is no longer waiting for allies."
    now = utcnow()
    invites = load_invites(party, now)
    if not any(i.discord_id == player.discord_id for i in invites):
        return False, "You are not invited to this expedition."

    members = load_members(party)
    if player.discord_id in {m.discord_id for m in members}:
        return False, "You already accepted."

    members.append(
        PartyMember(
            discord_id=player.discord_id,
            dao_name=player.dao_name or "Daoist",
            player_id=player.id,
        )
    )
    save_members(party, members)
    save_invites(party, [i for i in invites if i.discord_id != player.discord_id])
    party.updated_at = now
    return True, f"**{player.dao_name}** accepts the expedition."


def party_ready_to_launch(party: ActiveDungeonParty) -> bool:
    if party.status != "lobby":
        return False
    if load_invites(party):
        return False
    return len(load_members(party)) >= MIN_PARTY_SIZE


def expedition_busy_message(party: ActiveDungeonParty, discord_id: str) -> str:
    """Player-facing hint when /dungeon is blocked by an existing expedition."""
    dungeon = get_cooperative_dungeon(party.dungeon_id)
    name = dungeon.name if dungeon else "A dungeon expedition"
    if party.status == "in_combat":
        channel_hint = (
            f" Continue in <#{party.channel_id}>."
            if party.channel_id
            else " Look for a **dungeon-** channel in this server."
        )
        return (
            f"**{name}** is already underway.{channel_hint} "
            "Take your turn there. If the fight went quiet, wait a few minutes and try **`/dungeon`** again."
        )
    if discord_id in invited_discord_ids(party):
        return (
            f"You are invited to **{name}**. Scroll up for the expedition embed and press **Accept**. "
            "Decline by ignoring it — the invite dissolves in about **5 minutes** if the party does not form."
        )
    if party.leader_discord_id == discord_id:
        if load_invites(party):
            return (
                f"You are leading **{name}** and allies still need to **Accept**. "
                "Find that expedition message in this channel. "
                "If everyone has left, wait about **5 minutes**, then run **`/dungeon`** again."
            )
        return (
            f"**{name}** is forming under your banner — it should open shortly. "
            "If nothing happens, wait about **5 minutes**, then run **`/dungeon`** again."
        )
    return (
        f"You are marked on **{name}**. Find the party's expedition message or dungeon channel. "
        "After about **5 minutes** without progress, you may run **`/dungeon`** again."
    )


def can_start_party(party: ActiveDungeonParty) -> tuple[bool, str]:
    if party.status != "lobby":
        return False, "This expedition has already begun or ended."
    if load_invites(party):
        return False, "Waiting for all invited daoists to accept."
    members = load_members(party)
    if len(members) < MIN_PARTY_SIZE:
        return False, f"At least **{MIN_PARTY_SIZE}** daoists must stand together."
    if get_cooperative_dungeon(party.dungeon_id) is None:
        return False, "Dungeon configuration is missing."
    return True, ""


def format_invite_embed_description(party: ActiveDungeonParty) -> str:
    dungeon = get_cooperative_dungeon(party.dungeon_id)
    name = dungeon.name if dungeon else party.dungeon_id
    members = load_members(party)
    invites = load_invites(party)
    lines = [
        f"**{name}** — party expedition",
        f"**Leader:** <@{party.leader_discord_id}>",
        "",
        "**In party:**",
    ]
    for m in members:
        mark = " ✓" if m.discord_id != party.leader_discord_id or not invites else ""
        lines.append(f"• **{m.dao_name}**{mark}")
    if invites:
        lines.append("")
        lines.append("**Awaiting accept:**")
        for inv in invites:
            lines.append(f"• <@{inv.discord_id}> (**{inv.dao_name}**)")
        lines.append("")
        lines.append("_Invited daoists — press **Accept** below. The run begins when everyone is in._")
    else:
        lines.append("")
        lines.append("_All allies ready — the expedition is opening…_")
    return "\n".join(lines)


def apply_dungeon_rewards(
    session: Session,
    members: list[PartyMember],
    drops: dict[str, int],
) -> None:
    from .inventory import add_item

    for member in members:
        for item_id, qty in drops.items():
            add_item(session, member.player_id, item_id, qty)


def roll_party_rewards(
    dungeon_id: str,
    rng: random.Random,
    *,
    session: Session | None = None,
    members: list | None = None,
    pending_loot: dict[str, int] | None = None,
) -> dict[str, int]:
    from .character import get_character_modifiers
    from .combat_stats import compute_combat_stats
    from .loot import LootDropEntry, effective_drop_chance, merge_loot_dicts, roll_creature_loot
    from .models import Player

    dungeon = get_cooperative_dungeon(dungeon_id)
    if dungeon is None:
        return {}
    drops: dict[str, int] = dict(pending_loot or {})
    luck = 5.0
    drop_luck = 0.0
    realm_index = dungeon.realm_index
    if session is not None and members:
        luck_vals: list[float] = []
        drop_luck_vals: list[float] = []
        for member in members:
            player = session.get(Player, member.player_id)
            if player is None:
                continue
            mod = get_character_modifiers(session, player)
            stats = compute_combat_stats(player, session, mod)
            luck_vals.append(stats.luck)
            drop_luck_vals.append(mod.drop_luck)
            realm_index = max(realm_index, player.realm_index)
        if luck_vals:
            luck = sum(luck_vals) / len(luck_vals)
        if drop_luck_vals:
            drop_luck = sum(drop_luck_vals) / len(drop_luck_vals)

    guaranteed_table = tuple(
        LootDropEntry(item_id=e.item_id, rarity=e.rarity, min_qty=e.min_qty, max_qty=e.max_qty)
        for e in dungeon.guaranteed_drops
    )
    drops = merge_loot_dicts(drops, roll_creature_loot(
        guaranteed_table,
        rng,
        combat_tier="boss",
        luck=luck,
        drop_luck=drop_luck,
        player_realm_index=realm_index,
        area_min_realm=dungeon.realm_index,
        qty_mult=1.25,
        skip_manuals=False,
    ))
    for entry in dungeon.bonus_drops:
        chance = entry.chance if entry.chance is not None else effective_drop_chance(
            entry.rarity,
            combat_tier="boss",
            luck=luck,
            drop_luck=drop_luck,
            player_realm_index=realm_index,
            area_min_realm=dungeon.realm_index,
        )
        if rng.random() <= chance:
            qty = rng.randint(entry.min_qty, entry.max_qty)
            drops[entry.item_id] = drops.get(entry.item_id, 0) + qty
    return drops
