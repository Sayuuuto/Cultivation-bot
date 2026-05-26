from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.bot import cooldown_remaining, format_seconds, to_utc


def test_cooldown_remaining_none():
    now = datetime.now(timezone.utc)
    assert cooldown_remaining(now, None, 900) == 0


def test_cooldown_remaining_with_naive_last():
    now = datetime.now(timezone.utc)
    last = (now - timedelta(minutes=5)).replace(tzinfo=None)
    remaining = cooldown_remaining(now, last, 900)  # 15 min cooldown
    assert 500 <= remaining <= 610  # ~10 minutes left


def test_cooldown_remaining_expired():
    now = datetime.now(timezone.utc)
    last = now - timedelta(minutes=20)
    assert cooldown_remaining(now, last, 900) == 0


def test_to_utc_bot_helper():
    naive = datetime(2026, 1, 1, 0, 0, 0)
    assert to_utc(naive).tzinfo == timezone.utc


def test_format_seconds():
    assert format_seconds(45) == "45s"
    assert format_seconds(125) == "2m 5s"
    assert format_seconds(3665) == "1h 1m"
