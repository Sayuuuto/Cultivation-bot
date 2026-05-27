from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import Config
from src.bot import cooldown_remaining
from src.guidance import (
    build_cooldown_lines,
    format_cooldown_status,
    get_help_sections,
    get_next_steps,
    get_reroll_cooldown_line,
    get_start_next_steps,
    get_welcome_intro,
)
from src.models import Player


def test_welcome_intro_mentions_core_loop():
    intro = get_welcome_intro()
    assert "cultivat" in intro.lower()
    assert "/daily" in intro or "daily" in intro.lower()


def test_start_next_steps_lists_commands():
    steps = get_start_next_steps()
    assert "/daily" in steps
    assert "/profile" in steps
    assert "/help" in steps


def test_help_sections_cover_pve():
    sections = dict(get_help_sections())
    assert "Exploration & crafting" in sections
    assert "Martial techniques" in sections
    assert "/techniques" in sections["Martial techniques"]
    assert "/gather" in sections["Exploration & crafting"]


def test_cooldown_lines_ready_cultivate(player: Player, cfg: Config):
    now = datetime.now(timezone.utc)
    player.last_cultivate_at = None
    lines = build_cooldown_lines(player, cfg, now, lambda _n, _l, _s: 0)
    assert any("/cultivate" in line and "Ready now" in line for line in lines)


def test_cooldown_lines_daily_on_cooldown(player: Player, cfg: Config):
    now = datetime.now(timezone.utc)
    player.last_daily_at = now
    lines = build_cooldown_lines(
        player,
        cfg,
        now,
        cooldown_remaining,
    )
    daily_line = next(line for line in lines if "/daily" in line)
    assert "Ready now" not in daily_line
    assert "24h" in daily_line


def test_reroll_free_available(player: Player):
    player.spirit_root_reroll_free_used = False
    line = get_reroll_cooldown_line(player, datetime.now(timezone.utc))
    assert "free reroll" in line.lower()


def test_next_steps_suggest_breakthrough_at_cap(player: Player, cfg: Config):
    from src.game import qi_cap

    now = datetime.now(timezone.utc)
    cap = qi_cap(player.realm_index, player.substage)
    player.qi = cap
    hint = get_next_steps("cultivate", player, None, cfg, now, lambda _n, _l, _s: 900)
    assert "breakthrough" in hint.lower()


def test_format_cooldown_status():
    assert format_cooldown_status(0) == "Ready now"
    assert "m" in format_cooldown_status(125)
