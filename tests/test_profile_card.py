from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PIL")

from src.combat_stats import compute_combat_stats
from src.content import load_all_content
from src.inventory import load_item_catalog
from src.models import PlayerTechnique, TechniqueLoadout
from src.ui.profile_card import (
    build_profile_card_data,
    format_compact_number,
    format_spirit_stones,
    plain_card_text,
    realm_banner,
    render_profile_card,
)


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()


def test_format_compact_number():
    assert format_compact_number(1_300) == "1.3K"
    assert format_compact_number(7_410_000).startswith("7.41")
    assert format_compact_number(52) == "52"


def test_format_spirit_stones_readable():
    assert format_spirit_stones(1200) == "1,200"
    assert format_spirit_stones(50) == "50"


def test_realm_banner_roman():
    assert "MORTAL" in realm_banner(0, 0)
    assert realm_banner(0, 2).endswith("III")


def test_profile_card_uses_game_combat_stats(session, player, cfg):
    now = datetime.now(timezone.utc)
    from src.character import get_character_modifiers

    mod = get_character_modifiers(session, player)
    combat = compute_combat_stats(player, session, mod)
    data = build_profile_card_data(
        session,
        player,
        combat,
        cfg,
        now,
        guild_label="Test Guild",
        display_name="Tester",
    )
    assert not hasattr(data, "combat_stats")
    assert not hasattr(data, "cultivation_bonuses")
    assert data.next_action_line.startswith("▶")
    assert "Cultivate:" in data.activity_line
    assert any("Rank" in line for line in data.martial_lines)
    assert any("[Strike" in line or "[Defense" in line or "[Control" in line for line in data.martial_lines)
    assert data.spirit_stones_display == format_spirit_stones(player.spirit_stones)
    assert len(data.equipment_slots) == 4


def test_profile_card_cleans_empty_passive_and_warns_skew(session, player, cfg):
    now = datetime.now(timezone.utc)
    from src.character import get_character_modifiers

    for technique_id in ("soul_needle", "iron_body", "mountain_guard", "qi_barrier"):
        session.add(PlayerTechnique(player_id=player.id, technique_id=technique_id, rank=1))
    for slot, technique_id in {
        "1": "soul_needle",
        "2": "iron_body",
        "3": "mountain_guard",
        "4": "qi_barrier",
    }.items():
        session.add(TechniqueLoadout(player_id=player.id, slot=slot, technique_id=technique_id))
    session.commit()

    mod = get_character_modifiers(session, player)
    combat = compute_combat_stats(player, session, mod)
    data = build_profile_card_data(
        session, player, combat, cfg, now, guild_label="Test Guild", display_name="Tester"
    )

    assert data.martial_lines[0] == "Passive — none"
    assert data.martial_hint is not None
    assert "3 Defense" in data.martial_hint


def test_profile_card_breakthrough_ready_uses_banner_path(session, player, cfg):
    now = datetime.now(timezone.utc)
    from src.character import get_character_modifiers
    from src.game import qi_cap

    player.qi = qi_cap(player.realm_index, player.substage, player)
    player.novice_trial_step = 6
    session.add(player)
    session.commit()

    mod = get_character_modifiers(session, player)
    combat = compute_combat_stats(player, session, mod)
    data = build_profile_card_data(
        session, player, combat, cfg, now, guild_label="Test Guild", display_name="Tester"
    )

    assert data.breakthrough_ready
    assert data.trial_complete
    assert data.next_action_line.startswith("▶ Breakthrough")
    png = render_profile_card(data, avatar=None)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_with_multiline_trial_text(session, player, cfg):
    now = datetime.now(timezone.utc)
    from src.character import get_character_modifiers

    mod = get_character_modifiers(session, player)
    combat = compute_combat_stats(player, session, mod)
    data = build_profile_card_data(
        session, player, combat, cfg, now, guild_label="Test", display_name="Tester",
    )
    data.trial_line = plain_card_text("**Trial** — step **5/6**\n▸ Complete adventure")
    png = render_profile_card(data, avatar=None)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_profile_card_png(session, player, cfg):
    now = datetime.now(timezone.utc)
    from src.character import get_character_modifiers

    mod = get_character_modifiers(session, player)
    combat = compute_combat_stats(player, session, mod)
    data = build_profile_card_data(
        session,
        player,
        combat,
        cfg,
        now,
        guild_label="Test Sect Realm",
        display_name="Tester",
    )
    png = render_profile_card(data, avatar=None)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 8_000
