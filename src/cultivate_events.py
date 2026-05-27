from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from .inventory import add_item
from .manuals import normalize_manual_drops, pick_manual_from_pool

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "cultivate_events.json"

_events: list[dict] | None = None
_base_rare_chance: float | None = None


def _load_config() -> tuple[float, list[dict]]:
    global _events, _base_rare_chance
    if _events is not None and _base_rare_chance is not None:
        return _base_rare_chance, _events
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    _base_rare_chance = float(raw.get("base_rare_chance", 0.12))
    _events = list(raw.get("events", []))
    return _base_rare_chance, _events


def invalidate_cultivate_events_cache() -> None:
    global _events, _base_rare_chance
    _events = None
    _base_rare_chance = None


@dataclass(frozen=True)
class CultivateEventResult:
    event_id: str
    emoji: str
    title: str
    message: str
    qi_mult: float = 1.0
    bonus_qi: int = 0
    bonus_stones: int = 0
    stamina_restore: int = 0
    drops: dict[str, int] = field(default_factory=dict)


def roll_cultivate_event(
    rng: random.Random,
    *,
    player_id: int | None = None,
    session=None,
    karma: int = 0,
    force_event_id: str | None = None,
) -> CultivateEventResult | None:
    chance, events = _load_config()
    if not events:
        return None

    picked: dict | None = None
    if force_event_id:
        picked = next((e for e in events if e.get("id") == force_event_id), None)
    elif rng.random() <= chance:
        total = sum(int(e.get("weight", 1)) for e in events)
        roll = rng.randint(1, total)
        acc = 0
        for entry in events:
            acc += int(entry.get("weight", 1))
            if roll <= acc:
                picked = entry
                break
        if picked is None:
            picked = events[-1]

    if picked is None:
        return None

    drops: dict[str, int] = dict(picked.get("drops") or {})
    manual_pool = picked.get("manual_pool")
    if manual_pool and session is not None and player_id is not None:
        manual_id = pick_manual_from_pool(
            str(manual_pool), rng, session=session, player_id=player_id, karma=karma
        )
        if manual_id:
            drops[manual_id] = drops.get(manual_id, 0) + 1

    stones_min = int(picked.get("stones_min", 0))
    stones_max = int(picked.get("stones_max", 0))
    bonus_stones = rng.randint(stones_min, stones_max) if stones_max > 0 else 0

    return CultivateEventResult(
        event_id=str(picked["id"]),
        emoji=str(picked.get("emoji", "✨")),
        title=str(picked.get("title", "Rare Event")),
        message=str(picked.get("message", "The dao stirs.")),
        qi_mult=float(picked.get("qi_mult", 1.0)),
        bonus_qi=int(picked.get("bonus_qi", 0)),
        bonus_stones=bonus_stones,
        stamina_restore=int(picked.get("stamina_restore", 0)),
        drops=drops,
    )


def apply_cultivate_bonus_drops(session: Session, player_id: int, drops: dict[str, int]) -> dict[str, int]:
    """Grant cultivate event loot, converting duplicate manuals into fragments."""
    if not drops:
        return {}
    normalized = normalize_manual_drops(session, player_id, drops)
    for item_id, qty in normalized.items():
        add_item(session, player_id, item_id, qty)
    return normalized
