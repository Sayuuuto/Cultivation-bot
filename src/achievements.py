from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Player, PlayerAchievement

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "achievements.json"


@dataclass(frozen=True)
class AchievementDef:
    id: str
    name: str
    description: str
    category: str
    hint: str
    name_short: str | None = None

    @property
    def display_short(self) -> str:
        return self.name_short or self.name[:12]


@lru_cache(maxsize=1)
def load_achievements() -> dict[str, AchievementDef]:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    result: dict[str, AchievementDef] = {}
    for entry in raw.get("achievements", []):
        aid = str(entry["id"])
        result[aid] = AchievementDef(
            id=aid,
            name=str(entry["name"]),
            description=str(entry["description"]),
            category=str(entry.get("category", "general")),
            hint=str(entry.get("hint", "???")),
            name_short=entry.get("name_short"),
        )
    return result


def get_player_achievements(session: Session, player_id: int) -> dict[str, datetime]:
    stmt = select(PlayerAchievement).where(PlayerAchievement.player_id == player_id)
    rows = session.execute(stmt).scalars().all()
    return {row.achievement_id: row.unlocked_at for row in rows}


def unlock_achievement(
    session: Session,
    player: Player,
    achievement_id: str,
    *,
    now: datetime | None = None,
) -> AchievementDef | None:
    catalog = load_achievements()
    if achievement_id not in catalog:
        return None
    existing = session.execute(
        select(PlayerAchievement).where(
            PlayerAchievement.player_id == player.id,
            PlayerAchievement.achievement_id == achievement_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None
    ts = now or datetime.now(timezone.utc)
    session.add(
        PlayerAchievement(
            player_id=player.id,
            achievement_id=achievement_id,
            unlocked_at=ts,
        )
    )
    session.flush()
    return catalog[achievement_id]


def _meets_criteria(
    achievement: AchievementDef,
    event_type: str,
    event_data: dict[str, Any],
    player: Player,
    session: Session,
) -> bool:
    aid = achievement.id
    if aid == "first_blood" and event_type == "pvp_win":
        return int(event_data.get("wins", player.pvp_wins)) >= 1
    if aid == "duel_veteran" and event_type == "pvp_win":
        return player.pvp_wins >= 10
    if aid == "pack_hunter" and event_type == "hunt_complete":
        return int(getattr(player, "hunts_won", 0) or 0) >= 100
    if aid == "qi_acolyte" and event_type == "breakthrough":
        return player.realm_index >= 1 or int(event_data.get("new_realm", 0)) >= 1
    if aid == "realm_walker" and event_type == "breakthrough":
        return int(event_data.get("new_realm", player.realm_index)) >= 2
    if aid == "scholar" and event_type == "technique_learned":
        from .combat.loadout import get_learned_technique_ids

        return len(get_learned_technique_ids(session, player.id)) >= 10
    if aid == "trailblazer" and event_type == "adventure_complete":
        return player.adventures_completed >= 25
    if aid == "expeditionary" and event_type == "explore_complete":
        return True
    return False


def check_achievements(
    session: Session,
    player: Player,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> list[AchievementDef]:
    data = event_data or {}
    unlocked: list[AchievementDef] = []
    for achievement in load_achievements().values():
        if not _meets_criteria(achievement, event_type, data, player, session):
            continue
        newly = unlock_achievement(session, player, achievement.id, now=now)
        if newly is not None:
            unlocked.append(newly)
    return unlocked


def format_achievement_unlock_message(achievements: list[AchievementDef]) -> str | None:
    if not achievements:
        return None
    lines = [f"🏆 **{a.name}** — {a.description}" for a in achievements]
    return "\n".join(lines)


def build_profile_achievement_badges(session: Session, player_id: int, *, public: bool = False) -> list[str]:
    catalog = load_achievements()
    unlocked = get_player_achievements(session, player_id)
    badges: list[str] = []
    for aid, ach in catalog.items():
        if aid in unlocked:
            badges.append(ach.display_short)
        elif not public:
            badges.append("???")
    if public:
        return badges[:8]
    return badges[:8]


def build_achievements_embed(session: Session, player: Player) -> "discord.Embed":
    import discord

    catalog = load_achievements()
    unlocked = get_player_achievements(session, player.id)
    lines: list[str] = []
    for aid, ach in catalog.items():
        if aid in unlocked:
            ts = unlocked[aid]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            date_str = ts.strftime("%Y-%m-%d")
            lines.append(f"🏆 **{ach.name}** — {ach.description} _({date_str})_")
        else:
            lines.append(f"🔒 **???** — _{ach.hint}_")
    embed = discord.Embed(
        title=f"{player.dao_name} — Achievements",
        description="\n".join(lines) if lines else "No achievements defined yet.",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{len(unlocked)}/{len(catalog)} unlocked")
    return embed
