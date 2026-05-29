"""Adventure slash + button flow helpers: scripted encounters and log checks."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from src.adventure import get_active_adventure, get_encounters_for_area
from src.combat.monsters import MonsterDef, get_monster
from tests.rng_helpers import ScriptedRNG


def adventure_choice_success_floats() -> list[float]:
    """Per segment: skip catastrophe, succeed, loot, qty, rare gate."""
    return [0.99, 0.01, 0.1, 0.5, 0.99]

_FORBIDDEN_FRAGMENTS = (
    "something went wrong",
    "check the bot logs",
    "cannot mix embed",
)


def install_scripted_adventure_rng(
    monkeypatch: Any,
    *,
    area_id: str = "bamboo_grove",
    encounter_ids: tuple[str, ...] = ("injured_elder", "injured_elder", "injured_elder"),
    target_segments: int = 3,
) -> ScriptedRNG:
    """Force specific encounters and high success rolls for deterministic E2E runs."""
    pool = {e.id: e for e in get_encounters_for_area(area_id)}
    queue = [pool[eid] for eid in encounter_ids]
    floats: list[float] = []
    for _ in encounter_ids:
        floats.extend(adventure_choice_success_floats())
    rng = ScriptedRNG(floats=floats, encounter_queue=queue, randint_queue=[target_segments])

    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        _ = guild_id, user_id, salt
        return rng

    monkeypatch.setattr("src.bot.rng_for", _rng_for)
    return rng


def install_weak_adventure_monster(monkeypatch: Any, *, monster_id: str = "bamboo_specter") -> None:
    orig = get_monster

    def _get(mid: str) -> MonsterDef | None:
        m = orig(mid)
        if m is None or mid != monster_id:
            return m
        return MonsterDef(
            monster_id=m.monster_id,
            name=m.name,
            hp=450,
            attack=8,
            defense=3,
            speed=m.speed,
            areas=m.areas,
            traits=m.traits,
            combat_tier=m.combat_tier,
            drops=m.drops,
        )

    monkeypatch.setattr("src.combat.monsters.get_monster", _get)
    monkeypatch.setattr("src.adventure.get_monster", _get)


def install_forced_adventure_encounters(
    monkeypatch: Any,
    *,
    area_id: str,
    encounter_ids: tuple[str, ...],
) -> None:
    """Pin segment 1..N encounters regardless of moral/combat weighting."""
    pool = {e.id: e for e in get_encounters_for_area(area_id)}
    sequence = [pool[eid] for eid in encounter_ids]
    import src.adventure as adventure_module

    def _pick(
        rng: random.Random,
        area: str,
        segment: int,
        player: Any | None = None,
        *,
        state: dict | None = None,
    ):
        _ = rng, area, player, state
        idx = max(0, segment - 1)
        if idx < len(sequence):
            return sequence[idx]
        return sequence[-1]

    monkeypatch.setattr(adventure_module, "_pick_encounter", _pick)


def install_adventure_combat_rng(monkeypatch: Any, *, seed: int = 91) -> None:
    def _rng_for(guild_id: str, user_id: str, *, salt: str = "") -> random.Random:
        base = seed ^ hash((guild_id, user_id, salt)) & 0xFFFFFFFF
        return random.Random(base)

    monkeypatch.setattr("src.bot.rng_for", _rng_for)


def assert_no_forbidden_copy(text: str, *, context: str = "") -> None:
    lower = text.lower()
    prefix = f"{context}: " if context else ""
    for bad in _FORBIDDEN_FRAGMENTS:
        assert bad not in lower, f"{prefix}forbidden fragment {bad!r}"


def active_adventure_id(db: Session, player_id: int) -> int | None:
    row = get_active_adventure(db, player_id)
    return row.id if row is not None else None


@dataclass
class AdventureFlowAudit:
    choice_steps: int = 0
    combat_turns: int = 0
    completed: bool = False
    final_text: str = ""
