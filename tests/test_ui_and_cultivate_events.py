from __future__ import annotations

import random

from src.cultivate_events import roll_cultivate_event
from src.game import cultivate
from src.ui.formatting import format_hp_bar, format_qi_bar


def test_format_hp_bar_full_and_empty():
    assert format_hp_bar(10, 10, length=5) == "🟩" * 5
    assert format_hp_bar(0, 10, length=5) == "⬛" * 5


def test_format_hp_bar_partial():
    bar = format_hp_bar(5, 10, length=10)
    assert bar.count("🟩") >= 4
    assert bar.count("⬛") >= 4


def test_format_qi_bar_uses_blue():
    assert "🔵" in format_qi_bar(50, 100)


def test_roll_cultivate_event_respects_chance(monkeypatch):
    monkeypatch.setattr(
        "src.cultivate_events._load_config",
        lambda: (0.0, [{"id": "x", "weight": 1, "emoji": "✨", "title": "T", "message": "m"}]),
    )
    assert roll_cultivate_event(random.Random(1)) is None


def test_roll_cultivate_event_picks_entry(monkeypatch):
    monkeypatch.setattr(
        "src.cultivate_events._load_config",
        lambda: (
            1.0,
            [
                {
                    "id": "spirit_surge",
                    "weight": 100,
                    "emoji": "🌊",
                    "title": "Spirit Surge",
                    "message": "Surge!",
                    "qi_mult": 2.5,
                }
            ],
        ),
    )
    event = roll_cultivate_event(random.Random(1))
    assert event is not None
    assert event.event_id == "spirit_surge"
    assert event.qi_mult == 2.5


def test_cultivate_rare_events_fire(session, player, cfg):
    events: set[str] = set()
    for seed in range(400):
        res = cultivate(
            player,
            None,
            cfg,
            rng=random.Random(seed),
            session=session,
            player_id=player.id,
        )
        if res.event_id:
            events.add(res.event_id)
    assert len(events) >= 2


def test_cultivate_spirit_surge_outpaces_normal(session, player, cfg, monkeypatch):
    monkeypatch.setattr(
        "src.cultivate_events._load_config",
        lambda: (
            1.0,
            [
                {
                    "id": "spirit_surge",
                    "weight": 100,
                    "emoji": "🌊",
                    "title": "Spirit Surge",
                    "message": "Surge!",
                    "qi_mult": 3.0,
                }
            ],
        ),
    )
    player.qi = 0
    res = cultivate(player, None, cfg, rng=random.Random(1), session=session, player_id=player.id)
    assert res.event_id == "spirit_surge"
    assert res.qi_gain >= 20
