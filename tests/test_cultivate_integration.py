"""
Full /cultivate integration: slash command, qi gain, cooldown enforcement.
"""
from __future__ import annotations

import pytest

from src.bot import bot
from src.content import load_all_content
from src.inventory import load_item_catalog
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    install_bot_db_patch,
    install_discord_stubs,
    prepare_ready_player,
)
from tests.player_bot_session import PlayerBotSession


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.fixture
def cultivate_env(session, monkeypatch):
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    return session


@pytest.fixture
def ready_cultivator(session, cultivate_env):
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
            qi=10,
            spirit_stones=100,
            last_active_at=now,
            passive_accrual_at=now,
            last_cultivate_at=None,
        )
        session.add(row)
        session.flush()
    prepare_ready_player(session, row)
    row.last_cultivate_at = None
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def cultivator(ready_cultivator, cultivate_env, monkeypatch) -> PlayerBotSession:
    return PlayerBotSession.open(
        tree=bot.tree,
        client=bot,
        db=cultivate_env,
        player=ready_cultivator,
        production_ui=False,
        monkeypatch=monkeypatch,
    )


def test_cultivate_grants_qi_and_sets_cooldown(cultivator: PlayerBotSession):
    qi_before = cultivator.player.qi
    cultivator.cultivate_once()
    cultivator.reload_player()
    assert cultivator.player.qi > qi_before
    assert cultivator.player.last_cultivate_at is not None
    cultivator.cultivate_expect_cooldown()


def test_cultivate_embed_has_no_error_copy(cultivator: PlayerBotSession):
    from tests.adventure_flow_harness import assert_no_forbidden_copy

    cultivator.slash("cultivate")
    assert cultivator.last is not None
    assert_no_forbidden_copy(cultivator.last.text, context="/cultivate")
    assert cultivator.last.embed is not None
