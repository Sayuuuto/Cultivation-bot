from __future__ import annotations

import random
from datetime import datetime, timezone

from src.auto_combat import AutoCombatResult
from src.config import Config
from src.cooldown_haste import get_haste_reduction_seconds
from src.effects import add_haste_effect
from src.gather import run_gather
from src.hunt import finalize_hunt_combat, run_hunt
from src.inventory import get_item_quantity
from src.reminders import REMINDER_ACTIVITIES, compute_ready_at


def test_gather_grants_materials(session, player):
    res = run_gather(session, player, "mortal_grove", rng=random.Random(7))
    session.commit()
    assert res.success is True
    assert len(res.drops) >= 1
    total = sum(get_item_quantity(session, player.id, item_id) for item_id in res.drops)
    assert total > 0


def test_hunt_victory_grants_drops(session, player):
    player.realm_index = 2
    player.substage = 2
    session.commit()
    res = run_hunt(session, player, "mortal_grove", rng=random.Random(99))
    session.commit()
    assert res.combat is not None
    if res.success:
        assert len(res.drops) >= 1


def test_hunt_victory_has_fallback_drop_when_rolls_miss(session, player, monkeypatch):
    """Regression: a won 5-minute hunt should not end with zero usable materials."""
    monkeypatch.setattr("src.hunt.roll_creature_loot", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "src.hunt.pick_manual_from_pool",
        lambda *args, **kwargs: None,
    )

    res = finalize_hunt_combat(
        session,
        player,
        "mortal_grove",
        "bamboo_serpent",
        True,
        rng=random.Random(1),
    )
    session.commit()

    assert res.success is True
    assert res.drops == {"minor_beast_core": 1}
    assert "no usable materials" not in "\n".join(res.messages).lower()
    assert get_item_quantity(session, player.id, "minor_beast_core") == 1


def test_auto_hunt_victory_has_fallback_drop_when_rolls_miss(session, player, monkeypatch):
    monkeypatch.setattr("src.hunt.roll_creature_loot", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "src.auto_combat.resolve_auto_combat",
        lambda *args, **kwargs: AutoCombatResult(
            victory=True,
            beast_name="Bamboo Serpent",
            rounds_fought=1,
            player_hp_remaining=80,
            player_hp_start=80,
            beast_hp_remaining=0,
            beast_hp_start=55,
            log_lines=["Bamboo Serpent falls."],
        ),
    )
    monkeypatch.setattr("src.hunt._pick_beast", lambda beasts, rng: beasts[1])

    res = run_hunt(session, player, "mortal_grove", rng=random.Random(1))
    session.commit()

    assert res.success is True
    assert res.drops == {"minor_beast_core": 1}
    assert "no usable materials" not in "\n".join(res.messages).lower()


def test_gather_rejects_underleveled_area(session, player):
    player.realm_index = 0
    session.commit()
    res = run_gather(session, player, "foundation_ruins", rng=random.Random(1))
    assert res.success is False


def test_reminders_include_gather_and_hunt():
    assert "gather" in REMINDER_ACTIVITIES
    assert "hunt" in REMINDER_ACTIVITIES


def test_void_pulse_includes_gather_hunt_haste(session, player):
    add_haste_effect(session, player.id, "void_pulse_pill")
    session.commit()
    assert get_haste_reduction_seconds(session, player.id, "gather") == 3600
    assert get_haste_reduction_seconds(session, player.id, "hunt") == 3600


def test_gather_ready_at(session, player, cfg):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    player.last_gather_at = now
    session.commit()
    ready = compute_ready_at(session, player, cfg, "gather", now=now)
    assert (ready - now).total_seconds() == cfg.gather_cooldown_seconds
