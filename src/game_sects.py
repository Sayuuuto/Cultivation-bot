from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .karma import karma_tier
from .models import Player, PlayerSectInvitation, utcnow

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SECTS_PATH = CONFIG_DIR / "sects.json"
SECT_TASKS_PATH = CONFIG_DIR / "sect_tasks.json"
SECT_SHOPS_PATH = CONFIG_DIR / "sect_shops.json"

SECT_LEAVE_MERIT_PENALTY = 0.5
SECT_REJOIN_COOLDOWN = timedelta(hours=24)

REALM_BANDS: tuple[tuple[str, int, int], ...] = (
    ("mortal", 0, 0),
    ("earth", 1, 2),
    ("heaven", 3, 9),
)


@dataclass(frozen=True)
class GameSectDef:
    sect_id: str
    name: str
    tagline: str
    join_type: str
    karma_requirement: tuple[str, ...]
    min_realm_index: int
    theme: str
    description: str
    shop_id: str
    task_pool_id: str


_sects: dict[str, GameSectDef] | None = None
_task_pools: dict | None = None
_shops: dict | None = None


def _parse_sect(sect_id: str, raw: dict) -> GameSectDef:
    return GameSectDef(
        sect_id=sect_id,
        name=str(raw["name"]),
        tagline=str(raw.get("tagline", "")),
        join_type=str(raw.get("join_type", "open")),
        karma_requirement=tuple(raw.get("karma_requirement", ["neutral"])),
        min_realm_index=int(raw.get("min_realm_index", 0)),
        theme=str(raw.get("theme", "")),
        description=str(raw.get("description", "")),
        shop_id=str(raw.get("shop_id", "")),
        task_pool_id=str(raw.get("task_pool_id", "")),
    )


def load_game_sects() -> dict[str, GameSectDef]:
    global _sects
    if _sects is not None:
        return _sects
    with SECTS_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    _sects = {sect_id: _parse_sect(sect_id, data) for sect_id, data in raw.items()}
    return _sects


def load_sect_task_pools() -> dict:
    global _task_pools
    if _task_pools is not None:
        return _task_pools
    with SECT_TASKS_PATH.open(encoding="utf-8") as f:
        _task_pools = json.load(f)
    return _task_pools


def load_sect_shops() -> dict:
    global _shops
    if _shops is not None:
        return _shops
    with SECT_SHOPS_PATH.open(encoding="utf-8") as f:
        _shops = json.load(f)
    return _shops


def invalidate_game_sect_cache() -> None:
    global _sects, _task_pools, _shops
    _sects = None
    _task_pools = None
    _shops = None


def get_sect_def(sect_id: str) -> GameSectDef | None:
    return load_game_sects().get(sect_id)


def realm_band_for(realm_index: int) -> str:
    for band, lo, hi in REALM_BANDS:
        if lo <= realm_index <= hi:
            return band
    return "mortal"


def has_sect_invitation(session: Session, player_id: int, sect_id: str) -> bool:
    stmt = select(PlayerSectInvitation).where(
        PlayerSectInvitation.player_id == player_id,
        PlayerSectInvitation.sect_id == sect_id,
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def grant_sect_invitation(
    session: Session,
    player_id: int,
    sect_id: str,
    *,
    source: str = "adventure",
) -> bool:
    if get_sect_def(sect_id) is None:
        return False
    if has_sect_invitation(session, player_id, sect_id):
        return False
    session.add(
        PlayerSectInvitation(
            player_id=player_id,
            sect_id=sect_id,
            source=source,
        )
    )
    return True


def consume_sect_invitation(session: Session, player_id: int, sect_id: str) -> None:
    stmt = select(PlayerSectInvitation).where(
        PlayerSectInvitation.player_id == player_id,
        PlayerSectInvitation.sect_id == sect_id,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        session.delete(row)


def join_eligibility(session: Session, player: Player, sect_id: str) -> tuple[bool, str]:
    sect = get_sect_def(sect_id)
    if sect is None:
        return False, "That martial sect is not known in this realm."

    if player.game_sect_id == sect_id:
        return False, f"You already walk the path of **{sect.name}**."

    if player.game_sect_id is not None:
        return False, "Leave your current sect before joining another (`/sect-leave`)."

    cooldown = player.sect_leave_cooldown_until
    if cooldown is not None:
        now = utcnow()
        cd = cooldown if cooldown.tzinfo else cooldown.replace(tzinfo=timezone.utc)
        if cd > now:
            remaining = int((cd - now).total_seconds() // 3600) + 1
            return False, f"You must wait **~{remaining}h** before rejoining a sect."

    tier = karma_tier(player.karma)
    if tier not in sect.karma_requirement:
        allowed = ", ".join(sect.karma_requirement)
        return False, (
            f"**{sect.name}** accepts **{allowed}** cultivators. "
            f"Your karma reads as **{tier}**."
        )

    if player.realm_index < sect.min_realm_index:
        return False, (
            f"**{sect.name}** requires a higher realm before accepting disciples."
        )

    if sect.join_type == "secret" and not has_sect_invitation(session, player.id, sect_id):
        return False, (
            f"**{sect.name}** does not accept petitioners. "
            "An invitation must find you on the path."
        )

    return True, ""


def join_game_sect(session: Session, player: Player, sect_id: str) -> tuple[bool, str]:
    ok, reason = join_eligibility(session, player, sect_id)
    if not ok:
        return False, reason

    sect = get_sect_def(sect_id)
    assert sect is not None

    if sect.join_type == "secret":
        consume_sect_invitation(session, player.id, sect_id)

    player.game_sect_id = sect_id
    player.sect_merit = 0
    player.sect_joined_at = utcnow()
    player.sect_leave_cooldown_until = None
    session.add(player)
    return True, f"You kneel before **{sect.name}** and are accepted as an outer disciple."


def leave_game_sect(session: Session, player: Player) -> tuple[bool, str, int]:
    if player.game_sect_id is None:
        return False, "You walk the path alone — you belong to no martial sect.", 0

    sect = get_sect_def(player.game_sect_id)
    name = sect.name if sect else player.game_sect_id
    merit_lost = int(player.sect_merit * SECT_LEAVE_MERIT_PENALTY)
    player.sect_merit = max(0, player.sect_merit - merit_lost)
    remaining = player.sect_merit

    player.game_sect_id = None
    player.sect_joined_at = None
    player.last_sect_task_date = None
    player.sect_leave_cooldown_until = utcnow() + SECT_REJOIN_COOLDOWN
    session.add(player)

    msg = (
        f"You sever ties with **{name}**. "
        f"**{merit_lost}** sect merit fades with your departure"
    )
    if remaining:
        msg += f" (**{remaining}** merit retained as honorary record)."
    else:
        msg += "."
    return True, msg, merit_lost


def format_sect_list_entry(
    session: Session,
    player: Player,
    sect: GameSectDef,
) -> str:
    if sect.join_type == "secret" and not has_sect_invitation(session, player.id, sect.sect_id):
        return f"**???** — A hidden order. None may petition entry."

    karma_req = " / ".join(sect.karma_requirement)
    realm_note = f"realm ≥ {sect.min_realm_index}" if sect.min_realm_index else "any realm"
    invite = " · invitation required" if sect.join_type == "secret" else ""
    return (
        f"**{sect.name}** (`{sect.sect_id}`) — {sect.tagline}\n"
        f"_{karma_req} · {realm_note}{invite}_"
    )


def format_player_sect_status(player: Player) -> str:
    if player.game_sect_id is None:
        return (
            "You are **sectless** — a wandering cultivator.\n"
            "Use **`/sect-list`** to see martial orders and **`/sect-join`** to petition entry."
        )

    sect = get_sect_def(player.game_sect_id)
    if sect is None:
        return f"Unknown sect `{player.game_sect_id}`."

    lines = [
        f"**{sect.name}** — {sect.tagline}",
        f"**Sect merit:** {player.sect_merit}",
        sect.description,
    ]
    if sect.join_type == "secret":
        lines.append("_You were invited — few ever see this path._")
    lines.append("_Daily sect tasks and the sect shop arrive in a future update._")
    return "\n".join(lines)
