import random

import pytest

from src.area_risk import underleveled_drop_bonus
from src.spirit_stone_drops import (
    fortune_qty_mult,
    grant_coop_dungeon_clear_stones,
    grant_hunt_spirit_stones,
    grant_solo_dungeon_clear_stones,
    load_spirit_stone_drop_config,
    roll_hunt_spirit_stones,
)


def test_fortune_qty_mult_caps_bonus():
    ref = 12.0
    at_baseline = fortune_qty_mult(12, 0, ref, cap=0.5, sqrt_scale=0.22, drop_luck_as_luck=150)
    assert at_baseline == 1.0
    doubled = fortune_qty_mult(24, 0, ref, cap=0.5, sqrt_scale=0.22, drop_luck_as_luck=150)
    assert 1.2 <= doubled <= 1.25
    huge = fortune_qty_mult(1200, 0, ref, cap=0.5, sqrt_scale=0.22, drop_luck_as_luck=150)
    assert huge == pytest.approx(1.5)


def test_hunt_stones_scale_with_realm_and_tier():
    rng = random.Random(42)
    mortal_normal = [
        roll_hunt_spirit_stones(
            rng,
            area_min_realm=0,
            player_realm_index=0,
            combat_tier="normal",
            gap=0,
            luck=12,
            drop_luck=0,
        )
        for _ in range(200)
    ]
    foundation_elite = [
        roll_hunt_spirit_stones(
            rng,
            area_min_realm=2,
            player_realm_index=2,
            combat_tier="elite",
            gap=0,
            luck=160,
            drop_luck=0,
        )
        for _ in range(200)
    ]
    assert sum(mortal_normal) / max(1, len([x for x in mortal_normal if x])) >= 3
    assert max(foundation_elite) >= max(mortal_normal)


def test_hunt_overlevel_penalty(session, player):
    rng = random.Random(7)
    cfg = load_spirit_stone_drop_config().hunt
    high = roll_hunt_spirit_stones(
        rng,
        area_min_realm=0,
        player_realm_index=0,
        combat_tier="normal",
        gap=0,
        luck=12,
        drop_luck=0,
    )
    rng2 = random.Random(7)
    low = roll_hunt_spirit_stones(
        rng2,
        area_min_realm=0,
        player_realm_index=cfg.overlevel_realms_before_penalty,
        combat_tier="normal",
        gap=0,
        luck=12,
        drop_luck=0,
    )
    if high > 0 and low > 0:
        assert low <= high


def test_grant_hunt_spirit_stones_on_victory(session, player):
    player.spirit_stones = 0
    before = player.spirit_stones
    granted = 0
    for seed in range(50):
        player.spirit_stones = before
        stones, msg = grant_hunt_spirit_stones(
            session,
            player,
            random.Random(seed),
            area_min_realm=0,
            combat_tier="normal",
            gap=0,
        )
        if stones > 0:
            granted = stones
            assert msg is not None
            assert player.spirit_stones == before + stones
            break
    assert granted > 0


def test_underleveled_bonus_applies_to_hunt_stones():
    base = roll_hunt_spirit_stones(
        random.Random(1),
        area_min_realm=1,
        player_realm_index=1,
        combat_tier="normal",
        gap=0,
        luck=45,
        drop_luck=0,
    )
    boosted = roll_hunt_spirit_stones(
        random.Random(1),
        area_min_realm=1,
        player_realm_index=0,
        combat_tier="normal",
        gap=1,
        luck=45,
        drop_luck=0,
    )
    if base > 0 and boosted > 0:
        assert boosted >= int(base * underleveled_drop_bonus(1) * 0.85)


def test_coop_clear_grants_stones(session, player, player_two):
    from src.cooperative_dungeons import get_cooperative_dungeon
    from src.dungeon_party import PartyMember, create_party_with_invites, load_members, accept_invite

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two],
    )
    accept_invite(session, party, player_two)
    members = load_members(party)
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    assert dungeon is not None
    player.spirit_stones = 0
    player_two.spirit_stones = 0
    lines = grant_coop_dungeon_clear_stones(session, members, dungeon, random.Random(3))
    assert lines
    assert player.spirit_stones >= 30
    assert player_two.spirit_stones >= 30


def test_solo_dungeon_clear_is_fraction_of_coop(session, player):
    player.spirit_stones = 0
    stones, msg = grant_solo_dungeon_clear_stones(
        session,
        player,
        random.Random(5),
        dungeon_min_realm=1,
    )
    assert stones >= 30
    assert msg is not None
    assert player.spirit_stones == stones
