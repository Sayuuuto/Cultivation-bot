from __future__ import annotations

import random
from dataclasses import dataclass, field

from src.content import RareEventDef


@dataclass
class ScriptedRNG:
    """Deterministic RNG for multi-step game flows (adventure, craft, dungeon)."""

    floats: list[float] = field(default_factory=list)
    encounter_queue: list[object] = field(default_factory=list)
    randint_queue: list[int] = field(default_factory=list)
    _float_idx: int = 0
    _encounter_idx: int = 0
    _randint_idx: int = 0
    fallback: random.Random = field(default_factory=lambda: random.Random(0))

    def random(self) -> float:
        if self._float_idx < len(self.floats):
            value = self.floats[self._float_idx]
            self._float_idx += 1
            return value
        return self.fallback.random()

    def choice(self, seq):
        if not seq:
            raise IndexError("empty sequence")
        sample = seq[0]
        if hasattr(sample, "item_id") and hasattr(sample, "weight"):
            return sample
        if self._encounter_idx < len(self.encounter_queue):
            picked = self.encounter_queue[self._encounter_idx]
            self._encounter_idx += 1
            if isinstance(picked, int):
                return seq[picked]
            return picked
        return seq[0]

    def randint(self, a: int, b: int) -> int:
        if self._randint_idx < len(self.randint_queue):
            value = self.randint_queue[self._randint_idx]
            self._randint_idx += 1
            return max(a, min(b, value))
        return self.fallback.randint(a, b)


def randint_for_weighted_event(events: tuple[RareEventDef, ...], event_id: str) -> int:
    """Return a weighted-roll value guaranteed to select `event_id`."""
    acc = 0
    for event in events:
        if event.id == event_id:
            return acc + 1
        acc += event.weight
    raise ValueError(f"event {event_id!r} not in pool")


def safe_adventure_segment_floats(*, trigger_rare: bool = False) -> list[float]:
    """Per segment: skip catastrophe, succeed, loot pick, qty jitter, optional rare-event gate."""
    rare = 0.001 if trigger_rare else 0.99
    return [0.99, 0.01, 0.1, 0.5, rare]


def adventure_start_floats(*, segments: int = 2) -> list[float]:
    """Reserve floats for `_pick_encounter` (up to 2 rolls) plus segment resolution."""
    return [0.99, 0.99, *(safe_adventure_segment_floats() * segments)]


def collect_ids_over_seeds(picker, seeds: range) -> set[str]:
    seen: set[str] = set()
    for seed in seeds:
        seen.add(picker(random.Random(seed)))
    return seen
