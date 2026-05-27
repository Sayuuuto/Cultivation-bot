from __future__ import annotations

import os

from src.config import DEFAULT_DATABASE_FILENAME, resolve_database_path


def test_resolve_database_path_explicit(monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", "/custom/game.db")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    assert resolve_database_path() == "/custom/game.db"


def test_resolve_database_path_railway_volume(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    assert resolve_database_path() == f"/data/{DEFAULT_DATABASE_FILENAME}"


def test_resolve_database_path_local_default(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    assert resolve_database_path() == DEFAULT_DATABASE_FILENAME
