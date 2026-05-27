from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .combat.catalog import get_technique_by_manual
from .combat.rarity import rarity_at_most
from .inventory import add_item, get_item_name
from .karma import KARMA_DEMONIC_THRESHOLD, KARMA_RIGHTEOUS_THRESHOLD, karma_tier
from .models import Player, PlayerSectInvitation, utcnow

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SECTS_PATH = CONFIG_DIR / "sects.json"
SECT_TASKS_PATH = CONFIG_DIR / "sect_tasks.json"
SECT_SHOPS_PATH = CONFIG_DIR / "sect_shops.json"

SECT_LEAVE_MERIT_PENALTY = 0.5
SECT_REJOIN_COOLDOWN = timedelta(hours=24)

SECT_MERIT_ACTIVITY: dict[str, int] = {
    "cultivate": 2,
    "gather": 1,
    "hunt": 5,
    "adventure": 8,
    "dungeon": 15,
}

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


@dataclass(frozen=True)
class SectTaskDef:
    task_id: str
    task_type: str
    count: int
    merit: int
    label: str
    item_id: str | None = None
    area_id: str | None = None
    beast_tag: str | None = None


@dataclass(frozen=True)
class SectShopEntry:
    item_id: str
    merit_cost: int
    min_realm_index: int = 0


@dataclass(frozen=True)
class SectShopDef:
    shop_id: str
    name: str
    entries: tuple[SectShopEntry, ...]


@dataclass(frozen=True)
class SectTaskStatus:
    task: SectTaskDef | None
    progress: int
    completed_today: bool
    assigned_today: bool


_sects: dict[str, GameSectDef] | None = None
_task_pools: dict[str, dict[str, list[SectTaskDef]]] | None = None
_shops: dict[str, SectShopDef] | None = None


def _parse_task(entry: dict) -> SectTaskDef:
    return SectTaskDef(
        task_id=str(entry["id"]),
        task_type=str(entry["type"]),
        count=int(entry.get("count", 1)),
        merit=int(entry.get("merit", 10)),
        label=str(entry.get("label", entry["id"])),
        item_id=entry.get("item_id"),
        area_id=entry.get("area_id"),
        beast_tag=entry.get("beast_tag"),
    )


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


def load_sect_task_pools() -> dict[str, dict[str, list[SectTaskDef]]]:
    global _task_pools
    if _task_pools is not None:
        return _task_pools
    with SECT_TASKS_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    parsed: dict[str, dict[str, list[SectTaskDef]]] = {}
    for pool_id, bands in raw.items():
        parsed[pool_id] = {}
        for band, tasks in bands.items():
            parsed[pool_id][band] = [_parse_task(entry) for entry in tasks]
    _task_pools = parsed
    return parsed


def load_sect_shops() -> dict[str, SectShopDef]:
    global _shops
    if _shops is not None:
        return _shops
    with SECT_SHOPS_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    parsed: dict[str, SectShopDef] = {}
    for shop_id, data in raw.items():
        entries = tuple(
            SectShopEntry(
                item_id=str(row["item_id"]),
                merit_cost=int(row["merit_cost"]),
                min_realm_index=int(row.get("min_realm_index", 0)),
            )
            for row in data.get("entries", [])
        )
        parsed[shop_id] = SectShopDef(
            shop_id=shop_id,
            name=str(data.get("name", shop_id)),
            entries=entries,
        )
    return parsed


def invalidate_game_sect_cache() -> None:
    global _sects, _task_pools, _shops
    _sects = None
    _task_pools = None
    _shops = None


def get_sect_def(sect_id: str) -> GameSectDef | None:
    return load_game_sects().get(sect_id)


def get_sect_shop(shop_id: str) -> SectShopDef | None:
    return load_sect_shops().get(shop_id)


def realm_band_for(realm_index: int) -> str:
    for band, lo, hi in REALM_BANDS:
        if lo <= realm_index <= hi:
            return band
    return "mortal"


def utc_today_str(now: datetime | None = None) -> str:
    ts = now or utcnow()
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d")


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
    session.flush()
    return True


def try_grant_sect_invitation_from_adventure(
    session: Session,
    player: Player,
    sect_id: str | None,
    *,
    source: str = "adventure",
) -> str | None:
    if not sect_id:
        return None
    sect = get_sect_def(sect_id)
    if sect is None:
        return None
    if player.game_sect_id == sect_id:
        return None
    if has_sect_invitation(session, player.id, sect_id):
        return None

    tier = karma_tier(player.karma)
    if tier not in sect.karma_requirement:
        return None
    if player.realm_index < sect.min_realm_index:
        return None

    if grant_sect_invitation(session, player.id, sect_id, source=source):
        return f"A sealed invitation to **{sect.name}** finds its way to you."
    return None


def consume_sect_invitation(session: Session, player_id: int, sect_id: str) -> None:
    stmt = select(PlayerSectInvitation).where(
        PlayerSectInvitation.player_id == player_id,
        PlayerSectInvitation.sect_id == sect_id,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        session.delete(row)


def award_sect_merit(player: Player, amount: int) -> int:
    if player.game_sect_id is None or amount <= 0:
        return 0
    player.sect_merit += amount
    return amount


def on_sect_activity(
    session: Session,
    player: Player,
    activity: str,
    *,
    area_id: str | None = None,
    item_id: str | None = None,
    beast_id: str | None = None,
    adventure_success: bool = False,
) -> list[str]:
    """Award passive merit and advance daily task progress. Returns player messages."""
    if player.game_sect_id is None:
        return []

    messages: list[str] = []
    merit = SECT_MERIT_ACTIVITY.get(activity, 0)
    if activity == "adventure" and not adventure_success:
        merit = 0
    if merit > 0:
        gained = award_sect_merit(player, merit)
        messages.append(f"**+{gained}** sect merit ({activity}).")

    task_msgs = record_sect_task_progress(
        session,
        player,
        activity,
        area_id=area_id,
        item_id=item_id,
        beast_id=beast_id,
        adventure_success=adventure_success,
    )
    messages.extend(task_msgs)
    session.add(player)
    return messages


def _tasks_for_player(player: Player) -> list[SectTaskDef]:
    sect = get_sect_def(player.game_sect_id or "")
    if sect is None:
        return []
    pools = load_sect_task_pools()
    band = realm_band_for(player.realm_index)
    pool = pools.get(sect.task_pool_id, {})
    tasks = list(pool.get(band, []))
    if not tasks and band != "mortal":
        tasks = list(pool.get("mortal", []))
    return tasks


def _pick_daily_task(player: Player, today: str) -> SectTaskDef | None:
    tasks = _tasks_for_player(player)
    if not tasks:
        return None
    rng = random.Random(f"{player.id}:{player.game_sect_id}:{today}")
    return rng.choice(tasks)


def ensure_daily_sect_task(session: Session, player: Player, now: datetime | None = None) -> SectTaskDef | None:
    if player.game_sect_id is None:
        return None

    today = utc_today_str(now)
    if player.last_sect_task_date == today:
        return None

    if player.sect_daily_task_date == today and player.sect_daily_task_id:
        for task in _tasks_for_player(player):
            if task.task_id == player.sect_daily_task_id:
                return task
        return None

    task = _pick_daily_task(player, today)
    player.sect_daily_task_id = task.task_id if task else None
    player.sect_daily_task_progress = 0
    player.sect_daily_task_date = today
    session.add(player)
    return task


def get_sect_task_status(player: Player, now: datetime | None = None) -> SectTaskStatus:
    today = utc_today_str(now)
    completed_today = player.last_sect_task_date == today
    if player.game_sect_id is None:
        return SectTaskStatus(None, 0, completed_today, False)

    task: SectTaskDef | None = None
    if player.sect_daily_task_id and player.sect_daily_task_date == today:
        for candidate in _tasks_for_player(player):
            if candidate.task_id == player.sect_daily_task_id:
                task = candidate
                break

    return SectTaskStatus(
        task=task,
        progress=player.sect_daily_task_progress,
        completed_today=completed_today,
        assigned_today=player.sect_daily_task_date == today,
    )


def _complete_daily_task(session: Session, player: Player, task: SectTaskDef, today: str) -> str:
    player.last_sect_task_date = today
    player.sect_daily_task_progress = task.count
    gained = award_sect_merit(player, task.merit)
    session.add(player)
    return f"Daily sect task complete — **+{gained}** merit. (**{task.label}**)"


def record_sect_task_progress(
    session: Session,
    player: Player,
    activity: str,
    *,
    area_id: str | None = None,
    item_id: str | None = None,
    beast_id: str | None = None,
    adventure_success: bool = False,
) -> list[str]:
    if player.game_sect_id is None:
        return []

    today = utc_today_str()
    if player.last_sect_task_date == today:
        return []

    task = ensure_daily_sect_task(session, player)
    if task is None:
        status = get_sect_task_status(player)
        task = status.task
    if task is None:
        return []

    if task.task_type != activity:
        return []

    if task.task_type == "gather":
        if task.area_id and area_id != task.area_id:
            return []
        if task.item_id and item_id != task.item_id:
            return []

    if task.task_type == "hunt" and task.beast_tag:
        if not beast_id or task.beast_tag not in beast_id:
            return []

    if task.task_type == "adventure" and not adventure_success:
        return []

    player.sect_daily_task_progress += 1
    session.add(player)

    if player.sect_daily_task_progress >= task.count:
        return [_complete_daily_task(session, player, task, today)]

    remaining = task.count - player.sect_daily_task_progress
    return [f"Sect task progress: **{player.sect_daily_task_progress}/{task.count}** ({remaining} left)."]


def format_sect_task_status(player: Player) -> str:
    if player.game_sect_id is None:
        return "Join a martial sect with **`/sect-join`** to receive daily tasks."

    status = get_sect_task_status(player)
    if status.completed_today:
        return "Today's sect task is **complete**. A new assignment arrives at **UTC midnight**."

    if status.task is None:
        return "Your sect has no daily tasks configured for your realm band yet."

    progress = min(status.progress, status.task.count)
    return (
        f"**Daily task:** {status.task.label}\n"
        f"Progress: **{progress}/{status.task.count}** · Reward: **{status.task.merit}** merit"
    )


def list_sect_shop_entries(player: Player) -> tuple[SectShopDef | None, list[SectShopEntry]]:
    sect = get_sect_def(player.game_sect_id or "")
    if sect is None:
        return None, []
    shop = get_sect_shop(sect.shop_id)
    if shop is None:
        return None, []
    eligible = [
        entry
        for entry in shop.entries
        if player.realm_index >= entry.min_realm_index
        and _shop_entry_allowed(entry.item_id)
    ]
    return shop, eligible


def _shop_entry_allowed(item_id: str) -> bool:
    tech = get_technique_by_manual(item_id)
    if tech is None:
        return True
    return rarity_at_most(tech.rarity, "uncommon")


def buy_from_sect_shop(
    session: Session,
    player: Player,
    item_id: str,
) -> tuple[bool, str]:
    if player.game_sect_id is None:
        return False, "You must belong to a martial sect to use its shop."

    shop, entries = list_sect_shop_entries(player)
    if shop is None:
        return False, "Your sect has no shop configured."

    entry = next((row for row in entries if row.item_id == item_id), None)
    if entry is None:
        return False, "That manual is not sold at your sect's pavilion (or requires a higher realm)."

    if player.sect_merit < entry.merit_cost:
        return False, (
            f"You need **{entry.merit_cost}** sect merit "
            f"(you have **{player.sect_merit}**)."
        )

    player.sect_merit -= entry.merit_cost
    add_item(session, player.id, item_id, 1)
    session.add(player)
    return True, f"Purchased **{get_item_name(item_id)}** for **{entry.merit_cost}** sect merit."


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
    player.last_sect_task_date = None
    player.sect_daily_task_id = None
    player.sect_daily_task_progress = 0
    player.sect_daily_task_date = None
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
    player.sect_daily_task_id = None
    player.sect_daily_task_progress = 0
    player.sect_daily_task_date = None
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
    invited = ""
    if has_sect_invitation(session, player.id, sect.sect_id):
        invited = " · **invitation in hand**"
    return (
        f"**{sect.name}** (`{sect.sect_id}`) — {sect.tagline}\n"
        f"_{karma_req} · {realm_note}{invite}{invited}_"
    )


def format_player_sect_status(player: Player) -> str:
    if player.game_sect_id is None:
        pending = ""
        return (
            "You are **sectless** — a wandering cultivator.\n"
            "Use **`/sect-list`** to see martial orders and **`/sect-join`** to petition entry."
            + pending
        )

    sect = get_sect_def(player.game_sect_id)
    if sect is None:
        return f"Unknown sect `{player.game_sect_id}`."

    lines = [
        f"**{sect.name}** — {sect.tagline}",
        f"**Sect merit:** {player.sect_merit}",
        sect.description,
        format_sect_task_status(player),
        "Use **`/sect-shop`** and **`/sect-buy`** to spend merit on manuals.",
    ]
    if sect.join_type == "secret":
        lines.insert(3, "_You were invited — few ever see this path._")
    return "\n".join(lines)


def format_sect_shop_listing(player: Player) -> str:
    shop, entries = list_sect_shop_entries(player)
    if shop is None:
        return "Your sect has no merit shop yet."
    if not entries:
        return f"**{shop.name}** has nothing you can purchase at your realm."

    lines = [f"**{shop.name}** — your merit: **{player.sect_merit}**"]
    for entry in entries:
        name = get_item_name(entry.item_id)
        lines.append(f"• **{name}** — **{entry.merit_cost}** merit · `/sect-buy` `{entry.item_id}`")
    return "\n".join(lines)
