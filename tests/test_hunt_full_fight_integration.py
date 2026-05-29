"""
Full /hunt integration: slash command, button combat, monster fights back, log invariants.

Uses a fixed Training Dummy beast (see hunt_fight_harness.PYTEST_TRAINING_BEAST).
"""
from __future__ import annotations

import pytest

from src.bot import bot
from src.combat.loadout import get_equipped_passive, get_loadout, unequip_slot
from src.combat.session import get_active_combat
from src.content import load_all_content
from src.inventory import load_item_catalog
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    install_bot_db_patch,
    install_discord_stubs,
    prepare_ready_player,
)
from tests.hunt_fight_harness import (
    PYTEST_TRAINING_BEAST,
    clear_combat_state_probe,
    install_combat_state_probe,
    install_fixed_hunt_rng,
    install_pytest_hunt_beast,
    load_hunt_combat_state,
)
from tests.player_bot_session import PlayerBotSession


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.fixture
def hunt_fight_env(session, monkeypatch):
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    install_pytest_hunt_beast(monkeypatch)
    install_fixed_hunt_rng(monkeypatch, seed=42)
    install_combat_state_probe(monkeypatch)
    clear_combat_state_probe()
    return session


@pytest.fixture
def ready_hunter(session, hunt_fight_env):
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
            realm_index=0,
            substage=0,
            qi=0,
            spirit_stones=0,
            last_active_at=now,
            passive_accrual_at=now,
        )
        session.add(row)
        session.flush()
    prepare_ready_player(session, row)
    passive = get_equipped_passive(session, row.id)
    if passive is not None:
        unequip_slot(session, row.id, "passive")
    session.commit()
    return row


@pytest.fixture
def hunter(hunt_fight_env, ready_hunter, monkeypatch) -> PlayerBotSession:
    return PlayerBotSession.open(
        tree=bot.tree,
        client=bot,
        db=hunt_fight_env,
        player=ready_hunter,
        production_ui=False,
        monkeypatch=monkeypatch,
    )


def test_hunt_full_fight_vs_training_dummy(hunter: PlayerBotSession):
    audit = hunter.hunt_fight_to_end(technique_id="basic_strike", max_turns=25)

    assert audit.turns_played >= 3
    assert audit.final_opponent_hp <= 0 or audit.victory
    assert get_active_combat(hunter.db, hunter.player.id) is None


def test_hunt_opens_training_dummy_by_name(hunter: PlayerBotSession):
    hunter.slash("hunt", area="bamboo_grove")
    state = load_hunt_combat_state(hunter.db, hunter.player.id)
    assert state is not None
    assert state.opponent_name == PYTEST_TRAINING_BEAST.name
    assert state.opponent.max_hp == PYTEST_TRAINING_BEAST.hp


def test_hunt_no_passive_no_phantom_shield(hunter: PlayerBotSession, ready_hunter, session):
    """Regression: shield absorb lines require an explicit shield grant or shield pool."""
    assert get_equipped_passive(session, ready_hunter.id) is None
    loadout = get_loadout(session, ready_hunter.id)
    assert "passive" not in loadout or loadout.get("passive") in (None, "")

    audit = hunter.hunt_fight_to_end(max_turns=25)
    assert not audit.shield_grant_seen
    assert audit.max_player_shield_seen == 0
