from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Config
from .cooldown_haste import get_haste_reduction_seconds
from .game import to_utc, utcnow
from .guidance import _daily_claimed_today
from .models import Player, PlayerReminder

REMINDER_ACTIVITIES = ("cultivate", "adventure", "dungeon", "duel", "daily")

ACTIVITY_LABELS: dict[str, str] = {
    "cultivate": "/cultivate",
    "adventure": "/adventure",
    "dungeon": "/dungeon",
    "duel": "/duel",
    "daily": "/daily",
}

ACTIVITY_COOLDOWN_ATTR: dict[str, str] = {
    "cultivate": "cultivate_cooldown_seconds",
    "adventure": "adventure_cooldown_seconds",
    "dungeon": "dungeon_cooldown_seconds",
    "duel": "pvp_cooldown_seconds",
}

HASTE_ACTIVITIES = frozenset({"cultivate", "adventure", "dungeon"})

LAST_ACTIVITY_ATTR: dict[str, str] = {
    "cultivate": "last_cultivate_at",
    "adventure": "last_adventure_at",
    "dungeon": "last_dungeon_at",
    "duel": "last_pvp_at",
    "daily": "last_daily_at",
}

REMINDER_MESSAGES: dict[str, str] = {
    "cultivate": "Your meridians settle — **`/cultivate`** is ready.",
    "adventure": "The path clears — **`/adventure`** awaits.",
    "dungeon": "The dungeon gate stirs — **`/dungeon`** is ready.",
    "duel": "Your dao steadies — you may **`/duel`** again.",
    "daily": "A new UTC day begins — your **`/daily`** stipend is ready.",
}


@dataclass(frozen=True)
class ReminderRow:
    reminder: PlayerReminder
    player: Player


def _next_utc_midnight_after(now: datetime) -> datetime:
    now = to_utc(now)
    next_day = now.date() + timedelta(days=1)
    return datetime(next_day.year, next_day.month, next_day.day, tzinfo=timezone.utc)


def _cooldown_remaining(
    session: Session,
    player: Player,
    cfg: Config,
    activity: str,
    now: datetime,
) -> int:
    if activity == "daily":
        return 0 if not _daily_claimed_today(player, now) else -1

    attr = LAST_ACTIVITY_ATTR[activity]
    last = getattr(player, attr)
    cooldown = getattr(cfg, ACTIVITY_COOLDOWN_ATTR[activity])
    if last is None:
        return 0

    now = to_utc(now)
    last = to_utc(last)
    elapsed = (now - last).total_seconds()
    remaining = int(cooldown - elapsed)
    if activity in HASTE_ACTIVITIES:
        remaining -= get_haste_reduction_seconds(session, player.id, activity)
    return max(0, remaining)


def compute_ready_at(
    session: Session,
    player: Player,
    cfg: Config,
    activity: str,
    now: datetime | None = None,
) -> datetime:
    now = to_utc(now or utcnow())
    if activity == "daily":
        if _daily_claimed_today(player, now):
            return _next_utc_midnight_after(now)
        return now

    remaining = _cooldown_remaining(session, player, cfg, activity, now)
    if remaining <= 0:
        return now
    return now + timedelta(seconds=remaining)


def get_or_create_reminder(session: Session, player_id: int, activity: str) -> PlayerReminder:
    stmt = select(PlayerReminder).where(
        PlayerReminder.player_id == player_id,
        PlayerReminder.activity == activity,
    )
    reminder = session.execute(stmt).scalar_one_or_none()
    if reminder is None:
        reminder = PlayerReminder(player_id=player_id, activity=activity, enabled=False)
        session.add(reminder)
        session.flush()
    return reminder


def get_player_reminders(session: Session, player_id: int) -> dict[str, PlayerReminder]:
    stmt = select(PlayerReminder).where(PlayerReminder.player_id == player_id)
    rows = session.execute(stmt).scalars().all()
    by_activity = {row.activity: row for row in rows}
    for activity in REMINDER_ACTIVITIES:
        if activity not in by_activity:
            by_activity[activity] = get_or_create_reminder(session, player_id, activity)
    return by_activity


def refresh_reminder_schedule(
    session: Session,
    player: Player,
    cfg: Config,
    activity: str,
    now: datetime | None = None,
) -> None:
    reminder = get_or_create_reminder(session, player.id, activity)
    if not reminder.enabled:
        return
    reminder.ready_at = compute_ready_at(session, player, cfg, activity, now)
    reminder.sent_at = None
    session.add(reminder)


def schedule_after_activity(
    session: Session,
    player: Player,
    cfg: Config,
    activity: str,
    now: datetime | None = None,
) -> None:
    refresh_reminder_schedule(session, player, cfg, activity, now)


def set_reminder_enabled(
    session: Session,
    player: Player,
    cfg: Config,
    activity: str,
    enabled: bool,
    now: datetime | None = None,
) -> PlayerReminder:
    reminder = get_or_create_reminder(session, player.id, activity)
    reminder.enabled = enabled
    if enabled:
        refresh_reminder_schedule(session, player, cfg, activity, now)
    else:
        reminder.ready_at = None
        reminder.sent_at = None
    session.add(reminder)
    return reminder


def set_all_reminders_enabled(
    session: Session,
    player: Player,
    cfg: Config,
    enabled: bool,
    now: datetime | None = None,
) -> None:
    for activity in REMINDER_ACTIVITIES:
        set_reminder_enabled(session, player, cfg, activity, enabled, now)


def fetch_due_reminders(session: Session, now: datetime | None = None) -> list[ReminderRow]:
    now = to_utc(now or utcnow())
    stmt = (
        select(PlayerReminder, Player)
        .join(Player, Player.id == PlayerReminder.player_id)
        .where(
            PlayerReminder.enabled.is_(True),
            PlayerReminder.ready_at.is_not(None),
            PlayerReminder.ready_at <= now,
            PlayerReminder.sent_at.is_(None),
        )
    )
    rows = session.execute(stmt).all()
    return [ReminderRow(reminder=reminder, player=player) for reminder, player in rows]


def mark_reminder_sent(session: Session, reminder: PlayerReminder, now: datetime | None = None) -> None:
    reminder.sent_at = to_utc(now or utcnow())
    session.add(reminder)


def format_reminder_status_line(
    session: Session,
    player: Player,
    cfg: Config,
    activity: str,
    reminder: PlayerReminder,
    now: datetime,
) -> str:
    label = ACTIVITY_LABELS[activity]
    if not reminder.enabled:
        return f"**{label}** — reminders **off**"

    if reminder.ready_at is None:
        return f"**{label}** — reminders **on** · waiting for your next action"

    ready_at = to_utc(reminder.ready_at)
    if reminder.sent_at is not None:
        return f"**{label}** — reminders **on** · last ping sent"

    if ready_at <= to_utc(now):
        remaining = _cooldown_remaining(session, player, cfg, activity, now)
        if activity == "daily" and remaining == 0:
            return f"**{label}** — reminders **on** · **ready now** (ping pending)"
        if remaining <= 0:
            return f"**{label}** — reminders **on** · **ready now** (ping pending)"
        return f"**{label}** — reminders **on** · **ready now** (ping pending)"

    delta = ready_at - to_utc(now)
    minutes = max(1, int(delta.total_seconds() // 60))
    if minutes >= 60:
        hours = minutes // 60
        mins = minutes % 60
        when = f"{hours}h {mins}m" if mins else f"{hours}h"
    else:
        when = f"{minutes}m"
    return f"**{label}** — reminders **on** · ping in **~{when}**"


def build_reminder_status_text(
    session: Session,
    player: Player,
    cfg: Config,
    now: datetime | None = None,
) -> str:
    now = to_utc(now or utcnow())
    reminders = get_player_reminders(session, player.id)
    lines = [
        format_reminder_status_line(session, player, cfg, activity, reminders[activity], now)
        for activity in REMINDER_ACTIVITIES
    ]
    footer = "Use **`/remind on cultivate`** (or `adventure`, `dungeon`, `duel`, `daily`, `all`) to opt in."
    if player.remind_dms_blocked:
        footer += "\n⚠️ DMs are blocked — enable DMs from server members to receive pings."
    return "\n".join(lines) + f"\n\n{footer}"


def reminder_dm_content(activity: str, player: Player) -> str:
    msg = REMINDER_MESSAGES.get(activity, "An action is ready.")
    return f"**{player.dao_name}** — {msg}\nCheck **`/cooldown`** for all timers."
