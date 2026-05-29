from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

CANONICAL_REALM_AREAS: tuple[str, ...] = (
    "mortal_grove",
    "qi_refining_cliffs",
    "foundation_ruins",
    "core_formation_swamp",
    "nascent_soul_peak",
    "spirit_severing_abyss",
    "void_refinement_expanse",
    "immortal_ascension_gate",
    "heavenly_transcendence_domain",
    "immortal_monarch_court",
)

AREA_ALIASES: dict[str, str] = {
    "bamboo_grove": "mortal_grove",
    "mistwood_village": "mortal_grove",
    "ashen_cliff": "qi_refining_cliffs",
    "moonwell_ruins": "foundation_ruins",
    "verdant_depths": "foundation_ruins",
    "cursed_swamp": "core_formation_swamp",
}


@dataclass(frozen=True)
class DropEntry:
    item_id: str
    min_qty: int
    max_qty: int
    rarity: str = "common"
    weight: int = 0
    chance: float | None = None


@dataclass(frozen=True)
class RareEventDef:
    id: str
    weight: int
    message: str


@dataclass(frozen=True)
class AreaDef:
    area_id: str
    name: str
    difficulty: str
    min_realm: int
    recommended_text: str
    base_success: float
    drops: tuple[DropEntry, ...]
    rare_event_chance: float
    rare_events: tuple[RareEventDef, ...]


@dataclass(frozen=True)
class RecipeDef:
    recipe_id: str
    name: str
    recipe_type: str
    output_item_id: str
    output_quantity: int
    inputs: dict[str, int]
    success_chance: float
    byproduct_item_id: str | None
    min_realm: int = 0


@dataclass(frozen=True)
class DungeonDef:
    dungeon_id: str
    name: str
    key_item_id: str
    min_realm: int
    segments: int
    base_success: float
    boss_success: float
    guaranteed_drops: tuple[DropEntry, ...]
    bonus_drops: tuple[DropEntry, ...]


@dataclass(frozen=True)
class ModifierDef:
    key: str
    values: dict[str, float]
    description: str = ""


@dataclass(frozen=True)
class AffixDef:
    affix_id: str
    name: str
    values: dict[str, float]
    description: str


_loaded = False
_areas: dict[str, AreaDef] = {}
_recipes: dict[str, RecipeDef] = {}
_dungeons: dict[str, DungeonDef] = {}
_origins: dict[str, ModifierDef] = {}
_spirit_roots: dict[str, ModifierDef] = {}
_affixes: dict[str, AffixDef] = {}


def _load_json(name: str) -> dict:
    with (CONFIG_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def load_all_content() -> None:
    global _loaded, _areas, _recipes, _dungeons, _origins, _spirit_roots, _affixes
    if _loaded:
        return

    areas_raw = _load_json("areas.json")
    areas: dict[str, AreaDef] = {}
    for area_id, data in areas_raw.items():
        from .loot import parse_loot_table

        drops = tuple(
            DropEntry(
                item_id=e.item_id,
                min_qty=e.min_qty,
                max_qty=e.max_qty,
                rarity=e.rarity,
            )
            for e in parse_loot_table(data["drops"])
        )
        rare_events = tuple(
            RareEventDef(id=e["id"], weight=e["weight"], message=e["message"])
            for e in data.get("rare_events", [])
        )
        areas[area_id] = AreaDef(
            area_id=area_id,
            name=data["name"],
            difficulty=data["difficulty"],
            min_realm=data["min_realm"],
            recommended_text=data["recommended_text"],
            base_success=data["base_success"],
            drops=drops,
            rare_event_chance=data.get("rare_event_chance", 0.08),
            rare_events=rare_events,
        )
    _areas = areas

    recipes_raw = _load_json("recipes.json")
    recipes: dict[str, RecipeDef] = {}
    for recipe_id, data in recipes_raw.items():
        recipes[recipe_id] = RecipeDef(
            recipe_id=recipe_id,
            name=data["name"],
            recipe_type=data["type"],
            output_item_id=data["output_item_id"],
            output_quantity=data.get("output_quantity", 1),
            inputs=dict(data["inputs"]),
            success_chance=data.get("success_chance", 1.0),
            byproduct_item_id=data.get("byproduct_item_id"),
            min_realm=int(data.get("min_realm", 0)),
        )
    _recipes = recipes

    dungeons_raw = _load_json("dungeons.json")
    dungeons: dict[str, DungeonDef] = {}
    for dungeon_id, data in dungeons_raw.items():
        from .loot import parse_loot_drop, parse_loot_table

        guaranteed = tuple(
            DropEntry(
                item_id=e.item_id,
                min_qty=e.min_qty,
                max_qty=e.max_qty,
                rarity=e.rarity,
            )
            for e in parse_loot_table(data.get("guaranteed_drops", []))
        )
        bonus = tuple(
            DropEntry(
                item_id=e.item_id,
                min_qty=e.min_qty,
                max_qty=e.max_qty,
                rarity=e.rarity,
                chance=d.get("chance"),
            )
            for d in data.get("bonus_drops", [])
            for e in [parse_loot_drop(d)]
            if e is not None
        )
        dungeons[dungeon_id] = DungeonDef(
            dungeon_id=dungeon_id,
            name=data["name"],
            key_item_id=data["key_item_id"],
            min_realm=data["min_realm"],
            segments=data.get("segments", 3),
            base_success=data.get("base_success", 0.6),
            boss_success=data.get("boss_success", 0.5),
            guaranteed_drops=guaranteed,
            bonus_drops=bonus,
        )
    _dungeons = dungeons

    def _load_modifiers(filename: str) -> dict[str, ModifierDef]:
        raw = _load_json(filename)
        return {
            key: ModifierDef(key=key, values={k: v for k, v in data.items() if k != "description"}, description=data.get("description", ""))
            for key, data in raw.items()
        }

    _origins = _load_modifiers("origins.json")
    _spirit_roots = _load_modifiers("spirit_roots.json")

    affix_raw = _load_json("affixes.json")
    affixes: dict[str, AffixDef] = {}
    for affix_id, data in affix_raw.items():
        affixes[affix_id] = AffixDef(
            affix_id=affix_id,
            name=data["name"],
            values={k: v for k, v in data.items() if k not in ("name", "description")},
            description=data.get("description", ""),
        )
    _affixes = affixes

    _loaded = True


def get_areas() -> dict[str, AreaDef]:
    load_all_content()
    return _areas


def resolve_area_id(area_id: str | None) -> str | None:
    if area_id is None:
        return None
    normalized = area_id.strip().lower().replace(" ", "_").replace("-", "_")
    if not normalized:
        return None
    areas = get_areas()
    if normalized in areas:
        return normalized
    alias = AREA_ALIASES.get(normalized)
    if alias in areas:
        return alias

    matches = [
        candidate_id
        for candidate_id, area in areas.items()
        if area.name.lower() == area_id.strip().lower()
    ]
    if len(matches) == 1:
        return matches[0]

    tokens = [part for part in normalized.replace("_", " ").split() if part]
    fuzzy = [
        candidate_id
        for candidate_id, area in areas.items()
        if tokens
        and all(
            token in f"{candidate_id.replace('_', ' ')} {area.name}".lower()
            for token in tokens
        )
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def area_for_realm(realm_index: int) -> AreaDef:
    areas = get_areas()
    for area_id in CANONICAL_REALM_AREAS:
        area = areas.get(area_id)
        if area is not None and area.min_realm == realm_index:
            return area
    eligible = [area for area in areas.values() if area.min_realm <= realm_index]
    if eligible:
        return max(eligible, key=lambda area: area.min_realm)
    return min(areas.values(), key=lambda area: area.min_realm)


def get_area(area_id: str) -> AreaDef | None:
    resolved = resolve_area_id(area_id)
    if resolved is None:
        return None
    return get_areas().get(resolved)


def get_recipes() -> dict[str, RecipeDef]:
    load_all_content()
    return _recipes


def get_recipe(recipe_id: str) -> RecipeDef | None:
    return get_recipes().get(recipe_id)


def get_dungeons() -> dict[str, DungeonDef]:
    load_all_content()
    return _dungeons


def get_dungeon(dungeon_id: str) -> DungeonDef | None:
    return get_dungeons().get(dungeon_id)


def get_origin_modifiers(origin: str) -> ModifierDef | None:
    load_all_content()
    return _origins.get(origin)


def get_spirit_root_modifiers(spirit_root: str) -> ModifierDef | None:
    load_all_content()
    return _spirit_roots.get(spirit_root)


def get_affix(affix_id: str) -> AffixDef | None:
    load_all_content()
    return _affixes.get(affix_id)


def get_all_affixes() -> dict[str, AffixDef]:
    load_all_content()
    return _affixes
