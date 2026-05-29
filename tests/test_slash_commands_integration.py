"""Integration tests: invoke real slash-command callbacks like Discord would."""
from __future__ import annotations

import pytest
from discord import app_commands

from src.bot import NOT_STARTED_HINT, bot
from src.combat.loadout import equip_technique, learn_technique
from src.content import load_all_content
from src.inventory import load_item_catalog
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    assert_discord_view_valid,
    assert_response_ok,
    click_view_button,
    install_bot_db_patch,
    install_discord_stubs,
    invoke_slash,
    iter_slash_commands,
    make_mock_interaction,
    prepare_ready_player,
    run_async,
)
from tests.slash_command_specs import SLASH_COMMAND_SPECS, SPECS_BY_NAME


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


@pytest.fixture
def integration_env(session, monkeypatch):
    install_bot_db_patch(session, monkeypatch)
    install_discord_stubs(monkeypatch)
    return session


@pytest.fixture
def ready_player(session, integration_env):
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
    return row


def test_every_slash_command_has_spec():
    registered = {name for name, _ in iter_slash_commands(bot.tree)}
    documented = {spec.name for spec in SLASH_COMMAND_SPECS}
    missing = registered - documented
    extra = documented - registered
    assert not missing, f"Missing specs for: {sorted(missing)}"
    assert not extra, f"Unknown specs (not in tree): {sorted(extra)}"


@pytest.mark.parametrize("spec", SLASH_COMMAND_SPECS, ids=lambda s: s.name.replace(" ", "_"))
def test_slash_command_smoke(spec, integration_env, ready_player, session):
    if spec.skip:
        pytest.skip(spec.skip_reason)

    interaction = make_mock_interaction(client=bot)
    kwargs = dict(spec.kwargs)

    captured = run_async(invoke_slash(bot.tree, spec.name, interaction, **kwargs))

    if spec.expect_not_started:
        assert "/start" in captured.text.lower() or "already begun" in captured.text.lower()
        return

    assert_response_ok(captured, forbidden_substrings=list(spec.forbidden_in_text))
    if spec.validate_view:
        assert_discord_view_valid(captured.view, context=spec.name)


def test_slash_command_without_player_shows_start_hint(integration_env, session):
    """Commands that need a character must not crash when unregistered."""
    from sqlalchemy import delete

    from src.models import Player

    session.execute(delete(Player).where(Player.discord_id == str(TEST_USER_ID)))
    session.commit()

    interaction = make_mock_interaction(client=bot)
    captured = run_async(invoke_slash(bot.tree, "hunt", interaction, area="bamboo_grove"))
    assert "/start" in captured.text.lower() or NOT_STARTED_HINT in captured.text


def test_hunt_busy_shows_clear_combat_button(integration_env, ready_player, session):
    from src.combat.session import COMBAT_BUSY_MESSAGE, create_active_combat
    from src.combat.engine import create_combat_state, opponent_from_beast
    from src.auto_combat import BeastTemplate
    from src.character import get_character_modifiers
    from src.combat_stats import compute_combat_stats
    from src.bot import AbandonStuckCombatView

    mod = get_character_modifiers(session, ready_player)
    stats = compute_combat_stats(ready_player, session, mod)
    beast = BeastTemplate("test", "Test Beast", 10, 1, 0, ())
    state = create_combat_state(stats, opponent_from_beast(beast), context="hunt")
    create_active_combat(session, ready_player, state, context="hunt", context_key="bamboo_grove")
    session.commit()

    interaction = make_mock_interaction(client=bot)
    captured = run_async(
        invoke_slash(bot.tree, "hunt", interaction, area="bamboo_grove")
    )
    assert COMBAT_BUSY_MESSAGE in (captured.content or "")
    assert captured.view is not None
    assert isinstance(captured.view, AbandonStuckCombatView)
    assert_discord_view_valid(captured.view, context="hunt busy")

    clear_interaction = make_mock_interaction(client=bot)
    clear_cap = run_async(
        click_view_button(captured.view, clear_interaction, custom_id_contains="combat:abandon")
    )
    assert_response_ok(clear_cap)
    assert "cleared" in clear_cap.text.lower() or "free" in clear_cap.text.lower()


def test_hunt_combat_view_unique_custom_ids_with_duplicate_loadout(
    integration_env, ready_player, session
):
    learn_technique(session, ready_player.id, "swift_slash")
    equip_technique(session, ready_player, "swift_slash", "2")
    equip_technique(session, ready_player, "swift_slash", "3")
    session.commit()

    from src.bot import CombatView
    from src.combat.loadout import get_equipped_active_techniques

    techniques = get_equipped_active_techniques(session, ready_player.id)
    assert techniques.count(next(t for t in techniques if t.technique_id == "swift_slash")) == 1

    view = CombatView(
        str(TEST_USER_ID),
        str(TEST_GUILD_ID),
        combat_id=1,
        context="hunt",
        techniques=techniques,
        technique_cooldowns={},
    )
    assert_discord_view_valid(view, context="hunt duplicate loadout")
    assert any(
        "Pass Turn" in (getattr(child, "label", "") or "")
        for child in view.children
    )


def test_hunt_full_workflow_strike_until_finish(integration_env, ready_player, session):
    interaction = make_mock_interaction(client=bot)
    captured = run_async(
        invoke_slash(bot.tree, "hunt", interaction, area="bamboo_grove")
    )
    assert_response_ok(captured)
    assert captured.view is not None
    assert_discord_view_valid(captured.view, context="hunt initial")

    turns = 0
    while turns < 20:
        view = captured.view or (captured.edit or {}).get("view")
        if view is None:
            break
        assert_discord_view_valid(view, context=f"hunt turn {turns}")

        btn_interaction = make_mock_interaction(client=bot)
        try:
            turn_capture = run_async(
                click_view_button(view, btn_interaction, custom_id_contains="basic_strike")
            )
        except AssertionError:
            turn_capture = run_async(
                click_view_button(view, btn_interaction, custom_id_contains=":flee")
            )
            assert_response_ok(turn_capture)
            break

        assert_response_ok(turn_capture)
        captured = turn_capture
        turns += 1
        if captured.edit and captured.edit.get("view") is None:
            break
        if "defeated" in captured.text.lower() or "flees" in captured.text.lower():
            break

    assert turns > 0


def test_dungeon_cancel_clears_ongoing_expedition(integration_env, ready_player, session):
    from src.dungeon_party import create_party_with_invites, find_party_for_player

    party, err = create_party_with_invites(
        session,
        guild_id=ready_player.guild_id,
        leader=ready_player,
        dungeon_id="mortal_catacomb",
        invitees=[],
    )
    assert party is not None
    assert err == ""
    party.status = "in_combat"
    session.add(party)
    session.commit()

    interaction = make_mock_interaction(client=bot)
    captured = run_async(invoke_slash(bot.tree, "dungeon-cancel", interaction))

    assert_response_ok(captured)
    assert "closed" in captured.text.lower()
    assert find_party_for_player(session, ready_player.guild_id, ready_player.discord_id) is None


def test_techniques_command_sends_card_or_embed(integration_env, ready_player, session):
    interaction = make_mock_interaction(client=bot)
    captured = run_async(invoke_slash(bot.tree, "techniques", interaction))
    assert_response_ok(captured)
    assert captured.files or captured.embed or captured.followup_messages


def test_use_command_defers_and_responds(integration_env, ready_player, session):
    interaction = make_mock_interaction(client=bot)
    captured = run_async(
        invoke_slash(bot.tree, "use", interaction, item="qi_gathering_pill")
    )
    assert captured.deferred or captured.embed or captured.content
    assert_response_ok(captured)


def test_adventure_start_and_continue_workflow(integration_env, ready_player, session):
    interaction = make_mock_interaction(client=bot)
    captured = run_async(invoke_slash(bot.tree, "adventure", interaction))
    assert_response_ok(captured)
    if captured.view is not None:
        assert_discord_view_valid(captured.view, context="adventure choices")

    cont = make_mock_interaction(client=bot)
    cont_cap = run_async(invoke_slash(bot.tree, "adventure-continue", cont))
    assert_response_ok(cont_cap)
