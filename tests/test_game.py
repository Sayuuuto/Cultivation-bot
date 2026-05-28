from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from src.character import get_character_modifiers
from src.config import Config
from src.effects import add_effect
from src.game import (
    CULTIVATE_QI_CAP_FRACTION,
    breakthrough,
    collect_passive_qi,
    compute_breakthrough_preview,
    compute_daily_rewards,
    cultivate,
    duel,
    passive_bank_cap_qi,
    qi_cap,
    sync_passive_qi_bank,
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


def test_passive_bank_accrues_with_naive_clock(player: Player, cfg: Config):
    now = datetime.now(timezone.utc)
    player.passive_accrual_at = (now - timedelta(hours=1)).replace(tzinfo=None)
    player.passive_qi_bank = 0
    sync_passive_qi_bank(player, now)
    bank_cap = passive_bank_cap_qi(player.realm_index, substage=player.substage, player=player)
    assert 0 < player.passive_qi_bank <= bank_cap


def test_collect_passive_qi_moves_bank_to_pool(player: Player, cfg: Config):
    now = datetime.now(timezone.utc)
    player.passive_accrual_at = now - timedelta(hours=2)
    player.passive_qi_bank = 0
    before = player.qi
    collected = collect_passive_qi(player, now)
    assert collected > 0
    assert player.qi == before + collected
    assert player.passive_qi_bank == 0


def test_cultivate_increases_qi(player: Player, cfg: Config):
    rng = random.Random(42)
    before_qi = player.qi
    res = cultivate(player, None, cfg, rng=rng)
    assert res.qi_gain > 0
    assert player.qi == before_qi + res.qi_gain


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


def test_breakthrough_succeeds_when_qi_far_above_cap(player: Player, cfg: Config):
    cap = qi_cap(player.realm_index, player.substage, player)
    player.qi = cap * 5
    res = breakthrough(player, cfg, rng=FixedRoll(0.0))
    assert res.success is True
    assert player.substage == 1


def test_breakthrough_chance_is_fraction_not_percent(player: Player, cfg: Config):
    """96% display means success_chance=0.96; roll 0.95 should succeed, 0.97 should fail."""
    from src.modifiers import CharacterModifiers

    cap = qi_cap(player.realm_index, player.substage, player)
    player.qi = cap
    mod = CharacterModifiers(breakthrough_stability=0.06)
    preview = compute_breakthrough_preview(player, mod)
    assert preview.success_chance == pytest.approx(0.96, abs=0.001)
    res = breakthrough(player, cfg, rng=FixedRoll(0.95), mod=mod)
    assert res.success is True
    assert res.roll == pytest.approx(0.95)

    player.substage = 0
    player.qi = cap
    res_fail = breakthrough(player, cfg, rng=FixedRoll(0.97), mod=mod)
    assert res_fail.success is False
    assert res_fail.roll == pytest.approx(0.97)


def test_breakthrough_daily_seeded_rng_repeats_first_roll():
    """Document the old bug: same guild+user+date always produced identical roll 1."""
    from datetime import date

    day = date(2026, 5, 27).isoformat()
    seed = hash(("guild", "user", day)) & 0xFFFFFFFF
    assert random.Random(seed).random() == random.Random(seed).random()


def test_rng_for_differs_between_calls():
    from src.bot import rng_for

    rolls = [rng_for("g1", "u1").random() for _ in range(8)]
    assert len(set(rolls)) > 1


def test_breakthrough_roll_includes_clarity_pills(session, player: Player, cfg: Config):
    cap = qi_cap(player.realm_index, player.substage, player)
    player.qi = cap
    add_effect(session, player.id, "clarity", charges=3)
    session.commit()
    mod = get_character_modifiers(session, player)
    preview = compute_breakthrough_preview(player, mod, session=session, player_id=player.id)
    res = breakthrough(
        player, cfg, rng=FixedRoll(0.99), mod=mod, session=session, player_id=player.id
    )
    assert res.success_chance == pytest.approx(preview.success_chance, abs=0.001)
    assert res.success is True


def test_duel_is_stones_only(player: Player, cfg: Config):
    opponent = Player(
        guild_id=player.guild_id,
        discord_id="opponent",
        dao_name="Opponent",
        realm_index=0,
        substage=0,
        qi=50,
        spirit_stones=0,
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


