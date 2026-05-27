from __future__ import annotations

import sqlite3
from pathlib import Path

from src.db import DEFAULT_DATABASE_FILENAME, maybe_seed_database_file


def _write_players_db(path: Path, count: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE players (id INTEGER PRIMARY KEY, discord_id TEXT, guild_id TEXT)"
    )
    for i in range(count):
        conn.execute(
            "INSERT INTO players (discord_id, guild_id) VALUES (?, ?)",
            (f"user-{i}", "guild-1"),
        )
    conn.commit()
    conn.close()


def test_seed_copies_when_target_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_SEED_MODE", "if_empty")
    seed_dir = tmp_path / "deploy" / "seed"
    seed_dir.mkdir(parents=True)
    seed = seed_dir / DEFAULT_DATABASE_FILENAME
    _write_players_db(seed, 2)

    target = tmp_path / "live" / DEFAULT_DATABASE_FILENAME
    root = tmp_path

    import src.db as db_mod

    original_root = db_mod._project_root
    db_mod._project_root = lambda: root  # type: ignore[assignment]
    try:
        assert maybe_seed_database_file(str(target)) is True
    finally:
        db_mod._project_root = original_root  # type: ignore[assignment]

    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 2
    conn.close()


def test_seed_always_overwrites(tmp_path: Path, monkeypatch) -> None:
    seed_dir = tmp_path / "deploy" / "seed"
    seed_dir.mkdir(parents=True)
    _write_players_db(seed_dir / DEFAULT_DATABASE_FILENAME, 3)

    target = tmp_path / "live" / DEFAULT_DATABASE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_players_db(target, 1)

    import src.db as db_mod

    monkeypatch.setenv("DATABASE_SEED_MODE", "always")
    original_root = db_mod._project_root
    db_mod._project_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        assert maybe_seed_database_file(str(target)) is True
    finally:
        db_mod._project_root = original_root  # type: ignore[assignment]

    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 3
    conn.close()


def test_seed_skips_when_target_has_players(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_SEED_MODE", "if_empty")
    seed_dir = tmp_path / "deploy" / "seed"
    seed_dir.mkdir(parents=True)
    _write_players_db(seed_dir / DEFAULT_DATABASE_FILENAME, 5)

    target = tmp_path / DEFAULT_DATABASE_FILENAME
    _write_players_db(target, 1)

    import src.db as db_mod

    original_root = db_mod._project_root
    db_mod._project_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        assert maybe_seed_database_file(str(target)) is False
    finally:
        db_mod._project_root = original_root  # type: ignore[assignment]

    conn = sqlite3.connect(target)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1
    conn.close()
