from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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


@lru_cache(maxsize=1)
def load_monster_catalog() -> dict[str, MonsterDef]:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    catalog: dict[str, MonsterDef] = {}
    for monster_id, data in raw.items():
        catalog[monster_id] = MonsterDef(
            monster_id=monster_id,
            name=data["name"],
            hp=int(data["hp"]),
            attack=int(data["attack"]),
            defense=int(data["defense"]),
            speed=int(data.get("speed", int(data["attack"] * 0.5))),
            areas=tuple(data.get("areas", [])),
            traits=tuple(data.get("traits", [])),
        )
    return catalog


def get_monster(monster_id: str) -> MonsterDef | None:
    return load_monster_catalog().get(monster_id)


def get_monsters_for_area(area_id: str) -> list[MonsterDef]:
    return [m for m in load_monster_catalog().values() if area_id in m.areas]
