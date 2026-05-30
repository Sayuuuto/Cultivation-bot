"""
Full /dungeon integration: slash command, dungeon combat buttons, run completion.

Uses pytest_training_chamber (single weak foe) — see dungeon_fight_harness.
"""
from __future__ import annotations

import pytest

from src.bot import bot
from src.cooperative_dungeons import CooperativeDungeonDef, CoopRoomDef
from src.content import load_all_content
from src.dungeon_party import find_party_for_player
from src.inventory import load_item_catalog
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    install_bot_db_patch,
    install_discord_stubs,
    prepare_ready_player,
)
from tests.dungeon_fight_harness import (
    PYTEST_DUNGEON_ID,
    PYTEST_TRAINING_DUNGEON,
    clear_dungeon_probe,
    dungeon_channel_messages,
    install_dungeon_channel_mock,
    install_dungeon_state_probe,
    install_fixed_dungeon_rng,
    install_pytest_dungeon,
    install_weak_dungeon_enemies,
    last_dungeon_combat_view,
    load_dungeon_combat_state,
)
from tests.player_bot_session import PlayerBotSession


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.fixture
def dungeon_fight_env(session, monkeypatch):
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    install_pytest_dungeon(monkeypatch)
    install_weak_dungeon_enemies(monkeypatch)
    install_fixed_dungeon_rng(monkeypatch, seed=77)
    install_dungeon_state_probe(monkeypatch)
    install_dungeon_channel_mock(monkeypatch)
    clear_dungeon_probe()
    return session


@pytest.fixture
def ready_delver(session, dungeon_fight_env):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from src.models import Player

    row = session.execute(
        select(Player).where(
            Player.guild_id == str(TEST_GUILD_ID),
            Player.discord_id == str(TEST_USER_ID),
        )
    ).scalar_one_or_none()
    if row is None:
        now = datetime.now(timezone.utc)
        row = Player(
            guild_id=str(TEST_GUILD_ID),
            discord_id=str(TEST_USER_ID),
            discord_username="TestUser",
            dao_name="TestDao",
            origin="Mountain Rises",
            spirit_root="Pure Jade Root",
            moral_path="neutral",
            novice_trial_step=6,
            adventures_completed=1,
            realm_index=1,
            substage=0,
            qi=80,
            spirit_stones=500,
            last_active_at=now,
            passive_accrual_at=now,
            last_dungeon_at=None,
        )
        session.add(row)
        session.flush()
    prepare_ready_player(session, row)
    from src.combat.loadout import equip_technique, learn_technique

    learn_technique(session, row.id, "qi_barrier")
    equip_technique(session, row, "qi_barrier", "4")
    row.last_dungeon_at = None
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def delver(dungeon_fight_env, ready_delver, monkeypatch) -> PlayerBotSession:
    return PlayerBotSession.open(
        tree=bot.tree,
        client=bot,
        db=dungeon_fight_env,
        player=ready_delver,
        production_ui=False,
        monkeypatch=monkeypatch,
    )


def test_dungeon_opens_training_chamber(delver: PlayerBotSession):
    delver.slash("dungeon", dungeon=PYTEST_DUNGEON_ID)
    party = find_party_for_player(
        delver.db, str(delver.player.guild_id), str(delver.player.discord_id)
    )
    assert party is not None
    assert party.dungeon_id == PYTEST_DUNGEON_ID
    assert party.status == "in_combat"

    messages = dungeon_channel_messages()
    assert len(messages) >= 2, "opening log plus live combat panel"
    assert messages[-1].get("embed") is not None
    assert last_dungeon_combat_view() is not None


def test_dungeon_sliding_panel_after_first_strike(delver: PlayerBotSession):
    """Panel converts to log on action; a fresh panel is always the last embed."""
    delver.slash("dungeon", dungeon=PYTEST_DUNGEON_ID)
    before = len(dungeon_channel_messages())
    view = last_dungeon_combat_view()
    assert view is not None
    delver.fresh_interaction()
    from tests.discord_command_harness import (
        assert_response_ok,
        click_view_button_label_contains,
        run_async,
    )

    delver.last = run_async(
        click_view_button_label_contains(view, delver.interaction, substring="Basic Strike")
    )
    assert_response_ok(delver.last, context="first dungeon strike")
    delver.db.commit()

    messages = dungeon_channel_messages()
    assert len(messages) > before
    panels = [m for m in messages if m.get("embed") is not None]
    assert panels, "combat panel embed should remain in channel"
    assert panels[-1].get("view") is not None or last_dungeon_combat_view() is not None
    assert any(m.get("converted_to_log") for m in messages)


def test_dungeon_qi_barrier_resolves_without_target_buttons(delver: PlayerBotSession):
    """Self-target utility arts should resolve immediately in the real dungeon UI flow."""
    delver.slash("dungeon", dungeon=PYTEST_DUNGEON_ID)
    party = find_party_for_player(
        delver.db, str(delver.player.guild_id), str(delver.player.discord_id)
    )
    assert party is not None
    view = last_dungeon_combat_view()
    assert view is not None
    delver.fresh_interaction()

    from tests.discord_command_harness import (
        assert_response_ok,
        click_view_button_label_contains,
        run_async,
    )

    delver.last = run_async(
        click_view_button_label_contains(view, delver.interaction, substring="Qi Barrier")
    )
    assert_response_ok(delver.last, context="dungeon qi barrier")
    delver.db.commit()

    state = load_dungeon_combat_state(delver.db, party.id)
    assert state is not None
    assert state.pending_technique is None
    assert any("Qi Barrier" in line and "shield" in line.lower() for line in state.log)
    actor = next(f for f in state.fighters.values() if not f.is_enemy)
    assert actor.shield > 0

    latest_view = last_dungeon_combat_view()
    assert latest_view is not None
    labels = [getattr(child, "label", "") or "" for child in latest_view.children]
    assert not any("🎯" in label for label in labels), labels


def test_dungeon_full_fight_completes_training_chamber(delver: PlayerBotSession):
    audit = delver.dungeon_fight_to_end(dungeon_id=PYTEST_DUNGEON_ID, max_turns=35)

    assert audit.turns_played >= 2
    assert audit.run_complete
    assert audit.room_label == PYTEST_TRAINING_DUNGEON.rooms[0].label
    assert audit.player_strike_phases >= 1
    assert audit.log_message_count >= 1
    assert audit.panel_rotations >= 1
    panels = [m for m in dungeon_channel_messages() if m.get("embed") is not None]
    final_embed = panels[-1].get("embed")
    assert final_embed is not None
    assert panels[-1].get("view") is None, "finished card should not keep technique buttons"
    final_fields = getattr(final_embed, "fields", [])
    assert any("Dungeon conquered!" in field.value for field in final_fields)


def test_dungeon_room_clear_advances_to_next_live_panel(delver: PlayerBotSession, monkeypatch):
    two_room_dungeon = CooperativeDungeonDef(
        dungeon_id=PYTEST_DUNGEON_ID,
        name="Training Chamber",
        realm_index=0,
        recommended_party=1,
        rooms=(
            CoopRoomDef(label="Trial Hall", enemies=(("warden", 1),), boss_template=None),
            CoopRoomDef(label="Second Hall", enemies=(("warden", 1),), boss_template=None),
        ),
        guaranteed_drops=(),
        bonus_drops=(),
    )

    def _get(dungeon_id: str):
        if dungeon_id == PYTEST_DUNGEON_ID:
            return two_room_dungeon
        return None

    monkeypatch.setattr("src.cooperative_dungeons.get_cooperative_dungeon", _get)
    monkeypatch.setattr("src.dungeon_party.get_cooperative_dungeon", _get)
    monkeypatch.setattr("src.dungeon_combat.get_cooperative_dungeon", _get)
    monkeypatch.setattr("src.dungeon_discord.get_cooperative_dungeon", _get)

    delver.slash("dungeon", dungeon=PYTEST_DUNGEON_ID)
    party = find_party_for_player(
        delver.db, str(delver.player.guild_id), str(delver.player.discord_id)
    )
    assert party is not None

    from tests.discord_command_harness import (
        assert_response_ok,
        click_view_button_label,
        click_view_button_label_contains,
        run_async,
    )

    state = load_dungeon_combat_state(delver.db, party.id)
    assert state is not None
    view = last_dungeon_combat_view()
    assert view is not None
    for _ in range(6):
        delver.fresh_interaction()
        labels = [getattr(child, "label", "") or "" for child in view.children]
        if any("🎯" in label for label in labels):
            label = next(label for label in labels if "🎯" in label)
            delver.last = run_async(click_view_button_label(view, delver.interaction, label=label))
        else:
            delver.last = run_async(
                click_view_button_label_contains(view, delver.interaction, substring="Basic Strike")
            )
        assert_response_ok(delver.last)
        delver.db.commit()
        state = load_dungeon_combat_state(delver.db, party.id)
        assert state is not None
        view = last_dungeon_combat_view()
        if state.room_index == 1:
            break
        assert view is not None

    assert state.room_index == 1
    assert state.room_label == "Second Hall"
    assert not state.finished
    assert view is not None
    panels = [m for m in dungeon_channel_messages() if m.get("embed") is not None]
    assert panels, "combat panel embed should still be present"
    latest_panel = panels[-1]
    assert latest_panel.get("view") is not None
    embed_text = str(getattr(latest_panel.get("embed"), "title", "")) + str(
        getattr(latest_panel.get("embed"), "description", "")
    )
    assert "Second Hall" in embed_text
