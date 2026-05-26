from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_config
from .models import Base


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


def _migrate_player_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "players",
        {
            "last_adventure_at": "DATETIME",
            "last_dungeon_at": "DATETIME",
            "remind_dms_blocked": "BOOLEAN DEFAULT 0",
        },
    )


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
        },
    )


def _migrate_effect_columns(engine) -> None:
    _migrate_table_columns(
        engine,
        "player_effects",
        {"value_int": "INTEGER"},
    )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_player_columns(engine)
    _migrate_equipment_columns(engine)
    _migrate_effect_columns(engine)

