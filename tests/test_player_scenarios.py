"""
End-to-end player scenarios: slash commands + button/select flows like live Discord.

These are not unit tests of helpers — they walk the same code paths the bot runs
when a cultivator uses menus in their abode channel.
"""
from __future__ import annotations

import pytest

from src.bot import bot
from src.combat.loadout import get_loadout
from src.combat.technique_ui import EquipSlotPickView, EquipSkillView, TechniquesHubView
from src.content import load_all_content
from src.inventory import add_item, load_item_catalog
from tests.discord_command_harness import (
    TEST_GUILD_ID,
    TEST_USER_ID,
    hub_view_from_captured,
    install_bot_db_patch,
    install_discord_stubs,
    invoke_slash,
    prepare_ready_player,
    run_async,
    validate_discord_message_kwargs,
    view_from_capture,
)
from tests.player_bot_session import PlayerBotSession


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
def production_ui(monkeypatch):
    """PNG combat skill card on /techniques — matches deployed bot defaults."""
    from tests.discord_command_harness import enable_production_card_ui

    enable_production_card_ui(monkeypatch)


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


@pytest.fixture
def player(integration_env, ready_player, production_ui, monkeypatch) -> PlayerBotSession:
    return PlayerBotSession.open(
        tree=bot.tree,
        client=bot,
        db=integration_env,
        player=ready_player,
        production_ui=True,
        monkeypatch=monkeypatch,
    )


def test_discord_api_rejects_invalid_message_kwargs():
    with pytest.raises(TypeError, match="Cannot mix embed and embeds"):
        validate_discord_message_kwargs(embed=None, embeds=[])


def test_new_player_must_start_before_hunt(integration_env, session):
    from sqlalchemy import delete

    from src.models import Player
    from tests.discord_command_harness import make_mock_interaction

    session.execute(delete(Player).where(Player.discord_id == str(TEST_USER_ID)))
    session.commit()

    interaction = make_mock_interaction(client=bot)
    cap = run_async(invoke_slash(bot.tree, "hunt", interaction, area="bamboo_grove"))
    assert "/start" in cap.text.lower() or "cultivation path" in cap.text.lower()


def test_player_daily_activity_chain(player: PlayerBotSession):
    """Typical session: check profile, cultivate, gather herbs, open bag."""
    (
        player.slash("profile")
        .slash("cultivate")
        .slash("gather", area="bamboo_grove")
        .slash("inventory")
        .slash("cooldown")
    )


def test_player_daily_streak_increments_after_next_claim(player: PlayerBotSession):
    from datetime import timedelta

    from src.game import utcnow

    player.slash("daily")
    player.reload_player()
    assert player.player.daily_streak == 1

    previous = utcnow() - timedelta(hours=25)
    player.player.last_daily_at = previous
    player.player.last_daily_streak_claimed_at = previous
    player.db.add(player.player)
    player.db.commit()

    player.slash("daily")
    player.reload_player()
    assert player.player.daily_streak == 2


def test_player_uses_consumable_from_bag(player: PlayerBotSession):
    before_qi = player.player.qi
    player.slash("use", item="qi_gathering_pill")
    player.db.expire(player.player)
    player.db.refresh(player.player)
    assert player.player.qi >= before_qi


def test_player_hunt_combat_session(player: PlayerBotSession):
    player.slash("hunt", area="bamboo_grove")
    player.hunt_until_settled(max_turns=15)


def test_player_hunt_then_techniques_still_works(player: PlayerBotSession):
    """After fighting, opening /techniques must not break (PNG hub + sub-menus)."""
    (
        player.slash("hunt", area="bamboo_grove")
        .hunt_until_settled(max_turns=8)
        .open_techniques_hub()
        .assert_techniques_png_hub()
        .click("Skill Library")
        .back_to_combat_skills()
    )
    assert isinstance(player.require_view(), TechniquesHubView)


def test_player_techniques_hub_full_menu_tour(player: PlayerBotSession, integration_env):
    """
    One continuous visit to /techniques: every hub button, back to hub each time.
    Mirrors a player exploring the combat skills panel after opening the PNG card.
    """
    session = integration_env
    add_item(session, player.player.id, "manual_swift_slash", 1)
    session.commit()

    player.open_techniques_hub().assert_techniques_png_hub()

    # Skill Library (multi-embed edit from PNG message)
    player.click("Skill Library")
    assert player.last and player.last.edit and player.last.edit.get("embeds")
    player.back_to_combat_skills()
    assert isinstance(player.require_view(), TechniquesHubView)

    # Equip flow: pick art → pick slot → lands on hub
    player.click("Equip Skill")
    assert isinstance(player.require_view(), EquipSkillView)
    player.select("ember_palm")
    assert isinstance(player.require_view(), EquipSlotPickView)
    player.select("2")
    assert isinstance(player.require_view(), TechniquesHubView)
    session.expire_all()
    assert get_loadout(session, player.player.id).get("2") == "ember_palm"

    # Manage slots: clear slot 1
    player.click("Manage Slots").select("1")
    assert isinstance(player.require_view(), TechniquesHubView)
    session.expire_all()
    assert get_loadout(session, player.player.id).get("1") is None

    # Unlock manual
    player.click("Unlock Skill").select("manual_swift_slash")
    assert isinstance(player.require_view(), TechniquesHubView)
    assert player.last and player.last.followup_messages

    # Upgrade screen (may toast failure if short on mats — screen must open)
    player.click("Upgrade")
    assert player.last and player.last.edit
    player.back_to_combat_skills()
    assert isinstance(player.require_view(), TechniquesHubView)


def test_player_unlock_equip_and_verify_loadout(player: PlayerBotSession, integration_env):
    session = integration_env
    add_item(session, player.player.id, "manual_swift_slash", 1)
    session.commit()

    (
        player.open_techniques_hub()
        .click("Unlock Skill")
        .select("manual_swift_slash")
        .click("Equip Skill")
        .select("swift_slash")
        .select("3")
    )
    session.expire_all()
    assert get_loadout(session, player.player.id).get("3") == "swift_slash"


def test_player_adventure_start_and_continue(player: PlayerBotSession):
    player.slash("adventure")
    if player.last and view_from_capture(player.last):
        player.slash("adventure-continue")


def test_player_techniques_equip_back_without_committing(player: PlayerBotSession):
    """Browse equip menus and return — no DB change, hub must restore."""
    (
        player.open_techniques_hub()
        .assert_techniques_png_hub()
        .click("Equip Skill")
        .back_to_combat_skills()
    )
    assert player.last and player.last.edit.get("embed") is not None
    assert player.last.edit.get("file") is None
    assert isinstance(player.require_view(), TechniquesHubView)
