"""Hunt full-fight integration helpers: fixed test beast, log invariants, state probe."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from src.combat.engine import CombatState
from src.combat.session import get_active_combat, load_combat_state, process_combat_action
from src.hunt import HuntBeastDef
from src.models import Player

# Fixed opponent for pytest — always picked when install_pytest_hunt_beast is active.
PYTEST_TRAINING_BEAST = HuntBeastDef(
    beast_id="pytest_training_dummy",
    name="Training Dummy",
    weight=1,
    hp=700,
    attack=14,
    defense=3,
    combat_tier="normal",
    drops=(),
    traits=(),
    tags=(),
)

_SHIELD_GRANT_MARKERS = (
    "raises a shield",
    "emergency shield",
)

_FORBIDDEN_LOG_FRAGMENTS = (
    "something went wrong",
    "check the bot logs",
    "cannot mix embed",
)

_LAST_COMBAT_STATE: CombatState | None = None


def last_probed_combat_state() -> CombatState | None:
    return _LAST_COMBAT_STATE


def clear_combat_state_probe() -> None:
    global _LAST_COMBAT_STATE
    _LAST_COMBAT_STATE = None


def install_pytest_hunt_beast(monkeypatch: Any) -> None:
    """Force /hunt in bamboo_grove to always face the training dummy."""

    def _always_training(beasts: tuple[HuntBeastDef, ...], rng: random.Random) -> HuntBeastDef:
        return PYTEST_TRAINING_BEAST

    monkeypatch.setattr("src.hunt._pick_beast", _always_training)


def install_fixed_hunt_rng(monkeypatch: Any, *, seed: int = 42) -> None:
    """Deterministic combat rolls for integration tests."""

    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        base = seed ^ hash((guild_id, user_id, salt)) & 0xFFFFFFFF
        return random.Random(base)

    monkeypatch.setattr("src.bot.rng_for", _rng_for)


def install_combat_state_probe(monkeypatch: Any) -> None:
    """Capture the latest CombatState returned from process_combat_action (incl. final turn)."""
    import src.bot as bot_module
    import src.combat.session as combat_session

    orig = combat_session.process_combat_action

    def _wrapped(session: Session, player: Player, combat_id: int, action: str, **kwargs: Any):
        global _LAST_COMBAT_STATE
        result, err = orig(session, player, combat_id, action, **kwargs)
        if result is not None:
            _LAST_COMBAT_STATE = result.state
        return result, err

    monkeypatch.setattr(combat_session, "process_combat_action", _wrapped)
    monkeypatch.setattr(bot_module, "process_combat_action", _wrapped)


def load_hunt_combat_state(db: Session, player_id: int) -> CombatState | None:
    active = get_active_combat(db, player_id)
    if active is None:
        return None
    return load_combat_state(active)


@dataclass
class HuntFightAudit:
    """Snapshot after a full button-driven hunt fight."""

    turns_played: int
    finished: bool
    victory: bool
    fled: bool
    opponent_name: str
    final_player_hp: int
    final_opponent_hp: int
    max_player_shield_seen: int
    shield_grant_seen: bool
    opponent_hit_phases: int
    player_strike_phases: int
    full_log: list[str] = field(default_factory=list)


def assert_log_delta_invariants(
    *,
    state: CombatState,
    log_before: list[str],
    new_lines: list[str],
    turn_index: int,
    audit: HuntFightAudit,
) -> None:
    """Validate one turn's log lines and update audit counters."""
    full_lower = "\n".join(state.log).lower()
    for bad in _FORBIDDEN_LOG_FRAGMENTS:
        assert bad not in full_lower, f"turn {turn_index}: forbidden fragment {bad!r} in log"

    assert state.player.hp >= 0, f"turn {turn_index}: negative player HP"
    if not state.finished:
        assert state.opponent.hp >= 0, f"turn {turn_index}: negative opponent HP mid-fight"

    audit.max_player_shield_seen = max(audit.max_player_shield_seen, state.player_shield)
    if any(marker in line.lower() for line in new_lines for marker in _SHIELD_GRANT_MARKERS):
        audit.shield_grant_seen = True

    for line in new_lines:
        if "your shield absorbs" in line.lower():
            assert audit.shield_grant_seen or state.player_shield > 0, (
                f"turn {turn_index}: shield absorb without grant — line: {line!r}; "
                f"player_shield={state.player_shield}, grant_seen={audit.shield_grant_seen}"
            )

    player_acted = any(
        "hits for" in line.lower() or "basic strike" in line.lower() or "strikes" in line.lower()
        for line in new_lines
    )
    if player_acted and not state.finished:
        audit.player_strike_phases += 1

    opponent_acted = any(
        "hits you" in line.lower()
        or "attacks — you dodge" in line.lower()
        or "is stunned and cannot act" in line.lower()
        for line in new_lines
    )
    if opponent_acted:
        audit.opponent_hit_phases += 1

    # After the first exchange, every player strike should provoke a foe response unless fight ended.
    if turn_index >= 1 and player_acted and not state.finished:
        assert opponent_acted, (
            f"turn {turn_index}: player acted but no opponent phase in new lines: {new_lines!r}"
        )

    assert len(state.log) >= len(log_before), "combat log shrank unexpectedly"


def finalize_fight_audit(state: CombatState, audit: HuntFightAudit) -> None:
    audit.finished = state.finished
    audit.victory = state.victory
    audit.fled = state.fled
    audit.opponent_name = state.opponent_name
    audit.final_player_hp = state.player.hp
    audit.final_opponent_hp = state.opponent.hp
    audit.full_log = list(state.log)

    assert audit.opponent_name == PYTEST_TRAINING_BEAST.name
    assert audit.finished, "fight did not finish within turn limit"
    assert audit.victory, f"expected victory vs training dummy; log tail: {audit.full_log[-8:]}"
    assert not audit.fled
    assert audit.opponent_hit_phases >= 2, (
        "monster should hit back multiple times; "
        f"opponent_hit_phases={audit.opponent_hit_phases}"
    )
    assert audit.player_strike_phases >= 2

    if not audit.shield_grant_seen:
        for line in audit.full_log:
            assert "your shield absorbs" not in line.lower(), (
                f"shield absorb without grant in full log: {line!r}"
            )
