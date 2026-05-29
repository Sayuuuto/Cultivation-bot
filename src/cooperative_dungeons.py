from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .combat_stats import scale_monster_stats

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "cooperative_dungeons.json"

_catalog: dict | None = None


@dataclass(frozen=True)
class EnemyTemplate:
    template_id: str
    name: str
    hp: int
    attack: int
    defense: int
    speed: int
    combat_tier: str = "normal"
    drops: tuple = ()


@dataclass(frozen=True)
class CoopRoomDef:
    label: str
    enemies: tuple[tuple[str, int], ...]  # (template_id, count)
    boss_template: str | None


@dataclass(frozen=True)
class CoopRewardEntry:
    item_id: str
    min_qty: int
    max_qty: int
    rarity: str = "common"
    chance: float | None = None


@dataclass(frozen=True)
class CooperativeDungeonDef:
    dungeon_id: str
    name: str
    realm_index: int
    recommended_party: int
    rooms: tuple[CoopRoomDef, ...]
    guaranteed_drops: tuple[CoopRewardEntry, ...]
    bonus_drops: tuple[CoopRewardEntry, ...]


def _load_raw() -> dict:
    global _catalog
    if _catalog is None:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            _catalog = json.load(f)
    return _catalog


def invalidate_cooperative_dungeon_cache() -> None:
    global _catalog
    _catalog = None


def get_enemy_templates() -> dict[str, EnemyTemplate]:
    from .loot import parse_loot_table

    raw = _load_raw().get("enemy_templates", {})
    out: dict[str, EnemyTemplate] = {}
    for tid, data in raw.items():
        tier = str(data.get("combat_tier", "boss" if tid == "boss" else "normal"))
        out[tid] = EnemyTemplate(
            template_id=tid,
            name=str(data["name"]),
            hp=int(data["hp"]),
            attack=int(data["attack"]),
            defense=int(data["defense"]),
            speed=int(data.get("speed", 10)),
            combat_tier=tier,
            drops=parse_loot_table(data.get("drops", [])),
        )
    return out


def get_cooperative_dungeons() -> dict[str, CooperativeDungeonDef]:
    raw = _load_raw()
    templates = get_enemy_templates()
    dungeons: dict[str, CooperativeDungeonDef] = {}
    for dungeon_id, data in raw.get("dungeons", {}).items():
        rooms: list[CoopRoomDef] = []
        for room in data.get("rooms", []):
            enemies: list[tuple[str, int]] = []
            for entry in room.get("enemies", []):
                enemies.append((str(entry["template"]), int(entry["count"])))
            boss = room.get("boss")
            rooms.append(
                CoopRoomDef(
                    label=str(room.get("label", "Chamber")),
                    enemies=tuple(enemies),
                    boss_template=str(boss) if boss else None,
                )
            )
        rewards = data.get("rewards", {})
        from .loot import parse_loot_drop

        guaranteed = tuple(
            CoopRewardEntry(
                item_id=e.item_id,
                min_qty=e.min_qty,
                max_qty=e.max_qty,
                rarity=e.rarity,
            )
            for d in rewards.get("guaranteed", [])
            for e in [parse_loot_drop(d)]
            if e is not None
        )
        bonus = tuple(
            CoopRewardEntry(
                item_id=e.item_id,
                min_qty=e.min_qty,
                max_qty=e.max_qty,
                rarity=e.rarity,
                chance=float(d.get("chance", 1.0)) if d.get("chance") is not None else None,
            )
            for d in rewards.get("bonus", [])
            for e in [parse_loot_drop(d)]
            if e is not None
        )
        dungeons[dungeon_id] = CooperativeDungeonDef(
            dungeon_id=dungeon_id,
            name=str(data["name"]),
            realm_index=int(data["realm_index"]),
            recommended_party=int(data.get("recommended_party", 2)),
            rooms=tuple(rooms),
            guaranteed_drops=guaranteed,
            bonus_drops=bonus,
        )
    return dungeons


def get_cooperative_dungeon(dungeon_id: str) -> CooperativeDungeonDef | None:
    return get_cooperative_dungeons().get(dungeon_id)


def scaled_enemy_stats(
    template: EnemyTemplate,
    *,
    realm_index: int,
    party_size: int,
    is_boss: bool = False,
) -> EnemyTemplate:
    """Scale enemies for realm tier and party size (tuned for 2+ daoists)."""
    scaled = scale_monster_stats(
        template.hp,
        template.attack,
        template.defense,
        realm_index=realm_index,
        combat_tier="boss" if is_boss else template.combat_tier,
    )
    # Tuned for ~2 daoists; solo faces tougher foes, larger parties slightly easier.
    party_mult = (2.0 / max(1, party_size)) ** 0.55
    return EnemyTemplate(
        template_id=template.template_id,
        name=template.name,
        hp=max(1, int(scaled["hp"] * party_mult)),
        attack=max(1, int(scaled["attack"] * party_mult)),
        defense=max(1, int(scaled["defense"])),
        speed=template.speed,
        combat_tier=template.combat_tier,
        drops=template.drops,
    )
