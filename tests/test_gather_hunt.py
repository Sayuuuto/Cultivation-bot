from __future__ import annotations

import random
from datetime import datetime, timezone

from src.config import Config
from src.cooldown_haste import get_haste_reduction_seconds
from src.effects import add_void_pulse_haste
from src.gather import run_gather
from src.hunt import run_hunt
from src.inventory import get_item_quantity
from src.reminders import REMINDER_ACTIVITIES, compute_ready_at


def test_gather_grants_materials(session, player):
    res = run_gather(session, player, "bamboo_grove", rng=random.Random(7))
    session.commit()
    assert res.success is True
    assert len(res.drops) >= 1
    total = sum(get_item_quantity(session, player.id, item_id) for item_id in res.drops)
    assert total > 0


def test_hunt_victory_grants_drops(session, player):
    player.realm_index = 2
    player.substage = 2
    session.commit()
    res = run_hunt(session, player, "bamboo_grove", rng=random.Random(99))
    session.commit()
    assert res.combat is not None
    if res.success:
        assert len(res.drops) >= 1


def test_gather_rejects_underleveled_area(session, player):
    player.realm_index = 0
    session.commit()
    res = run_gather(session, player, "moonwell_ruins", rng=random.Random(1))
    assert res.success is False


def test_reminders_include_gather_and_hunt():
    assert "gather" in REMINDER_ACTIVITIES
    assert "hunt" in REMINDER_ACTIVITIES


def test_void_pulse_includes_gather_hunt_haste(session, player):
    add_void_pulse_haste(session, player.id)
    session.commit()
    assert get_haste_reduction_seconds(session, player.id, "gather") == 180
    assert get_haste_reduction_seconds(session, player.id, "hunt") == 180


def test_gather_ready_at(session, player, cfg):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    player.last_gather_at = now
    session.commit()
    ready = compute_ready_at(session, player, cfg, "gather", now=now)
    assert (ready - now).total_seconds() == cfg.gather_cooldown_seconds
