from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config import Config
from src.game import to_utc
from src.models import PlayerEffect, PlayerReminder
from src.reminders import (
    REMINDER_ACTIVITIES,
    compute_ready_at,
    fetch_due_reminders,
    mark_reminder_sent,
    schedule_after_activity,
    set_reminder_enabled,
)


@pytest.fixture
def cfg() -> Config:
    return Config(
        discord_token="test",
        guild_id="test-guild",
        database_path=":memory:",
        announce_channel_id=None,
        tutorial_channel_id=None,
        library_channel_id=None,
        abode_category_id=None,
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)


def test_set_reminder_enabled_schedules_cultivate(session, player, cfg, now):
    player.last_cultivate_at = now
    session.add(player)
    session.commit()

    reminder = set_reminder_enabled(session, player, cfg, "cultivate", True, now)
    session.commit()

    assert reminder.enabled is True
    assert reminder.ready_at is not None
    assert to_utc(reminder.ready_at) == now + timedelta(seconds=cfg.cultivate_cooldown_seconds)


def test_schedule_after_activity_respects_haste(session, player, cfg, now):
    session.add(
        PlayerEffect(
            player_id=player.id,
            effect_id="haste_cultivate",
            charges=2,
            value_int=600,
        )
    )
    player.last_cultivate_at = now
    session.add(player)
    set_reminder_enabled(session, player, cfg, "cultivate", True, now)
    session.commit()

    schedule_after_activity(session, player, cfg, "cultivate", now)
    session.commit()

    reminder = session.query(PlayerReminder).filter_by(player_id=player.id, activity="cultivate").one()
    expected = now + timedelta(seconds=cfg.cultivate_cooldown_seconds - 600)
    assert to_utc(reminder.ready_at) == expected
    assert reminder.sent_at is None


def test_fetch_due_reminders(session, player, cfg, now):
    set_reminder_enabled(session, player, cfg, "cultivate", True, now)
    reminder = session.query(PlayerReminder).filter_by(player_id=player.id, activity="cultivate").one()
    reminder.ready_at = now - timedelta(seconds=1)
    session.commit()

    due = fetch_due_reminders(session, now)
    assert len(due) == 1
    assert due[0].player.id == player.id
    assert due[0].reminder.activity == "cultivate"


def test_mark_reminder_sent_prevents_duplicate_fetch(session, player, cfg, now):
    set_reminder_enabled(session, player, cfg, "adventure", True, now)
    reminder = session.query(PlayerReminder).filter_by(player_id=player.id, activity="adventure").one()
    reminder.ready_at = now - timedelta(seconds=1)
    mark_reminder_sent(session, reminder, now)
    session.commit()

    assert fetch_due_reminders(session, now) == []


def test_daily_ready_at_after_claim(session, player, cfg, now):
    player.last_daily_at = now
    session.add(player)
    session.commit()

    ready = compute_ready_at(session, player, cfg, "daily", now)
    assert ready.date() == (now.date() + timedelta(days=1))


def test_daily_ready_now_if_not_claimed(session, player, cfg, now):
    ready = compute_ready_at(session, player, cfg, "daily", now)
    assert ready == now


def test_disable_clears_schedule(session, player, cfg, now):
    set_reminder_enabled(session, player, cfg, "duel", True, now)
    set_reminder_enabled(session, player, cfg, "duel", False, now)
    session.commit()

    reminder = session.query(PlayerReminder).filter_by(player_id=player.id, activity="duel").one()
    assert reminder.enabled is False
    assert reminder.ready_at is None


def test_all_activities_have_labels():
    assert set(REMINDER_ACTIVITIES) == {"cultivate", "adventure", "dungeon", "duel", "daily", "gather", "hunt"}
