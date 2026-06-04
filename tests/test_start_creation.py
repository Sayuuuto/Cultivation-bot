"""Integration tests for the /start → path selection → character creation flow."""
from __future__ import annotations

import discord
import pytest
from sqlalchemy import select

from src.bot import bot
from src.content import load_all_content
from src.discord_ui.helpers import _story_finalize_creation
from src.game import SPIRIT_ROOTS, ORIGINS
from src.inventory import load_item_catalog
from src.models import Player
from src.story_mode import PendingStoryCreation, start_pending
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    install_bot_db_patch,
    install_discord_stubs,
    invoke_slash,
    make_mock_interaction,
    run_async,
    view_from_capture,
)


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.fixture
def integration_env(session, monkeypatch):
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    return session


def test_start_smoke_invokes_no_error(integration_env, session):
    """Smoke test: /start must not crash and returns a story view."""
    from sqlalchemy import delete

    session.execute(delete(Player).where(Player.discord_id == str(TEST_USER_ID)))
    session.commit()

    interaction = make_mock_interaction()
    captured = run_async(invoke_slash(bot.tree, "start", interaction))
    view = view_from_capture(captured)
    assert view is not None, "Expected a story view"
    assert "something went wrong" not in captured.text.lower()


def test_story_finalize_creation_creates_player(session, monkeypatch):
    """Directly test _story_finalize_creation creates a player correctly."""
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    monkeypatch.setattr("src.discord_ui.helpers.get_session", lambda: session)

    guild_id = str(TEST_GUILD_ID)
    discord_id = str(TEST_USER_ID)

    pending = start_pending(guild_id, discord_id)
    pending.dao_name = "TestDao"
    pending.gender = "male"
    pending.story_path = "unbroken_blade"
    pending.node_id = "spirit_reveal"

    interaction = make_mock_interaction()

    run_async(_story_finalize_creation(interaction, pending))

    player = session.execute(
        select(Player).where(
            Player.guild_id == guild_id,
            Player.discord_id == discord_id,
        )
    ).scalar_one_or_none()

    assert player is not None, "Player was not created"
    assert player.dao_name == "TestDao"
    assert player.gender == "male"
    assert player.story_path == "unbroken_blade"
    assert player.story_step == "spirit_reveal"
    assert player.spirit_root in SPIRIT_ROOTS
    assert player.origin in ORIGINS
    assert player.moral_path == "neutral"
    assert player.realm_index == 0


def test_story_finalize_creation_existing_player_returns_early(session, monkeypatch):
    """When a player already exists, the function must not create a duplicate."""
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    monkeypatch.setattr("src.discord_ui.helpers.get_session", lambda: session)
    from datetime import datetime, timezone

    guild_id = str(TEST_GUILD_ID)
    discord_id = str(TEST_USER_ID)

    existing = Player(
        guild_id=guild_id,
        discord_id=discord_id,
        discord_username="ExistingDaoist",
        dao_name="ExistingDao",
        origin="Mountain Rises",
        spirit_root="Pure Jade Root",
        moral_path="neutral",
        story_step="complete",
        realm_index=1,
        substage=0,
        qi=50,
        spirit_stones=100,
        last_active_at=datetime.now(timezone.utc),
        passive_accrual_at=datetime.now(timezone.utc),
    )
    session.add(existing)
    session.commit()

    pending = start_pending(guild_id, discord_id)
    pending.dao_name = "TestDao"
    pending.story_path = "unbroken_blade"

    interaction = make_mock_interaction()

    run_async(_story_finalize_creation(interaction, pending))

    players = session.execute(
        select(Player).where(
            Player.guild_id == guild_id,
            Player.discord_id == discord_id,
        )
    ).all()
    assert len(players) == 1  # still only one row, no duplicate


def test_story_finalize_creation_with_each_path(session, monkeypatch):
    """Each cultivator path must produce a valid player."""
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    monkeypatch.setattr("src.discord_ui.helpers.get_session", lambda: session)

    guild_id = str(TEST_GUILD_ID)

    for path_id in ("unbroken_blade", "still_lotus", "wandering_star", "hidden_ledger"):
        discord_id = f"test-path-{path_id}"
        pending = start_pending(guild_id, discord_id)
        pending.dao_name = f"Dao_{path_id}"
        pending.gender = "female"
        pending.story_path = path_id
        pending.node_id = "spirit_reveal"

        interaction = make_mock_interaction()

        run_async(_story_finalize_creation(interaction, pending))

        player = session.execute(
            select(Player).where(
                Player.guild_id == guild_id,
                Player.discord_id == discord_id,
            )
        ).scalar_one_or_none()

        assert player is not None, f"Player not created for path {path_id}"
        assert player.story_path == path_id
        assert player.story_step == "spirit_reveal"
        assert player.origin is not None
        assert player.spirit_root is not None


def test_full_start_flow_to_path_selection(integration_env, session, monkeypatch):
    """Simulate /start → Continue through first nodes until path selection.

    Monkeypatches story delivery so the interaction stays in the same channel
    (avoids the abode-redirect flow which requires real Discord channels).
    """
    from sqlalchemy import delete

    async def _noop_deliver(*args, **kwargs):
        return False

    monkeypatch.setattr(
        "src.discord_ui.helpers.deliver_pending_story_to_abode", _noop_deliver
    )
    monkeypatch.setattr("src.discord_ui.helpers.get_session", lambda: session)

    session.execute(delete(Player).where(Player.discord_id == str(TEST_USER_ID)))
    session.commit()

    from tests.discord_command_harness import click_view_button_label, click_view_button

    interaction = make_mock_interaction()
    captured = run_async(invoke_slash(bot.tree, "start", interaction))
    view = view_from_capture(captured)
    assert view is not None, "Expected a story view after /start"

    step = 0
    max_steps = 20
    while step < max_steps:
        step += 1

        player = session.execute(
            select(Player).where(
                Player.guild_id == str(TEST_GUILD_ID),
                Player.discord_id == str(TEST_USER_ID),
            )
        ).scalar_one_or_none()
        if player is not None:
            assert player.story_step == "spirit_reveal"
            return

        assert view is not None, f"Lost the story view at step {step}"

        labels = [
            getattr(c, "label", None)
            for c in view.children
            if isinstance(c, discord.ui.Button)
        ]
        valid = [l for l in labels if l and l != "Write your own name"]
        if not valid:
            pytest.fail(f"No clickable buttons at step {step}. Labels: {labels}")

        captured = run_async(
            click_view_button_label(view, interaction, label=valid[0])
        )
        view = view_from_capture(captured)

    pytest.fail(f"Did not reach player creation after {max_steps} steps")
