"""
Full /adventure integration: slash command, choice buttons, optional combat segment.
"""
from __future__ import annotations

import pytest

from src.adventure import get_active_adventure
from src.bot import bot
from src.combat.session import get_active_combat
from src.content import load_all_content
from src.inventory import load_item_catalog
from tests.adventure_flow_harness import (
    adventure_choice_success_floats,
    assert_no_forbidden_copy,
    install_forced_adventure_encounters,
    install_scripted_adventure_rng,
    install_weak_adventure_monster,
)
from tests.hunt_fight_harness import install_fixed_hunt_rng
from tests.rng_helpers import ScriptedRNG
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    assert_response_ok,
    click_view_button,
    install_bot_db_patch,
    install_discord_stubs,
    prepare_ready_player,
    run_async,
)
from tests.hunt_fight_harness import clear_combat_state_probe, install_combat_state_probe
from tests.player_bot_session import PlayerBotSession


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.fixture
def adventure_env(session, monkeypatch):
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    return session


@pytest.fixture
def ready_traveler(session, adventure_env):
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
            adventures_completed=2,
            realm_index=1,
            substage=0,
            qi=80,
            spirit_stones=500,
            last_active_at=now,
            passive_accrual_at=now,
            last_adventure_at=None,
        )
        session.add(row)
        session.flush()
    prepare_ready_player(session, row)
    row.realm_index = 0
    row.adventures_completed = max(row.adventures_completed, 2)
    row.last_adventure_at = None
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def traveler(ready_traveler, adventure_env, monkeypatch) -> PlayerBotSession:
    return PlayerBotSession.open(
        tree=bot.tree,
        client=bot,
        db=adventure_env,
        player=ready_traveler,
        production_ui=False,
        monkeypatch=monkeypatch,
    )


def test_adventure_choice_run_completes(traveler: PlayerBotSession, monkeypatch):
    install_scripted_adventure_rng(
        monkeypatch,
        encounter_ids=("injured_elder", "injured_elder", "injured_elder"),
    )

    audit = traveler.adventure_run_to_end(area="bamboo_grove", stance="balanced")

    assert audit.completed
    assert audit.choice_steps >= 3
    assert get_active_adventure(traveler.db, traveler.player.id) is None
    assert_no_forbidden_copy(audit.final_text, context="adventure finish")


def test_adventure_combat_segment_completes(traveler: PlayerBotSession, monkeypatch):
    install_forced_adventure_encounters(
        monkeypatch,
        area_id="bamboo_grove",
        encounter_ids=("injured_elder", "bamboo_specter_fight", "injured_elder"),
    )
    floats: list[float] = []
    floats.extend(adventure_choice_success_floats() * 3)
    rng = ScriptedRNG(floats=floats, randint_queue=[3])

    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        _ = guild_id, user_id, salt
        return rng

    monkeypatch.setattr("src.bot.rng_for", _rng_for)
    install_weak_adventure_monster(monkeypatch)
    install_fixed_hunt_rng(monkeypatch, seed=91)
    install_combat_state_probe(monkeypatch)
    clear_combat_state_probe()

    audit = traveler.adventure_run_to_end(area="bamboo_grove", stance="balanced", max_steps=30)

    assert audit.completed
    assert audit.choice_steps >= 1
    assert audit.combat_turns >= 2
    assert get_active_adventure(traveler.db, traveler.player.id) is None


def test_adventure_route_choice_shapes_followup_and_summary(
    traveler: PlayerBotSession, monkeypatch
):
    floats: list[float] = []
    floats.extend(adventure_choice_success_floats() * 4)
    rng = ScriptedRNG(floats=floats, randint_queue=[4])

    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        _ = guild_id, user_id, salt
        return rng

    monkeypatch.setattr("src.bot.rng_for", _rng_for)

    traveler.slash("adventure")
    assert traveler.last is not None
    initial_text = traveler.last.text.lower()
    if traveler.last.embed is not None:
        initial_text += str(traveler.last.embed.to_dict()).lower()
    assert "paved road" in initial_text

    view = traveler.require_view()
    traveler.fresh_interaction()

    traveler.last = run_async(click_view_button(view, traveler.interaction, custom_id_contains="adv:"))
    traveler.db.commit()
    assert traveler.last is not None
    followup_text = traveler.last.text.lower()
    if traveler.last.embed is not None:
        followup_text += str(traveler.last.embed.to_dict()).lower()
    assert "merchant village road" in followup_text
    assert "lantern toll" in followup_text or "merchant" in followup_text

    for step in range(10):
        if get_active_adventure(traveler.db, traveler.player.id) is None:
            break
        view = traveler.require_view()
        traveler.fresh_interaction()
        traveler.last = run_async(click_view_button(view, traveler.interaction, custom_id_contains="adv:"))
        assert_response_ok(traveler.last, context=f"route adventure step {step}")
        traveler.db.commit()

    assert traveler.last is not None
    final_text = traveler.last.text.lower()
    if traveler.last.embed is not None:
        final_text += str(traveler.last.embed.to_dict()).lower()
    assert "merchant village road" in final_text
    assert get_active_adventure(traveler.db, traveler.player.id) is None


def test_deep_forest_raid_opens_guardian_combat(
    traveler: PlayerBotSession, monkeypatch
):
    rng = ScriptedRNG(
        floats=adventure_choice_success_floats(),
        randint_queue=[4],
    )

    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        _ = guild_id, user_id, salt
        return rng

    monkeypatch.setattr("src.bot.rng_for", _rng_for)
    install_weak_adventure_monster(monkeypatch)
    install_combat_state_probe(monkeypatch)
    clear_combat_state_probe()

    traveler.slash("adventure")
    assert traveler.last is not None

    view = traveler.require_view()
    traveler.fresh_interaction()
    traveler.last = run_async(
        click_view_button(view, traveler.interaction, custom_id_contains="deep_forest")
    )
    assert_response_ok(traveler.last, context="deep forest route choice")
    traveler.db.commit()

    view = traveler.require_view()
    traveler.fresh_interaction()
    traveler.last = run_async(
        click_view_button(view, traveler.interaction, custom_id_contains="raid_nest")
    )
    assert_response_ok(traveler.last, context="deep forest raid choice")
    traveler.db.commit()

    assert get_active_combat(traveler.db, traveler.player.id) is not None
    assert traveler.last is not None
    combat_text = traveler.last.text.lower()
    if traveler.last.embed is not None:
        combat_text += str(traveler.last.embed.to_dict()).lower()
    assert "bamboo specter" in combat_text
