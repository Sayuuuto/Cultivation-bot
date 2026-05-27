from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..loot import LootDropEntry, parse_loot_table

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "monsters.json"


@dataclass(frozen=True)
class MonsterDef:
    monster_id: str
    name: str
    hp: int
    attack: int
    defense: int
    speed: int
    areas: tuple[str, ...]
    traits: tuple[str, ...] = ()
    combat_tier: str = "normal"
    drops: tuple[LootDropEntry, ...] = ()


@lru_cache(maxsize=1)
def load_monster_catalog() -> dict[str, MonsterDef]:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    catalog: dict[str, MonsterDef] = {}
    for monster_id, data in raw.items():
        tier = str(data.get("combat_tier", "boss" if data.get("is_boss") else "normal"))
        catalog[monster_id] = MonsterDef(
            monster_id=monster_id,
            name=data["name"],
            hp=int(data["hp"]),
            attack=int(data["attack"]),
            defense=int(data["defense"]),
            speed=int(data.get("speed", int(data["attack"] * 0.5))),
            areas=tuple(data.get("areas", [])),
            traits=tuple(data.get("traits", [])),
            combat_tier=tier,
            drops=parse_loot_table(data.get("drops", [])),
        )
    return catalog


def get_monster(monster_id: str) -> MonsterDef | None:
    return load_monster_catalog().get(monster_id)


def get_monsters_for_area(area_id: str) -> list[MonsterDef]:
    return [m for m in load_monster_catalog().values() if area_id in m.areas]


def get_area_monsters_by_tier(area_id: str) -> tuple[list[MonsterDef], list[MonsterDef], list[MonsterDef]]:
    """Return (normal, elite, boss) monsters configured for an area."""
    normals: list[MonsterDef] = []
    elites: list[MonsterDef] = []
    bosses: list[MonsterDef] = []
    for monster in get_monsters_for_area(area_id):
        if monster.combat_tier == "boss":
            bosses.append(monster)
        elif monster.combat_tier == "elite":
            elites.append(monster)
        else:
            normals.append(monster)
    return normals, elites, bosses
