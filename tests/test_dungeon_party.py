from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from src.cooperative_dungeons import get_cooperative_dungeon
from src.dungeon_arena import build_dungeon_combat_embed, format_fighter_line
from src.dungeon_combat import select_target, select_technique, start_room_combat
from src.dungeon_party import (
    MAX_INVITES,
    MAX_PARTY_SIZE,
    MIN_PARTY_SIZE,
    PARTY_LOBBY_TIMEOUT_SECONDS,
    accept_invite,
    create_party_with_invites,
    expire_stale_dungeon_parties,
    find_party_for_player,
    load_invites,
    load_members,
    party_is_stale,
    party_ready_to_launch,
)
from src.models import ActiveDungeonParty


def test_cooperative_dungeons_per_realm():
    from src.cooperative_dungeons import get_cooperative_dungeons

    dungeons = get_cooperative_dungeons()
    assert len(dungeons) >= 10
    mortal = get_cooperative_dungeon("mortal_catacomb")
    assert mortal is not None
    assert mortal.realm_index == 0
    assert len(mortal.rooms) == 4


def test_solo_party_ready_immediately(session, player):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    assert MIN_PARTY_SIZE == 1
    assert MAX_PARTY_SIZE == 4
    assert MAX_INVITES == 3
    assert party_ready_to_launch(party)
    assert len(load_members(party)) == 1


def test_party_starts_when_all_accept(session, player, player_two):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two],
    )
    assert len(load_invites(party)) == 1
    assert not party_ready_to_launch(party)

    accept_invite(session, party, player_two)
    assert len(load_invites(party)) == 0
    assert party_ready_to_launch(party)
    assert len(load_members(party)) == 2


def test_party_is_stale_lobby(session, player):
    now = datetime.now(timezone.utc)
    party = ActiveDungeonParty(
        guild_id=player.guild_id,
        leader_discord_id=player.discord_id,
        dungeon_id="mortal_catacomb",
        status="lobby",
        created_at=now - timedelta(seconds=PARTY_LOBBY_TIMEOUT_SECONDS + 1),
        expires_at=now + timedelta(hours=1),
        updated_at=now,
    )
    assert party_is_stale(party, now)


def test_expire_stale_lobby_unlocks_player(session, player):
    now = datetime.now(timezone.utc)
    party = ActiveDungeonParty(
        guild_id=player.guild_id,
        leader_discord_id=player.discord_id,
        dungeon_id="mortal_catacomb",
        status="lobby",
        created_at=now - timedelta(seconds=PARTY_LOBBY_TIMEOUT_SECONDS + 60),
        expires_at=now + timedelta(hours=1),
        updated_at=now - timedelta(seconds=PARTY_LOBBY_TIMEOUT_SECONDS + 60),
    )
    session.add(party)
    session.commit()

    assert find_party_for_player(session, player.guild_id, player.discord_id) is None

    party, err = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    assert party is not None
    assert err == ""


def test_start_room_spawns_enemies_and_players(session, player, player_two):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two],
    )
    accept_invite(session, party, player_two)
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    members = load_members(party)
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=members,
        rng=random.Random(1),
    )
    assert len(state.living_enemies()) >= 1
    assert len(state.living_players()) == 2

    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    line = format_fighter_line(ally)
    assert str(ally.combatant.hp) in line
    embed = build_dungeon_combat_embed(party, state)
    assert embed.title
    party_field = next(f for f in embed.fields if f.name == "Party")
    assert str(ally.combatant.hp) in party_field.value


def test_select_target_resolves_player_strike(session, player):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    members = load_members(party)
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=members,
        rng=random.Random(1),
    )
    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    enemy = next(f for f in state.fighters.values() if f.is_enemy)
    state.turn_order = [ally.fighter_id, enemy.fighter_id]
    state.turn_index = 0

    prep = select_technique(session, state, ally.fighter_id, "basic_strike")
    assert prep.ok
    assert state.pending_technique == "basic_strike"

    result = select_target(session, state, ally.fighter_id, enemy.fighter_id, rng=random.Random(2))
    assert result.ok
    assert state.pending_technique is None
    assert enemy.combatant.hp < enemy.combatant.max_hp
