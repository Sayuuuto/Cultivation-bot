from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Config
from src.models import Base, Player


@pytest.fixture
def cfg() -> Config:
    return Config(
        discord_token="test-token",
        guild_id="986320746710183937",
        database_path=":memory:",
        announce_channel_id=None,
        tutorial_channel_id=None,
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def player(session: Session) -> Player:
    now = datetime.now(timezone.utc)
    p = Player(
        guild_id="test-guild",
        discord_id="test-user",
        discord_username="TestUser",
        dao_name="TestDao",
        origin="Mountain Rises",
        spirit_root="Pure Jade Root",
        moral_path="neutral",
        realm_index=0,
        substage=0,
        qi=0,
        spirit_stones=0,
        stamina=100,
        stamina_last_updated_at=now,
        last_active_at=now,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def player_two(session: Session) -> Player:
    now = datetime.now(timezone.utc)
    p = Player(
        guild_id="test-guild",
        discord_id="other-user",
        discord_username="OtherUser",
        dao_name="OtherDao",
        origin="River Dragon's Gift",
        spirit_root="Scarlet Flame Root",
        moral_path="demonic",
        realm_index=1,
        substage=0,
        qi=50,
        spirit_stones=100,
        stamina=100,
        stamina_last_updated_at=now,
        last_active_at=now,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p
