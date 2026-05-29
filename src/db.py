from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import DEFAULT_DATABASE_FILENAME, get_config
from .models import Base

logger = logging.getLogger(__name__)


_engine = None
_session_factory: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    cfg = get_config()
    # Optional: set SQLALCHEMY_ECHO=1 in .env to log all SQL queries.
    import os
    sql_echo = os.getenv("SQLALCHEMY_ECHO", "0") in {"1", "true", "True", "yes", "YES"}
    engine = create_engine(
        f"sqlite:///{cfg.database_path}",
        connect_args={"check_same_thread": False},
        future=True,
        echo=sql_echo,
    )

    _engine = engine
    _session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    return engine


def get_session() -> Session:
    global _session_factory
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return column in {row[1] for row in rows}


def _rename_column_if_exists(conn, table: str, old: str, new: str) -> None:
    if _column_exists(conn, table, old) and not _column_exists(conn, table, new):
        conn.execute(text(f'ALTER TABLE {table} RENAME COLUMN {old} TO {new}'))


def _migrate_clan_rename(engine) -> None:
    """Rename legacy player-guild 'sects' table/columns to 'clans'."""
    with engine.connect() as conn:
        if _table_exists(conn, "sects") and not _table_exists(conn, "clans"):
            conn.execute(text("ALTER TABLE sects RENAME TO clans"))
        if _table_exists(conn, "clans"):
            _rename_column_if_exists(conn, "clans", "sect_qi_contributed", "clan_qi_contributed")
        if _table_exists(conn, "players"):
            _rename_column_if_exists(conn, "players", "sect_id", "clan_id")
            _rename_column_if_exists(conn, "players", "sect_role", "clan_role")
            _rename_column_if_exists(
                conn, "players", "sect_contribution_qi_total", "clan_contribution_qi_total"
            )
        conn.commit()


def _migrate_table_columns(engine, table: str, additions: dict[str, str]) -> None:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        if not rows:
            return
        existing = {row[1] for row in rows}
        for column, col_type in additions.items():
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()


def _migrate_game_sect_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "players",
        {
            "game_sect_id": "VARCHAR(32)",
            "sect_merit": "INTEGER DEFAULT 0",
            "last_sect_task_date": "VARCHAR(10)",
            "sect_daily_task_id": "VARCHAR(64)",
            "sect_daily_task_progress": "INTEGER DEFAULT 0",
            "sect_daily_task_date": "VARCHAR(10)",
            "sect_joined_at": "DATETIME",
            "sect_leave_cooldown_until": "DATETIME",
        },
    )


def _migrate_clan_invite_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "clans",
        {"invite_only": "BOOLEAN DEFAULT 0"},
    )


def _migrate_drop_legacy_stamina_columns(engine) -> None:
    """Drop stamina columns from an older schema; Player no longer maps them."""
    with engine.connect() as conn:
        if not _table_exists(conn, "players"):
            return
        for column in ("stamina_last_updated_at", "stamina"):
            if _column_exists(conn, "players", column):
                conn.execute(text(f"ALTER TABLE players DROP COLUMN {column}"))
        conn.commit()


def _migrate_player_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "players",
        {
            "last_adventure_at": "DATETIME",
            "last_dungeon_at": "DATETIME",
            "last_gather_at": "DATETIME",
            "last_hunt_at": "DATETIME",
            "remind_dms_blocked": "BOOLEAN DEFAULT 0",
            "karma": "INTEGER DEFAULT 0",
            "reputation": "INTEGER DEFAULT 0",
            "novice_trial_step": "INTEGER DEFAULT 6",
            "novice_cultivates": "INTEGER DEFAULT 0",
            "adventures_completed": "INTEGER DEFAULT 0",
            "abode_channel_id": "VARCHAR(32)",
            "foundation_body_json": "VARCHAR(512) DEFAULT '{}'",
            "foundation_meridian_json": "VARCHAR(512) DEFAULT '{}'",
            "meridian_points": "INTEGER DEFAULT 0",
            "body_temper_charges": "INTEGER DEFAULT 0",
            "passive_qi_bank": "INTEGER DEFAULT 0",
            "passive_accrual_at": "DATETIME",
        },
    )
    _migrate_passive_qi_backfill(engine)
    _migrate_novice_trial_existing_players(engine)


def _migrate_passive_qi_backfill(engine) -> None:
    """Seed passive accrual clock from last activity so existing players keep continuity."""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(players)")).fetchall()
        if not rows:
            return
        columns = {row[1] for row in rows}
        if "passive_accrual_at" not in columns:
            return
        if "last_active_at" in columns:
            conn.execute(
                text(
                    "UPDATE players SET passive_accrual_at = last_active_at "
                    "WHERE passive_accrual_at IS NULL AND last_active_at IS NOT NULL"
                )
            )
        conn.execute(
            text(
                "UPDATE players SET passive_accrual_at = CURRENT_TIMESTAMP "
                "WHERE passive_accrual_at IS NULL"
            )
        )
        conn.commit()


def _migrate_novice_trial_existing_players(engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(players)")).fetchall()
        if not rows:
            return
        columns = {row[1] for row in rows}
        if "novice_trial_step" not in columns or "realm_index" not in columns:
            return
        parts = ["realm_index > 0", "substage > 0", "qi > 0", "spirit_stones > 0"]
        if "last_daily_at" in columns:
            parts.insert(0, "last_daily_at IS NOT NULL")
        where = f"novice_trial_step = 0 AND ({' OR '.join(parts)})"
        conn.execute(
            text(
                f"""
                UPDATE players
                SET novice_trial_step = 6
                WHERE {where}
                """
            )
        )
        conn.commit()


def _migrate_equipment_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "player_equipment",
        {
            "item_id": "VARCHAR(64)",
            "stat_power": "INTEGER DEFAULT 0",
            "stat_defense": "INTEGER DEFAULT 0",
            "stat_fortune": "INTEGER DEFAULT 0",
            "stat_insight": "INTEGER DEFAULT 0",
            "technique_tag": "VARCHAR(16)",
            "gear_realm": "INTEGER DEFAULT 0",
            "gear_grade": "VARCHAR(16) DEFAULT 'external'",
            "gear_item_id": "INTEGER",
        },
    )


def _migrate_gear_stash(engine) -> None:
    """Backfill player_gear_items from legacy slot rows."""
    with engine.connect() as conn:
        if not _table_exists(conn, "player_gear_items"):
            return
        if not _table_exists(conn, "player_equipment"):
            return
        rows = conn.execute(
            text(
                """
                SELECT id, player_id, slot, item_id, stat_power, stat_defense, stat_fortune, stat_insight,
                       affix_id, technique_tag, gear_realm, gear_grade, gear_item_id
                FROM player_equipment
                WHERE item_id IS NOT NULL AND (gear_item_id IS NULL OR gear_item_id = 0)
                """
            )
        ).fetchall()
        for (
            eq_id,
            player_id,
            slot,
            item_id,
            stat_power,
            stat_defense,
            stat_fortune,
            stat_insight,
            affix_id,
            technique_tag,
            gear_realm,
            gear_grade,
            _gear_item_id,
        ) in rows:
            result = conn.execute(
                text(
                    """
                    INSERT INTO player_gear_items
                    (player_id, slot, item_id, stat_power, stat_defense, stat_fortune, stat_insight,
                     affix_id, technique_tag, gear_realm, gear_grade, equipped_in_slot)
                    VALUES
                    (:player_id, :slot, :item_id, :stat_power, :stat_defense, :stat_fortune, :stat_insight,
                     :affix_id, :technique_tag, :gear_realm, :gear_grade, :equipped_in_slot)
                    """
                ),
                {
                    "player_id": player_id,
                    "slot": slot,
                    "item_id": item_id,
                    "stat_power": stat_power or 0,
                    "stat_defense": stat_defense or 0,
                    "stat_fortune": stat_fortune or 0,
                    "stat_insight": stat_insight or 0,
                    "affix_id": affix_id,
                    "technique_tag": technique_tag,
                    "gear_realm": gear_realm or 0,
                    "gear_grade": gear_grade or "external",
                    "equipped_in_slot": slot,
                },
            )
            gear_item_id = result.lastrowid
            conn.execute(
                text("UPDATE player_equipment SET gear_item_id = :gear_item_id WHERE id = :eq_id"),
                {"gear_item_id": gear_item_id, "eq_id": eq_id},
            )
        conn.commit()


def _migrate_effect_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "player_effects",
        {"value_int": "INTEGER"},
    )


def _migrate_player_technique_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "player_techniques",
        {"rank": "INTEGER DEFAULT 1"},
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _database_seed_sources() -> list[Path]:
    """Bundled SQLite files checked in order when the live DB has no players."""
    root = _project_root()
    return [
        root / "deploy" / "seed" / DEFAULT_DATABASE_FILENAME,
        root / DEFAULT_DATABASE_FILENAME,
    ]


def _database_seed_mode() -> str:
    """
    if_empty — copy seed when the target DB has no player rows (default).
    always   — copy seed on every startup (overwrites volume; use for one-time sync).
    never    — never copy; only create/migrate an empty schema.
    """
    mode = os.getenv("DATABASE_SEED_MODE", "if_empty").strip().lower()
    if mode in {"if_empty", "always", "never"}:
        return mode
    logger.warning("Unknown DATABASE_SEED_MODE=%r; using if_empty.", mode)
    return "if_empty"


def _local_player_count(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM players").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def maybe_seed_database_file(database_path: str) -> bool:
    """
    On startup, copy cultivation_bot.sqlite3 into DATABASE_PATH from a bundled seed.

    Sources (first match wins):
      1. deploy/seed/cultivation_bot.sqlite3  — commit via publish_database_seed.ps1
      2. ./cultivation_bot.sqlite3 at project root — local dev / manual copy in image

    DATABASE_SEED_MODE:
      if_empty (default) — copy only when the target has no player rows.
      always             — copy every run (overwrites the volume file).
      never              — skip copying.
    """
    target = Path(database_path).resolve()
    mode = _database_seed_mode()
    if mode == "never":
        print(f"Database seed disabled; using {target.as_posix()}")
        return False

    existing = _local_player_count(target)
    if mode == "if_empty" and existing > 0:
        msg = f"Database ready: {target.as_posix()} ({existing} players, seed skipped)"
        logger.info(msg)
        print(msg)
        return False

    for seed in _database_seed_sources():
        if not seed.is_file():
            continue
        try:
            if seed.resolve() == target.resolve():
                msg = f"Database: {target.as_posix()} (using project SQLite file)"
                logger.info(msg)
                print(msg)
                return False
        except OSError:
            pass

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, target)
        players = _local_player_count(target)
        if mode == "always" and existing > 0:
            msg = (
                f"DATABASE_SEED_MODE=always: replaced {target.as_posix()} "
                f"from {seed.as_posix()} ({players} players, was {existing})"
            )
        else:
            msg = f"Copied {seed.as_posix()} -> {target.as_posix()} ({players} players)"
        logger.info(msg)
        print(msg)
        return True

    if mode == "always" and existing > 0:
        msg = (
            f"DATABASE_SEED_MODE=always but no seed file; "
            f"keeping {target.as_posix()} ({existing} players)"
        )
        logger.warning(msg)
        print(msg)
        return False

    msg = (
        f"No seed {DEFAULT_DATABASE_FILENAME} found; "
        f"creating empty database at {target.as_posix()}"
    )
    logger.info(msg)
    print(msg)
    return False


def init_db() -> None:
    cfg = get_config()
    db_path = Path(cfg.database_path).resolve()
    print(f"Database path: {db_path.as_posix()}")
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume_mount and not str(db_path).startswith(Path(volume_mount).resolve().as_posix()):
        logger.warning(
            "DATABASE_PATH is %s but volume is mounted at %s — set DATABASE_PATH=%s/cultivation_bot.sqlite3 in Railway variables.",
            db_path,
            volume_mount,
            volume_mount,
        )
        print(
            f"WARNING: DATABASE_PATH should be {volume_mount}/cultivation_bot.sqlite3 for persistent player data."
        )
    seeded = maybe_seed_database_file(cfg.database_path)
    if seeded:
        global _engine, _session_factory
        if _engine is not None:
            _engine.dispose()
            _engine = None
            _session_factory = None

    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_clan_rename(engine)
    _migrate_drop_legacy_stamina_columns(engine)
    _migrate_player_columns(engine)
    _migrate_equipment_columns(engine)
    _migrate_gear_stash(engine)
    _migrate_effect_columns(engine)
    _migrate_player_technique_columns(engine)
    _migrate_game_sect_columns(engine)
    _migrate_clan_invite_columns(engine)
    _migrate_karma_from_moral_path(engine)


def _migrate_karma_from_moral_path(engine) -> None:
    from .karma import karma_from_legacy_moral_path

    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(players)")).fetchall()
        if not rows:
            return
        columns = {row[1] for row in rows}
        if "karma" not in columns:
            return
        players = conn.execute(
            text("SELECT id, moral_path, karma FROM players WHERE karma = 0 AND moral_path IS NOT NULL")
        ).fetchall()
        for player_id, moral_path, karma in players:
            if karma != 0:
                continue
            mapped = karma_from_legacy_moral_path(str(moral_path or "neutral"))
            if mapped != 0:
                conn.execute(
                    text("UPDATE players SET karma = :karma WHERE id = :id"),
                    {"karma": mapped, "id": player_id},
                )
        conn.commit()

