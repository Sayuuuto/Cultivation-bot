from __future__ import annotations

from .catalog import TechniqueDef

TARGET_SINGLE = "single"
TARGET_ALL_ENEMIES = "all_enemies"
TARGET_SELF = "self"


def technique_targeting(tech: TechniqueDef | None) -> str:
    if tech is None:
        return TARGET_SINGLE
    mode = getattr(tech, "targeting", TARGET_SINGLE) or TARGET_SINGLE
    return mode if mode in (TARGET_SINGLE, TARGET_ALL_ENEMIES, TARGET_SELF) else TARGET_SINGLE


def technique_hits_all_enemies(tech: TechniqueDef | None) -> bool:
    return technique_targeting(tech) == TARGET_ALL_ENEMIES


def technique_needs_manual_target(tech: TechniqueDef | None) -> bool:
    """Dungeon (and similar) fights require picking a foe only for single-target arts."""
    return technique_targeting(tech) == TARGET_SINGLE
