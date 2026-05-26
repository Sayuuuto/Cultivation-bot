from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from src.config import Config
from src.game import (
    apply_offline_progress,
    apply_stamina_regen,
    breakthrough,
    compute_daily_rewards,
    compute_stamina_regen,
    cultivate,
    duel,
    qi_cap,
    to_utc,
)
from src.models import Player


def test_qi_cap_scales_with_substage():
    assert qi_cap(0, 0) == 100
    assert qi_cap(0, 1) == 150
    assert qi_cap(0, 2) == 220


def test_to_utc_handles_naive_and_aware():
    naive = datetime(2026, 5, 26, 12, 0, 0)
    aware = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
    assert to_utc(naive).tzinfo == timezone.utc
    assert to_utc(aware) == aware


def test_stamina_regen_with_naive_db_timestamp():
    now = datetime.now(timezone.utc)
    naive_last = datetime(2026, 5, 26, 10, 0, 0)  # 2 hours ago if now is 12:00 - use fixed delta instead
    last = now - timedelta(hours=2)
    naive_last = last.replace(tzinfo=None)
    regen = compute_stamina_regen(naive_last, now)
    assert regen == 20  # 2 hours * 10 per hour, capped at 100


def test_offline_progress_with_naive_last_active(player: Player, cfg: Config):
    now = datetime.now(timezone.utc)
    player.last_active_at = (now - timedelta(hours=1)).replace(tzinfo=None)
    qi = apply_offline_progress(player, now, cfg.offline_cap_minutes)
    assert qi > 0


def test_cultivate_increases_qi(player: Player, cfg: Config):
    rng = random.Random(42)
    before_qi = player.qi
    res = cultivate(player, None, cfg, rng=rng)
    assert res.qi_gain > 0
    assert player.qi == before_qi + res.qi_gain
    assert player.stamina < 100


def test_breakthrough_requires_full_qi(player: Player, cfg: Config):
    player.qi = 50
    res = breakthrough(player, cfg, rng=random.Random(1))
    assert res.success is False
    assert "not sufficient" in res.message.lower()
    assert player.substage == 0


class FixedRoll:
    """Deterministic RNG stand-in for breakthrough tests."""

    def __init__(self, roll: float):
        self._roll = roll

    def random(self) -> float:
        return self._roll


def test_breakthrough_success_advances_substage(player: Player, cfg: Config):
    cap = qi_cap(player.realm_index, player.substage)
    player.qi = cap
    res = breakthrough(player, cfg, rng=FixedRoll(0.0))
    assert res.success is True
    assert player.substage == 1


def test_breakthrough_failure_reduces_qi(player: Player, cfg: Config):
    cap = qi_cap(player.realm_index, player.substage)
    player.qi = cap
    before = player.qi
    res = breakthrough(player, cfg, rng=FixedRoll(0.99))
    assert res.success is False
    assert player.qi < before


def test_duel_is_stones_only(player: Player, cfg: Config):
    opponent = Player(
        guild_id=player.guild_id,
        discord_id="opponent",
        dao_name="Opponent",
        realm_index=0,
        substage=0,
        qi=50,
        spirit_stones=0,
        stamina=100,
        stamina_last_updated_at=player.stamina_last_updated_at,
        last_active_at=player.last_active_at,
    )
    player.qi = 50
    player_challenger_qi = player.qi
    opponent_qi = opponent.qi

    res = duel(player, opponent, cfg, rng=random.Random(1))
    assert res.qi_transfer == 0
    assert player.qi == player_challenger_qi
    assert opponent.qi == opponent_qi
    assert res.stones_delta_winner > 0


def test_daily_rewards_scale_with_streak(player: Player):
    player.daily_streak = 5
    stones, qi = compute_daily_rewards(player)
    assert stones == 50 + 10  # base + streak bonus (min(20, streak*2))
    assert qi == 10 + player.realm_index * 2


def test_apply_stamina_regen(player: Player):
    now = datetime.now(timezone.utc)
    player.stamina = 50
    player.stamina_last_updated_at = (now - timedelta(hours=3)).replace(tzinfo=None)
    apply_stamina_regen(player, now)
    assert player.stamina == 80
