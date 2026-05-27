from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Clan(Base):
    __tablename__ = "clans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_by_discord_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    clan_qi_contributed: Mapped[int] = mapped_column(Integer, default=0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    invite_only: Mapped[bool] = mapped_column(default=False)

    members: Mapped[list["Player"]] = relationship(back_populates="clan")

    __table_args__ = (
        UniqueConstraint("guild_id", "name", name="uq_clan_guild_name"),
    )


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String(32), index=True)

    discord_id: Mapped[str] = mapped_column(String(32))
    discord_username: Mapped[str] = mapped_column(String(64), default="")

    # Identity / flavor
    dao_name: Mapped[str] = mapped_column(String(64), default="")
    origin: Mapped[str] = mapped_column(String(64), default="")
    spirit_root: Mapped[str] = mapped_column(String(64), default="")
    moral_path: Mapped[str] = mapped_column(String(16), default="neutral")  # legacy; karma is authoritative
    karma: Mapped[int] = mapped_column(Integer, default=0)
    reputation: Mapped[int] = mapped_column(Integer, default=0)
    novice_trial_step: Mapped[int] = mapped_column(Integer, default=0)
    novice_cultivates: Mapped[int] = mapped_column(Integer, default=0)
    adventures_completed: Mapped[int] = mapped_column(Integer, default=0)
    # Aptitude rerolls (MVP: 1 free reroll on first creation, then gated by time + stones).
    spirit_root_reroll_free_used: Mapped[bool] = mapped_column(default=False)
    spirit_root_last_reroll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Progression
    realm_index: Mapped[int] = mapped_column(Integer, default=0)  # 0..9
    substage: Mapped[int] = mapped_column(Integer, default=0)  # 0..2
    qi: Mapped[int] = mapped_column(Integer, default=0)
    spirit_stones: Mapped[int] = mapped_column(Integer, default=0)

    stamina: Mapped[int] = mapped_column(Integer, default=100)
    stamina_last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Times
    last_cultivate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_daily_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_daily_streak_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pvp_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_adventure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dungeon_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_gather_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_hunt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    daily_streak: Mapped[int] = mapped_column(Integer, default=0)

    # PVP
    pvp_wins: Mapped[int] = mapped_column(Integer, default=0)
    pvp_losses: Mapped[int] = mapped_column(Integer, default=0)

    remind_dms_blocked: Mapped[bool] = mapped_column(default=False)

    # Private Discord abode channel for this cultivator (server-specific).
    abode_channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Player clan (Discord-server guild group).
    clan_id: Mapped[int | None] = mapped_column(ForeignKey("clans.id"), nullable=True)
    clan_role: Mapped[str] = mapped_column(String(16), default="member")  # founder/member/officer later
    clan_contribution_qi_total: Mapped[int] = mapped_column(Integer, default=0)

    # In-world martial sect (fixed factions; see config/sects.json).
    game_sect_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sect_merit: Mapped[int] = mapped_column(Integer, default=0)
    last_sect_task_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sect_daily_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sect_daily_task_progress: Mapped[int] = mapped_column(Integer, default=0)
    sect_daily_task_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sect_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sect_leave_cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    clan: Mapped[Clan | None] = relationship(back_populates="members")
    inventory_items: Mapped[list["InventoryItem"]] = relationship(back_populates="player")
    equipment: Mapped[list["PlayerEquipment"]] = relationship(back_populates="player")
    effects: Mapped[list["PlayerEffect"]] = relationship(back_populates="player")

    __table_args__ = (
        UniqueConstraint("guild_id", "discord_id", name="uq_player_guild_discord"),
    )


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer, default=0)

    player: Mapped[Player] = relationship(back_populates="inventory_items")

    __table_args__ = (
        UniqueConstraint("player_id", "item_id", name="uq_inventory_player_item"),
    )


EQUIPMENT_SLOTS = ("weapon", "armor", "accessory", "talisman")


class PlayerEquipment(Base):
    __tablename__ = "player_equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    slot: Mapped[str] = mapped_column(String(16))
    item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stat_power: Mapped[int] = mapped_column(Integer, default=0)
    stat_defense: Mapped[int] = mapped_column(Integer, default=0)
    stat_fortune: Mapped[int] = mapped_column(Integer, default=0)
    stat_insight: Mapped[int] = mapped_column(Integer, default=0)
    affix_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    technique_tag: Mapped[str | None] = mapped_column(String(16), nullable=True)

    player: Mapped[Player] = relationship(back_populates="equipment")

    __table_args__ = (
        UniqueConstraint("player_id", "slot", name="uq_equipment_player_slot"),
    )


class ActiveAdventure(Base):
    __tablename__ = "active_adventures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, unique=True)
    area_id: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(16), default="balanced")
    segment: Mapped[int] = mapped_column(Integer, default=1)
    encounter_id: Mapped[str] = mapped_column(String(64), default="")
    state_json: Mapped[str] = mapped_column(String(4096), default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerEffect(Base):
    __tablename__ = "player_effects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    effect_id: Mapped[str] = mapped_column(String(32))
    charges: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_int: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    player: Mapped[Player] = relationship(back_populates="effects")


class AdventureRun(Base):
    __tablename__ = "adventure_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    area_id: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(16), default="balanced")
    outcome: Mapped[str] = mapped_column(String(16), default="partial")
    rewards_json: Mapped[str] = mapped_column(String(2048), default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DungeonRun(Base):
    __tablename__ = "dungeon_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    dungeon_id: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="solo")
    outcome: Mapped[str] = mapped_column(String(16), default="fail")
    rewards_json: Mapped[str] = mapped_column(String(2048), default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PendingDuel(Base):
    __tablename__ = "pending_duels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String(32), index=True)
    challenger_discord_id: Mapped[str] = mapped_column(String(32), index=True)
    opponent_discord_id: Mapped[str] = mapped_column(String(32), index=True)
    challenger_dao_name: Mapped[str] = mapped_column(String(64), default="")
    opponent_dao_name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/declined/expired/completed
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActiveCombat(Base):
    __tablename__ = "active_combats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True, unique=True)
    context: Mapped[str] = mapped_column(String(16), default="hunt")
    context_key: Mapped[str] = mapped_column(String(64), default="")
    state_json: Mapped[str] = mapped_column(String(8192), default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlayerTechnique(Base):
    __tablename__ = "player_techniques"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    technique_id: Mapped[str] = mapped_column(String(64))
    learned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("player_id", "technique_id", name="uq_player_technique"),
    )


class TechniqueLoadout(Base):
    __tablename__ = "technique_loadout"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    slot: Mapped[str] = mapped_column(String(16))
    technique_id: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("player_id", "slot", name="uq_technique_loadout_slot"),
    )


class PlayerReminder(Base):
    __tablename__ = "player_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    activity: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(default=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("player_id", "activity", name="uq_reminder_player_activity"),
    )


class ClanInvitation(Base):
    __tablename__ = "clan_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clan_id: Mapped[int] = mapped_column(ForeignKey("clans.id"), index=True)
    guild_id: Mapped[str] = mapped_column(String(32), index=True)
    invitee_discord_id: Mapped[str] = mapped_column(String(32), index=True)
    invited_by_discord_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("guild_id", "invitee_discord_id", "clan_id", name="uq_clan_invite"),
    )


class PlayerSectInvitation(Base):
    __tablename__ = "player_sect_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    sect_id: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(64), default="adventure")

    __table_args__ = (
        UniqueConstraint("player_id", "sect_id", name="uq_player_sect_invitation"),
    )

