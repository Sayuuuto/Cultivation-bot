from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

GUIDE_PATH = Path(__file__).resolve().parent.parent / "config" / "player_guides" / "combat_progression.json"


@lru_cache(maxsize=1)
def load_combat_progression_guides() -> dict[str, Any]:
    with GUIDE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def guide_text(section: str, key: str, *, default: str = "") -> str:
    guides = load_combat_progression_guides()
    block = guides.get(section, {})
    return str(block.get(key, default))


def format_load_hub_line(
    *,
    active_used: int,
    active_cap: int,
    passive_used: int,
    passive_cap: int,
    total_used: int,
    total_cap: int,
) -> str:
    template = guide_text("load_budget", "hub_line")
    return template.format(
        active_used=active_used,
        active_cap=active_cap,
        passive_used=passive_used,
        passive_cap=passive_cap,
        total_used=total_used,
        total_cap=total_cap,
    )


def invalidate_player_guide_cache() -> None:
    load_combat_progression_guides.cache_clear()
