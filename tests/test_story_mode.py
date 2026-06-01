from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models import Base, Player
from src.story_mode import (
    apply_path_bonuses,
    get_node,
    get_path_def,
    node_choices,
    on_story_command_completed,
    resolve_next_node,
    story_allows_sect_join,
    story_command_available,
    unlock_chapter_two,
    validate_dao_name,
    chapter_start_node,
)
from src.inventory import get_player_inventory


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()


def _player(**kwargs) -> Player:
    defaults = {
        "guild_id": "1",
        "discord_id": "99",
        "dao_name": "Test Dao",
        "story_path": "unbroken_blade",
        "story_chapter": 1,
        "story_step": "wait_daily",
        "novice_trial_step": 0,
        "realm_index": 0,
    }
    defaults.update(kwargs)
    return Player(**defaults)


def test_validate_dao_name():
    assert validate_dao_name("A") is not None
    assert validate_dao_name("Valid Name") is None
    assert validate_dao_name("x" * 33) is not None


def test_chapter1_nodes_load():
    assert get_node(1, "awakening") is not None
    assert get_node(1, "wait_learn") is not None
    assert get_node(1, "wait_equip") is not None
    assert get_node(1, "name_confirmed") is not None
    assert get_node(2, "chapter2_opening") is not None


def test_pick_name_suggestions():
    from src.story_mode import pick_name_suggestions

    a = pick_name_suggestions("12345", count=3)
    b = pick_name_suggestions("12345", count=3)
    assert a == b
    assert len(a) == 3
    assert len(set(a)) == 3


def test_chapter1_flavor_branches_converge():
    awakening = get_node(1, "awakening")
    assert awakening is not None
    choices = node_choices(awakening)
    assert len(choices) == 3
    for choice in choices:
        branch = get_node(1, choice["next"])
        assert branch is not None
        nxt = resolve_next_node(branch)
        assert nxt == "cultivator_asks_name"


def test_on_story_command_completed_advances_daily(session: Session):
    player = _player(story_step="wait_daily", novice_trial_step=0)
    session.add(player)
    session.flush()
    msgs = on_story_command_completed(player, "daily")
    assert player.story_step == "after_daily"
    assert msgs == []


def test_story_command_chapter1_at_mortal():
    player = _player(realm_index=0, story_chapter=1, story_step="wait_cultivate")
    ok, _ = story_command_available(player)
    assert ok is True


def test_story_command_locked_before_qi_refining_chapter2():
    player = _player(realm_index=0, story_chapter=2, story_step="chapter2_opening")
    ok, msg = story_command_available(player)
    assert ok is False
    assert "Qi Refining" in msg


def test_unlock_chapter_two():
    player = _player(realm_index=1, story_chapter=1, story_step="chapter1_complete")
    msgs = unlock_chapter_two(player)
    assert player.story_chapter == 2
    assert player.story_step == chapter_start_node(2)
    assert msgs


def test_sect_join_blocked_until_sect_unlock():
    player = _player(story_chapter=2, story_step="chapter2_opening", realm_index=1)
    allowed, msg = story_allows_sect_join(player)
    assert allowed is False
    assert "/story" in msg


def test_sect_join_allowed_at_sect_unlock():
    player = _player(story_chapter=2, story_step="sect_unlock", realm_index=1)
    allowed, _ = story_allows_sect_join(player)
    assert allowed is True


def test_sect_join_allowed_when_complete():
    player = _player(story_chapter=2, story_step="complete", realm_index=1)
    allowed, _ = story_allows_sect_join(player)
    assert allowed is True


def test_apply_path_bonuses(session: Session):
    player = _player(story_path="hidden_ledger")
    session.add(player)
    session.flush()
    msgs = apply_path_bonuses(session, player)
    path = get_path_def("hidden_ledger")
    assert path is not None
    assert player.spirit_stones >= int(path["spirit_stones"])
    inv = get_player_inventory(session, player.id)
    frag_qty = sum(i.quantity for i in inv if i.item_id == "technique_fragment")
    assert frag_qty >= 1
    assert msgs


def test_elder_trial_blocks_wrong_commands():
    from src.story_mode import check_elder_trial_command, elder_trial_active, should_show_command_guidance

    player = _player(story_step="wait_daily", novice_trial_step=0)
    assert elder_trial_active(player)
    assert check_elder_trial_command(player, "daily") is None
    assert check_elder_trial_command(player, "story") is None
    assert check_elder_trial_command(player, "hunt") is not None
    assert not should_show_command_guidance(player)


def test_elder_trial_choice_node_only_story():
    from src.story_mode import check_elder_trial_command

    player = _player(story_step="trial_intro", novice_trial_step=0)
    assert check_elder_trial_command(player, "daily") is not None
    assert check_elder_trial_command(player, "story") is None


def test_elder_trial_allows_profile_on_cultivate_step():
    from src.story_mode import check_elder_trial_command

    player = _player(story_step="wait_cultivate", novice_trial_step=1)
    assert check_elder_trial_command(player, "profile") is None
    assert check_elder_trial_command(player, "cultivate") is None
    assert check_elder_trial_command(player, "daily") is not None


def test_elder_trial_complete_unlocks_commands():
    from src.story_mode import check_elder_trial_command, should_show_command_guidance

    player = _player(novice_trial_step=7)
    assert check_elder_trial_command(player, "hunt") is None
    assert should_show_command_guidance(player)


def test_creation_redirect_skips_abode_panel():
    from src.story_delivery import should_redirect_start_to_public_channel

    assert should_redirect_start_to_public_channel(
        interaction_channel_id="111",
        abode_channel_id="222",
    )
    assert not should_redirect_start_to_public_channel(
        interaction_channel_id="222",
        abode_channel_id="222",
    )


def test_trial_gate_uses_explicit_story_step_not_trial_fallback():
    from src.story_mode import check_elder_trial_command, current_awaited_command

    player = _player(novice_trial_step=5, story_step="")
    assert current_awaited_command(player) is None
    assert check_elder_trial_command(player, "adventure") is not None

    player.story_step = "wait_adventure"
    assert current_awaited_command(player) == "adventure"
    assert check_elder_trial_command(player, "adventure") is None


def test_premature_adventure_does_not_apply_cooldown():
    from src.story_mode import should_apply_trial_activity_cooldown

    player = _player(novice_trial_step=5, story_step="after_equip")
    assert should_apply_trial_activity_cooldown(player, "adventure") is False
    player.story_step = "wait_adventure"
    player.novice_trial_step = 6
    assert should_apply_trial_activity_cooldown(player, "adventure") is True
