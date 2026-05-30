from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from src.cooperative_dungeons import get_cooperative_dungeon
from src.dungeon_arena import build_dungeon_combat_embed, format_fighter_line
from src.combat.loadout import ensure_starter_techniques, learn_technique
from src.combat.targeting import technique_hits_all_enemies
from src.combat.catalog import get_technique
from src.dungeon_combat import (
    advance_to_next_room,
    select_target,
    select_technique,
    should_advance_room,
    start_room_combat,
)
from src.dungeon_party import (
    MAX_INVITES,
    MAX_PARTY_SIZE,
    MIN_PARTY_SIZE,
    PARTY_LOBBY_TIMEOUT_SECONDS,
    accept_invite,
    cancel_party_for_player,
    create_party_with_invites,
    expire_stale_dungeon_parties,
    find_party_for_player,
    load_invites,
    load_members,
    party_is_stale,
    party_ready_to_launch,
)
from src.models import ActiveDungeonParty, Player


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


def test_party_not_ready_until_all_invitees_accept(session, player, player_two):
    now = datetime.now(timezone.utc)
    player_three = Player(
        guild_id=player.guild_id,
        discord_id="third-user",
        discord_username="ThirdUser",
        dao_name="ThirdDao",
        origin="River Dragon's Gift",
        spirit_root="Scarlet Flame Root",
        moral_path="righteous",
        novice_trial_step=6,
        adventures_completed=1,
        realm_index=0,
        substage=0,
        qi=0,
        spirit_stones=0,
        last_active_at=now,
        passive_accrual_at=now,
    )
    session.add(player_three)
    session.commit()

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two, player_three],
    )
    assert len(load_invites(party)) == 2
    assert not party_ready_to_launch(party)

    accept_invite(session, party, player_two)
    assert len(load_invites(party)) == 1
    assert not party_ready_to_launch(party)

    accept_invite(session, party, player_three)
    assert len(load_invites(party)) == 0
    assert party_ready_to_launch(party)
    assert len(load_members(party)) == 3


def test_accept_invite_preserves_abode_message_refs(session, player, player_two):
    from src.dungeon_party import attach_invite_abode_message, load_members

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two],
    )
    attach_invite_abode_message(
        party,
        player_two.discord_id,
        abode_channel_id="9001",
        abode_message_id="9002",
    )
    accept_invite(session, party, player_two)
    ally = next(m for m in load_members(party) if m.discord_id == player_two.discord_id)
    assert ally.abode_channel_id == "9001"
    assert ally.abode_message_id == "9002"


def test_iter_invite_message_refs_pending_and_accepted(session, player, player_two):
    from src.dungeon_party import attach_invite_abode_message, iter_invite_message_refs

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[player_two],
    )
    attach_invite_abode_message(
        party,
        player_two.discord_id,
        abode_channel_id="100",
        abode_message_id="101",
    )
    pending = iter_invite_message_refs(party)
    assert len(pending) == 1
    assert pending[0][3] is True

    accept_invite(session, party, player_two)
    accepted = iter_invite_message_refs(party)
    assert len(accepted) == 1
    assert accepted[0][3] is False


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


def test_cancel_party_for_player_clears_active_combat(session, player):
    party, err = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    assert party is not None
    assert err == ""
    party.status = "in_combat"
    session.add(party)
    session.commit()

    ok, message = cancel_party_for_player(session, player.guild_id, player.discord_id)
    session.commit()

    assert ok
    assert "closed" in message.lower()
    assert party.status == "cancelled"
    assert find_party_for_player(session, player.guild_id, player.discord_id) is None


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
    embed = build_dungeon_combat_embed(state)
    assert embed.title
    party_field = next(f for f in embed.fields if f.name == "Party")
    assert str(ally.combatant.hp) in party_field.value


def test_combat_panel_shows_targets_when_technique_pending(session, player):
    from src.dungeon_discord import _build_combat_panel_view

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=load_members(party),
        rng=random.Random(1),
    )
    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    state.turn_order = [ally.fighter_id]
    state.turn_index = 0
    select_technique(session, state, ally.fighter_id, "basic_strike")

    view = _build_combat_panel_view(party.id, state)
    assert view is not None
    labels = [getattr(c, "label", "") for c in view.children]
    assert any("🎯" in label for label in labels)
    assert any("Cancel" in label for label in labels)


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


def test_should_advance_room_after_clearing_enemies(session, player):
    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=load_members(party),
        rng=random.Random(1),
    )
    for foe in state.living_enemies():
        foe.combatant.hp = 0
    state.room_cleared = True
    state.finished = True
    state.victory = True
    assert should_advance_room(state)


def test_advance_to_next_room_resets_combat_and_keeps_log(session, player):
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
        rng=random.Random(3),
    )
    state.log.append("**Entry Hall** is cleared!")
    state.log_cursor = len(state.log)
    state.room_cleared = True
    state.finished = True
    state.victory = True
    new_state = advance_to_next_room(session, state, members, random.Random(4))
    assert new_state.room_index == 1
    assert not new_state.finished
    assert new_state.living_enemies()
    assert "Advancing to" in "\n".join(new_state.log)
    assert "Entry Hall" in "\n".join(new_state.log)


def test_flame_burst_clearing_room_sets_advance_flag(session, player):
    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "flame_burst")
    session.commit()
    tech = get_technique("flame_burst")
    assert tech is not None
    assert technique_hits_all_enemies(tech)

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=load_members(party),
        rng=random.Random(10),
    )
    ally = next(f for f in state.fighters.values() if not f.is_enemy)
    for foe in state.living_enemies():
        foe.combatant.hp = 1
    state.turn_order = [ally.fighter_id]
    state.turn_index = 0

    res = select_technique(session, state, ally.fighter_id, "flame_burst", rng=random.Random(11))
    assert res.ok
    assert not res.needs_target
    assert state.finished
    assert state.victory
    assert state.room_cleared
    assert should_advance_room(state)


def test_format_new_log_lines_tracks_cursor(session, player):
    from src.dungeon_arena import format_new_log_lines

    party, _ = create_party_with_invites(
        session,
        guild_id=player.guild_id,
        leader=player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    dungeon = get_cooperative_dungeon("mortal_catacomb")
    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=load_members(party),
        rng=random.Random(1),
    )
    state.log_cursor = 0
    chunk = format_new_log_lines(state)
    assert chunk
    state.log_cursor = len(state.log)
    assert format_new_log_lines(state) is None
