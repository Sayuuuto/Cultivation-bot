"""Dungeon full-fight integration helpers: training chamber, sliding panel UI, state probe."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session

from src.cooperative_dungeons import (
    CoopRoomDef,
    CooperativeDungeonDef,
    EnemyTemplate,
    get_cooperative_dungeon,
    scaled_enemy_stats,
)
from src.dungeon_combat import DungeonCombatState, load_combat_state, save_combat_state

PYTEST_DUNGEON_ID = "pytest_training_chamber"

PYTEST_TRAINING_DUNGEON = CooperativeDungeonDef(
    dungeon_id=PYTEST_DUNGEON_ID,
    name="Training Chamber",
    realm_index=0,
    recommended_party=1,
    rooms=(
        CoopRoomDef(
            label="Trial Hall",
            enemies=(("warden", 1),),
            boss_template=None,
        ),
    ),
    guaranteed_drops=(),
    bonus_drops=(),
)

_FORBIDDEN_LOG_FRAGMENTS = (
    "something went wrong",
    "check the bot logs",
    "cannot mix embed",
)

_LAST_DUNGEON_STATE: DungeonCombatState | None = None
_LAST_DUNGEON_VIEW: Any = None
_DUNGEON_CHANNEL_MESSAGES: dict[int, dict[str, Any]] = {}
_DUNGEON_MESSAGE_ORDER: list[int] = []
_NEXT_DUNGEON_MSG_ID = 888888888888888800


def last_dungeon_combat_view() -> Any:
    return _LAST_DUNGEON_VIEW


def last_probed_dungeon_state() -> DungeonCombatState | None:
    return _LAST_DUNGEON_STATE


def dungeon_channel_messages() -> list[dict[str, Any]]:
    return [_DUNGEON_CHANNEL_MESSAGES[mid] for mid in _DUNGEON_MESSAGE_ORDER]


def clear_dungeon_probe() -> None:
    global _LAST_DUNGEON_STATE, _LAST_DUNGEON_VIEW
    global _DUNGEON_CHANNEL_MESSAGES, _DUNGEON_MESSAGE_ORDER, _NEXT_DUNGEON_MSG_ID
    _LAST_DUNGEON_STATE = None
    _LAST_DUNGEON_VIEW = None
    _DUNGEON_CHANNEL_MESSAGES = {}
    _DUNGEON_MESSAGE_ORDER = []
    _NEXT_DUNGEON_MSG_ID = 888888888888888800


def install_pytest_dungeon(monkeypatch: Any) -> None:
    """Single-room training dungeon for slash-command integration tests."""
    orig = get_cooperative_dungeon

    def _get(dungeon_id: str) -> CooperativeDungeonDef | None:
        if dungeon_id == PYTEST_DUNGEON_ID:
            return PYTEST_TRAINING_DUNGEON
        return orig(dungeon_id)

    monkeypatch.setattr("src.cooperative_dungeons.get_cooperative_dungeon", _get)
    monkeypatch.setattr("src.dungeon_party.get_cooperative_dungeon", _get)
    monkeypatch.setattr("src.dungeon_combat.get_cooperative_dungeon", _get)
    monkeypatch.setattr("src.dungeon_discord.get_cooperative_dungeon", _get)


def install_weak_dungeon_enemies(monkeypatch: Any) -> None:
    """Keep dungeon fights short while still exercising real combat math."""

    def _weak(
        template: EnemyTemplate,
        *,
        realm_index: int,
        party_size: int,
        is_boss: bool = False,
    ) -> EnemyTemplate:
        _ = realm_index, party_size, is_boss
        return EnemyTemplate(
            template_id=template.template_id,
            name=template.name,
            hp=16,
            attack=4,
            defense=1,
            speed=template.speed,
            combat_tier=template.combat_tier,
            drops=template.drops,
        )

    monkeypatch.setattr("src.cooperative_dungeons.scaled_enemy_stats", _weak)
    monkeypatch.setattr("src.dungeon_combat.scaled_enemy_stats", _weak)


def install_fixed_dungeon_rng(monkeypatch: Any, *, seed: int = 77) -> None:
    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        base = seed ^ hash((guild_id, user_id, salt)) & 0xFFFFFFFF
        return random.Random(base)

    monkeypatch.setattr("src.bot.rng_for", _rng_for)


def install_dungeon_state_probe(monkeypatch: Any) -> None:
    global _LAST_DUNGEON_STATE
    orig = save_combat_state

    def _wrapped(party: Any, state: DungeonCombatState) -> None:
        global _LAST_DUNGEON_STATE
        _LAST_DUNGEON_STATE = state
        return orig(party, state)

    monkeypatch.setattr("src.dungeon_combat.save_combat_state", _wrapped)


def _register_message(*, content: str | None = None, embed: Any = None, view: Any = None) -> MagicMock:
    global _NEXT_DUNGEON_MSG_ID, _LAST_DUNGEON_VIEW
    _NEXT_DUNGEON_MSG_ID += 1
    mid = _NEXT_DUNGEON_MSG_ID
    record: dict[str, Any] = {
        "id": mid,
        "content": content,
        "embed": embed,
        "view": view,
        "was_panel": view is not None,
        "converted_to_log": False,
    }
    _DUNGEON_CHANNEL_MESSAGES[mid] = record
    _DUNGEON_MESSAGE_ORDER.append(mid)
    if view is not None:
        _LAST_DUNGEON_VIEW = view

    async def _edit(**kwargs: Any) -> None:
        if "content" in kwargs:
            record["content"] = kwargs.get("content")
        if "embed" in kwargs:
            record["embed"] = kwargs.get("embed")
        if "view" in kwargs:
            record["view"] = kwargs.get("view")
            if kwargs.get("view") is not None:
                global _LAST_DUNGEON_VIEW
                _LAST_DUNGEON_VIEW = kwargs.get("view")
        if kwargs.get("content") and kwargs.get("view") is None and kwargs.get("embed") is None:
            record["converted_to_log"] = True

    msg = MagicMock()
    msg.id = mid
    msg.edit = AsyncMock(side_effect=_edit)
    return msg


def install_dungeon_channel_mock(monkeypatch: Any) -> None:
    """Mock dungeon channel; runs real _sync_combat_ui (sliding panel + log conversion)."""
    import src.dungeon_discord as dungeon_discord

    channel = MagicMock()
    channel.id = 777777777777777777
    channel.mention = "#dungeon-test"

    async def _send(*args: Any, **kwargs: Any) -> MagicMock:
        content = kwargs.get("content")
        if content is None and args and isinstance(args[0], str):
            content = args[0]
        return _register_message(
            content=content,
            embed=kwargs.get("embed"),
            view=kwargs.get("view"),
        )

    channel.send = AsyncMock(side_effect=_send)

    async def _fetch_message(message_id: int) -> MagicMock:
        mid = int(message_id)
        rec = _DUNGEON_CHANNEL_MESSAGES.get(mid)
        if rec is None:
            raise LookupError(f"message {mid} not found")
        msg = MagicMock()
        msg.id = mid

        async def _edit(**kwargs: Any) -> None:
            if "content" in kwargs:
                rec["content"] = kwargs.get("content")
            if "embed" in kwargs:
                rec["embed"] = kwargs.get("embed")
            if "view" in kwargs:
                rec["view"] = kwargs.get("view")
                if kwargs.get("view") is not None:
                    global _LAST_DUNGEON_VIEW
                    _LAST_DUNGEON_VIEW = kwargs.get("view")
            if kwargs.get("content") and kwargs.get("view") is None and kwargs.get("embed") is None:
                rec["converted_to_log"] = True

        msg.edit = AsyncMock(side_effect=_edit)
        return msg

    channel.fetch_message = AsyncMock(side_effect=_fetch_message)

    async def _mock_create(*args: Any, **kwargs: Any) -> MagicMock:
        return channel

    async def _get_channel(client: Any, channel_id: str | None) -> MagicMock | None:
        if channel_id:
            return channel
        return None

    monkeypatch.setattr(dungeon_discord, "_create_dungeon_channel", _mock_create)
    monkeypatch.setattr(dungeon_discord, "_get_dungeon_channel_by_id", _get_channel)


# Backwards-compatible name for existing fixtures
def install_dungeon_combat_ui_capture(monkeypatch: Any) -> None:
    install_dungeon_channel_mock(monkeypatch)


def load_dungeon_combat_state(db: Session, party_id: int) -> DungeonCombatState | None:
    from src.models import ActiveDungeonParty

    party = db.get(ActiveDungeonParty, party_id)
    if party is None:
        return None
    return load_combat_state(party)


@dataclass
class DungeonFightAudit:
    turns_played: int = 0
    finished: bool = False
    victory: bool = False
    run_complete: bool = False
    room_label: str = ""
    enemy_phases: int = 0
    player_strike_phases: int = 0
    panel_rotations: int = 0
    log_message_count: int = 0
    full_log: list[str] = field(default_factory=list)


def assert_dungeon_log_delta(
    *,
    state: DungeonCombatState,
    new_lines: list[str],
    turn_index: int,
    audit: DungeonFightAudit,
) -> None:
    full_lower = "\n".join(state.log).lower()
    for bad in _FORBIDDEN_LOG_FRAGMENTS:
        assert bad not in full_lower, f"turn {turn_index}: forbidden {bad!r} in log"

    player_acted = any(
        "hits for" in line.lower() or "basic strike" in line.lower() or "strikes" in line.lower()
        for line in new_lines
    )
    if player_acted and not state.finished:
        audit.player_strike_phases += 1

    enemy_acted = any(
        "hits" in line.lower() and "for" not in line.lower().split("hits", 1)[-1][:8]
        for line in new_lines
    ) or any("attacks" in line.lower() for line in new_lines)
    if enemy_acted:
        audit.enemy_phases += 1


def audit_sliding_panel_channel(audit: DungeonFightAudit) -> None:
    """After a fight, channel history should show converted logs + bottom panel pattern."""
    messages = dungeon_channel_messages()
    assert len(messages) >= 2, "expected opening log and at least one panel"

    log_msgs = [m for m in messages if m.get("content")]
    panel_msgs = [m for m in messages if m.get("was_panel")]
    converted = [m for m in messages if m.get("converted_to_log")]
    assert len(converted) >= 1, "panel should convert into log text after a player action"
    assert len(log_msgs) >= 1, "expected at least one combat log message from panel conversion"

    last = messages[-1]
    assert last.get("embed") is not None, "last channel message should be the live or final card"
    assert last.get("view") is None, "finished card should not keep technique buttons"

    if audit.turns_played >= 2 and not audit.finished:
        assert len(panel_msgs) >= 2, "multi-turn fights should rotate the panel"
    else:
        assert len(panel_msgs) >= 1, "at least one live combat panel"

    audit.log_message_count = len(log_msgs)
    audit.panel_rotations = len(panel_msgs)


def finalize_dungeon_audit(state: DungeonCombatState, audit: DungeonFightAudit) -> None:
    audit.finished = state.finished
    audit.victory = state.victory
    audit.run_complete = state.run_complete
    audit.room_label = state.room_label
    audit.full_log = list(state.log)
    assert audit.finished, "dungeon fight did not finish"
    assert audit.victory, f"expected victory; log tail: {audit.full_log[-10:]}"
    assert audit.run_complete, "training chamber should mark run complete"
    audit_sliding_panel_channel(audit)
