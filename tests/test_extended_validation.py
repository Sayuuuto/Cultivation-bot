from __future__ import annotations

import random
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from src.auto_combat import BeastTemplate, resolve_auto_combat
from src.combat_stats import PlayerCombatStats, compute_combat_stats
from src.config import Config
from src.content import CANONICAL_REALM_AREAS, get_areas, load_all_content, resolve_area_id
from src.cooldown_haste import consume_haste_for_activity, get_haste_reduction_seconds
from src.drop_sources import get_drop_sources
from src.gather import get_gather_areas, run_gather
from src.guidance import build_cooldown_embed, build_cooldown_lines, get_help_sections
from src.hunt import get_hunt_areas, run_hunt
from src.inventory import load_item_catalog
from src.models import Player, PlayerEffect
from src.reminders import compute_ready_at, schedule_after_activity, set_reminder_enabled


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()
    # Rebuild drop-source index after content modules load.
    import src.drop_sources as drop_sources

    drop_sources._item_sources = None


def test_config_areas_and_items_aligned():
    items = set(load_item_catalog().keys())
    areas = set(get_areas().keys())

    for area_id, gather_def in get_gather_areas().items():
        assert area_id in areas
        for node in gather_def.nodes + gather_def.rare_nodes:
            assert node.item_id in items

    for area_id, hunt_def in get_hunt_areas().items():
        assert area_id in areas
        for beast in hunt_def.beasts:
            assert beast.hp > 0 and beast.attack > 0
            for drop in beast.drops:
                assert drop.item_id in items


def test_canonical_realm_areas_cover_all_realms_once():
    areas = get_areas()
    assert tuple(areas.keys()) == CANONICAL_REALM_AREAS
    assert sorted(area.min_realm for area in areas.values()) == list(range(10))
    assert set(get_gather_areas()) == set(areas)
    assert set(get_hunt_areas()) == set(areas)


def test_old_area_ids_resolve_to_canonical_areas():
    assert resolve_area_id("bamboo_grove") == "mortal_grove"
    assert resolve_area_id("mistwood_village") == "mortal_grove"
    assert resolve_area_id("ashen_cliff") == "qi_refining_cliffs"
    assert resolve_area_id("moonwell_ruins") == "foundation_ruins"
    assert resolve_area_id("verdant_depths") == "foundation_ruins"
    assert resolve_area_id("cursed_swamp") == "core_formation_swamp"


@pytest.mark.parametrize("seed", range(100))
def test_auto_combat_never_crashes(seed: int):
    stats = PlayerCombatStats(
        hp=100 + seed,
        max_hp=100 + seed,
        internal_strength=10 + seed % 50,
        external_strength=10 + seed % 40,
        agility=5 + seed % 20,
        spiritual_sense=5 + seed % 15,
        defense=5 + seed % 25,
        comprehension=5,
        luck=5,
        crit_chance=min(0.4, seed * 0.004),
        dodge=min(0.35, seed * 0.003),
    )
    beast = BeastTemplate(
        beast_id="fuzz",
        name="Fuzz Beast",
        hp=20 + seed % 120,
        attack=5 + seed % 30,
        defense=seed % 15,
    )
    result = resolve_auto_combat(stats, beast, rng=random.Random(seed))
    assert result.rounds_fought >= 0
    assert 0 <= result.player_hp_remaining <= result.player_hp_start
    assert result.beast_hp_remaining >= 0
    assert len(result.log_lines) >= 1
    assert any("You strike" in line for line in result.log_lines if result.rounds_fought > 0)


def test_auto_combat_player_always_attacks_when_rounds_fought(session, player):
    mod = None
    stats = compute_combat_stats(player, session, mod)
    beast = BeastTemplate(beast_id="hare", name="Spirit Hare", hp=500, attack=1, defense=0)
    result = resolve_auto_combat(stats, beast, rng=random.Random(0))
    assert any("You strike" in line for line in result.log_lines)


@pytest.mark.parametrize("area_id", CANONICAL_REALM_AREAS)
@pytest.mark.parametrize("seed", range(20))
def test_gather_all_areas(session, player, area_id: str, seed: int):
    player.realm_index = max(get_areas()[area_id].min_realm, player.realm_index)
    session.commit()
    res = run_gather(session, player, area_id, rng=random.Random(seed))
    assert res.success is True
    assert len(res.drops) >= 1


@pytest.mark.parametrize("area_id", CANONICAL_REALM_AREAS)
@pytest.mark.parametrize("seed", range(20))
def test_hunt_all_areas(session, player, area_id: str, seed: int):
    player.realm_index = max(get_areas()[area_id].min_realm, player.realm_index)
    player.substage = 2
    session.commit()
    res = run_hunt(session, player, area_id, rng=random.Random(seed))
    assert res.combat is not None
    assert len(res.messages) >= 2


def test_cooldown_lines_include_gather_and_hunt(player, cfg):
    now = datetime.now(timezone.utc)
    lines = build_cooldown_lines(player, cfg, now, lambda _n, _l, _s: 0)
    text = "\n".join(lines)
    assert "/gather" in text
    assert "/hunt" in text
    assert "5m" in text


def test_help_mentions_gather_and_hunt():
    sections = dict(get_help_sections())
    exploration = sections["Exploration & crafting"]
    assert "/gather" in exploration
    assert "/hunt" in exploration


def test_drop_sources_include_gather_and_hunt():
    herb_sources = {s.via for s in get_drop_sources("green_dew_herb")}
    assert "`/gather`" in herb_sources
    core_sources = {s.via for s in get_drop_sources("minor_beast_core")}
    assert "`/hunt`" in core_sources


def test_gather_haste_consumed(session, player, cfg):
    now = datetime.now(timezone.utc)
    session.add(
        PlayerEffect(
            player_id=player.id,
            effect_id="haste_gather",
            charges=1,
            value_int=120,
        )
    )
    session.commit()
    assert get_haste_reduction_seconds(session, player.id, "gather") == 120
    shaved = consume_haste_for_activity(session, player.id, "gather")
    session.commit()
    assert shaved == 120
    assert get_haste_reduction_seconds(session, player.id, "gather") == 0


def test_hunt_reminder_schedule_respects_haste(session, player, cfg):
    now = datetime.now(timezone.utc)
    session.add(
        PlayerEffect(
            player_id=player.id,
            effect_id="haste_hunt",
            charges=1,
            value_int=180,
        )
    )
    player.last_hunt_at = now
    session.add(player)
    set_reminder_enabled(session, player, cfg, "hunt", True, now)
    session.commit()

    schedule_after_activity(session, player, cfg, "hunt", now)
    session.commit()

    ready = compute_ready_at(session, player, cfg, "hunt", now=now)
    assert (ready - now).total_seconds() == cfg.hunt_cooldown_seconds - 180


def test_db_migration_adds_gather_hunt_columns():
    import sqlite3
    import tempfile
    from pathlib import Path

    from src.db import _migrate_player_columns

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "legacy.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                guild_id VARCHAR(32),
                discord_id VARCHAR(32),
                last_cultivate_at DATETIME
            )
            """
        )
        conn.commit()
        conn.close()

        engine = create_engine(f"sqlite:///{db_path}", future=True)
        try:
            _migrate_player_columns(engine)
            conn = sqlite3.connect(db_path)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
            conn.close()
            assert "last_gather_at" in cols
            assert "last_hunt_at" in cols
        finally:
            engine.dispose()


def test_cooldown_embed_builds(session, player, cfg):
    now = datetime.now(timezone.utc)
    embed = build_cooldown_embed(
        player,
        cfg,
        now,
        lambda n, last, seconds: 0,
        session=session,
    )
    fields = {f.name: f.value for f in embed.fields}
    assert "Timed commands" in fields
    assert "/gather" in fields["Timed commands"]
