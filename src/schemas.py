from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

Rarity = Literal["common", "uncommon", "rare", "legendary"]
SlotType = Literal["active", "passive"]
DamageType = Literal["physical", "internal", "none"]
Alignment = Literal["neutral", "righteous", "demonic"]
Role = Literal["applier", "payoff", "finisher", "control", "sustain", "utility"]
Category = Literal[
    "sword", "fire", "body", "soul", "utility", "passive"
]
CombatTier = Literal["normal", "elite", "boss"]
MonsterTrait = Literal[
    "bleed_immune",
    "seal_on_hit",
    "high_stun_chance",
    "cleanse_every_3_turns",
]
ItemCategory = Literal["material", "pill", "equipment", "manual", "key", "special", "supply"]
PathType = Literal["internal", "external", "hp"]
GearSlot = Literal["weapon", "armor", "accessory", "talisman"]
Difficulty = Literal["easy", "medium", "hard", "severe", "deadly", "mythic"]
RecipeType = Literal["key", "pill"]

# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

class ItemSchema(BaseModel):
    name: str
    category: ItemCategory
    description: str

# ---------------------------------------------------------------------------
# Realms
# ---------------------------------------------------------------------------

class RealmLoadBudget(BaseModel):
    active: int = Field(ge=0)
    passive: int = Field(ge=0)
    total: int = Field(ge=0)

class RealmEntry(BaseModel):
    name: str
    base_qi_cap: int = Field(ge=1)
    technique_load_budget: RealmLoadBudget
    technique_rank_cap: int = Field(ge=0, le=10)

class BreakthroughConfig(BaseModel):
    start_success: float = Field(ge=0.0, le=1.0)
    penalty_per_realm: float = Field(ge=0.0)
    penalty_per_substage: float = Field(ge=0.0)
    min_base_success: float = Field(ge=0.0, le=1.0)

class RealmsSchema(BaseModel):
    realms: list[RealmEntry]
    substages: list[str]
    substage_multipliers: list[float]
    breakthrough: BreakthroughConfig

    @field_validator("substages")
    @classmethod
    def substages_must_match_multipliers(cls, v: list[str], info) -> list[str]:
        data = info.data
        if "substage_multipliers" in data and len(v) != len(data["substage_multipliers"]):
            raise ValueError("substages and substage_multipliers must have same length")
        return v

# ---------------------------------------------------------------------------
# Combat Rules
# ---------------------------------------------------------------------------

class StatusDef(BaseModel):
    damage_per_stack: int | None = None
    duration: int = Field(ge=1)
    max_stacks: int = Field(ge=1)
    tags: list[str] = []
    propagates: bool | None = None
    spread_chance: float | None = Field(default=None, ge=0.0, le=1.0)
    cancels_turn: bool | None = None
    control: bool | None = None
    dr_window: int | None = Field(default=None, ge=0)
    dr_multiplier: float | None = Field(default=None, ge=0.0)
    skip_turn_chance: float | None = Field(default=None, ge=0.0, le=1.0)
    damage_mult: float | None = Field(default=None, ge=0.0)

class KarmaPolicy(BaseModel):
    techniques_shift_karma_in_combat: bool = False
    max_karma_per_fight: int = Field(default=3, ge=0)
    max_karma_per_day: int = Field(default=10, ge=0)

class PvpLegalityRules(BaseModel):
    max_legendary: int = Field(ge=0)
    max_control: int = Field(ge=0)
    max_shield: int = Field(ge=0)
    max_heal: int = Field(ge=0)
    max_survival_passive: int = Field(ge=0)

class DotScaling(BaseModel):
    reference_strike: float = Field(ge=1)
    strike_to_potency_ratio: float = Field(ge=0.0)
    potency_exponent: float = Field(ge=0.0)
    min_potency: float = Field(ge=0.0)
    max_potency: float = Field(ge=0.0)
    spread_potency_ratio: float = Field(ge=0.0)

class CombatRulesSchema(BaseModel):
    feature_flags: dict[str, bool]
    max_turns: int = Field(ge=1)
    auto_finish_after_turn: int = Field(ge=1)
    partial_win_beast_hp_fraction: float = Field(ge=0.0, le=1.0)
    flee_base_chance: float = Field(ge=0.0, le=1.0)
    beast_speed_from_attack_ratio: float = Field(ge=0.0)
    player_basic_attack_ratio: float = Field(ge=0.0)
    karma_policy: KarmaPolicy
    pvp_legality: PvpLegalityRules
    dot_scaling: DotScaling
    statuses: dict[str, StatusDef]

# ---------------------------------------------------------------------------
# Techniques
# ---------------------------------------------------------------------------

class EffectDefSchema(BaseModel):
    trigger: str = "on_use"
    type: str
    status: str | None = None
    chance: float | None = Field(default=None, ge=0.0, le=1.0)
    ratio: float | None = Field(default=None, ge=0.0)
    hits: int | None = Field(default=None, ge=1)
    hit_ratio: float | None = Field(default=None, ge=0.0)
    bleed_chance: float | None = Field(default=None, ge=0.0, le=1.0)
    requires_bleeding: bool | None = None
    requires_burning: bool | None = None
    requires_status: str | None = None
    bonus_ratio: float | None = Field(default=None, ge=0.0)
    shield_pct: float | None = Field(default=None, ge=0.0)
    turns: int | None = Field(default=None, ge=1)
    heal_ratio: float | None = Field(default=None, ge=0.0)
    count: int | None = Field(default=None, ge=1)

class PassiveTriggerSchema(BaseModel):
    event: str
    type: str
    bonus: float | None = Field(default=None, ge=0.0)
    chance: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str | None = None
    heal_pct: float | None = Field(default=None, ge=0.0)
    ratio: float | None = Field(default=None, ge=0.0)
    bonus_per_hit: float | None = Field(default=None, ge=0.0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    damage_boost: float | None = Field(default=None, ge=0.0)
    boost_turns: int | None = Field(default=None, ge=1)
    shield_pct: float | None = Field(default=None, ge=0.0)
    cooldown: int | None = Field(default=None, ge=1)

class TechniqueSchema(BaseModel):
    name: str
    category: str
    tier: str
    min_realm: int = Field(ge=0)
    slot_type: str
    damage_type: str
    base_damage: int = Field(ge=0)
    scaling_stat: str
    scaling_ratio: float = Field(ge=0.0)
    cooldown: int = Field(ge=0)
    status_id: str | None = None
    status_chance: float = Field(default=0.0, ge=0.0, le=1.0)
    alignment: str = "neutral"
    role: str = "finisher"
    description: str = ""
    synergy_hint: str = ""
    manual_item_id: str | None = None
    rarity: str = "common"
    targeting: str = "single"
    heal_ratio: float = Field(default=0.0, ge=0.0)
    load: int | None = Field(default=None, ge=1)
    tags: list[str] = []
    effects: list[dict[str, Any]] = []
    passive_triggers: list[dict[str, Any]] = []
    passive_on_bleed: dict[str, Any] | None = None
    passive_burn_bonus: float = Field(default=0.0, ge=0.0)
    passive_crit_bonus: float = Field(default=0.0, ge=0.0)
    rank_effects: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Monsters
# ---------------------------------------------------------------------------

class MonsterDropSchema(BaseModel):
    item_id: str
    rarity: str = "common"
    min: int = Field(default=1, ge=0)
    max: int = Field(default=1, ge=0)

class MonsterSchema(BaseModel):
    name: str
    hp: int = Field(ge=1)
    attack: int = Field(ge=0)
    defense: int = Field(ge=0)
    speed: int | None = Field(default=None, ge=0)
    areas: list[str] = []
    traits: list[str] = []
    combat_tier: str = "normal"
    drops: list[MonsterDropSchema] = []

# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

class DropEntrySchema(BaseModel):
    item_id: str
    min: int = Field(default=1, ge=0)
    max: int = Field(default=1, ge=0)
    rarity: str = "common"

class RareEventSchema(BaseModel):
    id: str
    weight: int = Field(ge=0)
    message: str

class AreaSchema(BaseModel):
    name: str
    difficulty: str
    min_realm: int = Field(ge=0)
    recommended_text: str
    base_success: float = Field(ge=0.0, le=1.0)
    drops: list[DropEntrySchema]
    rare_event_chance: float = Field(default=0.08, ge=0.0, le=1.0)
    rare_events: list[RareEventSchema] = []

# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------

class RecipeSchema(BaseModel):
    name: str
    type: str
    output_item_id: str
    output_quantity: int = Field(default=1, ge=1)
    inputs: dict[str, int]
    success_chance: float = Field(default=1.0, ge=0.0, le=1.0)
    byproduct_item_id: str | None = None
    min_realm: int = Field(default=0, ge=0)

# ---------------------------------------------------------------------------
# Dungeons (solo)
# ---------------------------------------------------------------------------

class DungeonDropSchema(BaseModel):
    item_id: str
    min: int = Field(default=1, ge=0)
    max: int = Field(default=1, ge=0)
    rarity: str = "common"
    chance: float | None = Field(default=None, ge=0.0, le=1.0)

class DungeonSchema(BaseModel):
    name: str
    key_item_id: str
    min_realm: int = Field(ge=0)
    segments: int = Field(default=3, ge=1)
    base_success: float = Field(default=0.6, ge=0.0, le=1.0)
    boss_success: float = Field(default=0.5, ge=0.0, le=1.0)
    guaranteed_drops: list[DungeonDropSchema] = []
    bonus_drops: list[DungeonDropSchema] = []

# ---------------------------------------------------------------------------
# Shop
# ---------------------------------------------------------------------------

class ShopListingSchema(BaseModel):
    name: str
    category: str
    price: int = Field(ge=0)
    description: str
    item_id: str | None = None
    quantity: int = Field(default=1, ge=1)
    type: str | None = None
    slot: str | None = None
    stats: dict[str, int | float] | None = None

# ---------------------------------------------------------------------------
# Equipment Forge
# ---------------------------------------------------------------------------

class ForgeSlotSchema(BaseModel):
    name: str
    item_id: str
    description: str
    technique_tag: str
    inputs: dict[str, int]
    stat_ranges: dict[str, list[int]]

# ---------------------------------------------------------------------------
# Affixes
# ---------------------------------------------------------------------------

class AffixSchema(BaseModel):
    name: str
    description: str
    # remaining keys are stat modifiers (float values)
    model_config = {"extra": "allow"}

# ---------------------------------------------------------------------------
# Gear Catalog
# ---------------------------------------------------------------------------

class GearStatRanges(BaseModel):
    power: list[int] = Field(default=[0, 0], min_length=2, max_length=2)
    defense: list[int] = Field(default=[0, 0], min_length=2, max_length=2)
    fortune: list[int] = Field(default=[0, 0], min_length=2, max_length=2)
    insight: list[int] = Field(default=[0, 0], min_length=2, max_length=2)

class GearEntrySchema(BaseModel):
    id: str
    realm_index: int = Field(ge=0, le=9)
    path: str
    slot: str
    rarity: str
    name: str
    item_id: str
    technique_tag: str
    stat_ranges: GearStatRanges

# ---------------------------------------------------------------------------
# Hunt Targets
# ---------------------------------------------------------------------------

class BeastDropSchema(BaseModel):
    item_id: str
    rarity: str = "common"
    min: int = Field(default=1, ge=0)
    max: int = Field(default=1, ge=0)

class BeastEntrySchema(BaseModel):
    beast_id: str
    name: str
    weight: int = Field(ge=0)
    hp: int = Field(ge=1)
    attack: int = Field(ge=0)
    defense: int = Field(ge=0)
    combat_tier: str | None = None
    traits: list[str] = []
    tags: list[str] = []
    drops: list[BeastDropSchema] = []

class HuntAreaSchema(BaseModel):
    flavor: str
    beasts: list[BeastEntrySchema]

# ---------------------------------------------------------------------------
# Gather Nodes
# ---------------------------------------------------------------------------

class GatherNodeSchema(BaseModel):
    item_id: str
    min: int = Field(default=1, ge=0)
    max: int = Field(default=1, ge=0)
    weight: int = Field(ge=0)
    rarity: str = "common"

class RareNodeSchema(BaseModel):
    item_id: str
    min: int = Field(default=1, ge=0)
    max: int = Field(default=1, ge=0)
    rarity: str = "common"
    message: str

class GatherAreaSchema(BaseModel):
    flavor: str
    nodes: list[GatherNodeSchema]
    rare_node_chance: float = Field(default=0.05, ge=0.0, le=1.0)
    rare_nodes: list[RareNodeSchema] = []

# ---------------------------------------------------------------------------
# Equipment Tiers
# ---------------------------------------------------------------------------

class EquipmentTierStatRange(BaseModel):
    min: list[int]
    max: list[int]

class EquipmentTierPath(BaseModel):
    name: str
    stat_order: list[str]
    weapon: EquipmentTierStatRange
    armor: EquipmentTierStatRange
    accessory: EquipmentTierStatRange
    talisman: EquipmentTierStatRange

class EquipmentTierSchema(BaseModel):
    name: str
    realm: int = Field(ge=0)
    material_item_ids: list[str]
    paths: dict[str, EquipmentTierPath]

# ---------------------------------------------------------------------------
# Other configs (minimal validation)
# ---------------------------------------------------------------------------

class GenericListSchema(BaseModel):
    root: list[dict[str, Any]]

# ---------------------------------------------------------------------------
# Validation runner
# ---------------------------------------------------------------------------

def _load_json(name: str) -> Any:
    path = CONFIG_DIR / name
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def validate_items(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for item_id, data in raw.items():
        try:
            ItemSchema(**data)
        except Exception as e:
            errors.append(f"items.json/{item_id}: {e}")
    return errors

def validate_realms(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        RealmsSchema(**raw)
    except Exception as e:
        errors.append(f"realms.json: {e}")
    return errors

def validate_combat_rules(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        CombatRulesSchema(**raw)
    except Exception as e:
        errors.append(f"combat_rules.json: {e}")
    return errors

def validate_techniques(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    status_registry = _load_json("combat_rules.json")
    known_statuses = set(status_registry.get("statuses", {})) if status_registry else set()
    known_statuses.update(("none",))

    for tech_id, data in raw.items():
        try:
            TechniqueSchema(**data)
        except Exception as e:
            errors.append(f"techniques.json/{tech_id}: schema error: {e}")
            continue

        status_id = data.get("status_id") or "none"
        if status_id != "none" and known_statuses and status_id not in known_statuses:
            errors.append(f"techniques.json/{tech_id}: unknown status_id '{status_id}'")

        manual_id = data.get("manual_item_id")
        if manual_id:
            items_raw = _load_json("items.json")
            if items_raw and manual_id not in items_raw:
                errors.append(f"techniques.json/{tech_id}: manual_item_id '{manual_id}' not found in items.json")

    return errors

def validate_monsters(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items_raw = _load_json("items.json") or {}

    for mon_id, data in raw.items():
        try:
            MonsterSchema(**data)
        except Exception as e:
            errors.append(f"monsters.json/{mon_id}: schema error: {e}")
            continue

        for drop in data.get("drops", []):
            if drop.get("item_id") not in items_raw:
                errors.append(f"monsters.json/{mon_id}: drop item_id '{drop.get('item_id')}' not found in items.json")

        for trait in data.get("traits", []):
            valid_traits = {"bleed_immune", "seal_on_hit", "high_stun_chance", "cleanse_every_3_turns"}
            if trait not in valid_traits:
                errors.append(f"monsters.json/{mon_id}: unknown trait '{trait}'")

    return errors

def validate_areas(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items_raw = _load_json("items.json") or {}

    for area_id, data in raw.items():
        try:
            AreaSchema(**data)
        except Exception as e:
            errors.append(f"areas.json/{area_id}: schema error: {e}")
            continue

        for drop in data.get("drops", []):
            if drop.get("item_id") not in items_raw:
                errors.append(f"areas.json/{area_id}: drop item_id '{drop.get('item_id')}' not found in items.json")

    return errors

def validate_recipes(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items_raw = _load_json("items.json") or {}

    for recipe_id, data in raw.items():
        try:
            RecipeSchema(**data)
        except Exception as e:
            errors.append(f"recipes.json/{recipe_id}: schema error: {e}")
            continue

        for input_id in data.get("inputs", {}):
            if input_id not in items_raw:
                errors.append(f"recipes.json/{recipe_id}: input '{input_id}' not found in items.json")

        output_id = data.get("output_item_id")
        if output_id and output_id not in items_raw:
            errors.append(f"recipes.json/{recipe_id}: output '{output_id}' not found in items.json")

    return errors

def validate_dungeons(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items_raw = _load_json("items.json") or {}

    for dun_id, data in raw.items():
        try:
            DungeonSchema(**data)
        except Exception as e:
            errors.append(f"dungeons.json/{dun_id}: schema error: {e}")
            continue

        for drop in data.get("guaranteed_drops", []) + data.get("bonus_drops", []):
            if drop.get("item_id") not in items_raw:
                errors.append(f"dungeons.json/{dun_id}: drop item_id '{drop.get('item_id')}' not found in items.json")

    return errors

def validate_shop(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    items_raw = _load_json("items.json") or {}

    for listing_id, data in raw.items():
        try:
            ShopListingSchema(**data)
        except Exception as e:
            errors.append(f"shop.json/{listing_id}: schema error: {e}")
            continue

        item_id = data.get("item_id")
        if item_id and item_id not in items_raw:
            errors.append(f"shop.json/{listing_id}: item_id '{item_id}' not found in items.json")

    return errors

def validate_equipment_forge(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for slot_id, data in raw.items():
        try:
            ForgeSlotSchema(**data)
        except Exception as e:
            errors.append(f"equipment_forge.json/{slot_id}: schema error: {e}")
    return errors

def validate_affixes(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for affix_id, data in raw.items():
        try:
            AffixSchema(**data)
        except Exception as e:
            errors.append(f"affixes.json/{affix_id}: schema error: {e}")
    return errors

def validate_gear_catalog(raw: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    items_raw = _load_json("items.json") or {}

    for i, entry in enumerate(raw):
        try:
            GearEntrySchema(**entry)
        except Exception as e:
            errors.append(f"gear_catalog.json[{i}]: schema error: {e}")
            continue

        if entry.get("item_id") and entry["item_id"] not in items_raw:
            errors.append(f"gear_catalog.json[{i}]: item_id '{entry['item_id']}' not found in items.json")

    return errors

def validate_hunt_targets(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for area_id, data in raw.items():
        try:
            HuntAreaSchema(**data)
        except Exception as e:
            errors.append(f"hunt_targets.json/{area_id}: schema error: {e}")
    return errors

def validate_gather_nodes(raw: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for area_id, data in raw.items():
        try:
            GatherAreaSchema(**data)
        except Exception as e:
            errors.append(f"gather_nodes.json/{area_id}: schema error: {e}")
    return errors

def run_validation(*, raise_on_error: bool = True) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    all_ok = True

    validators: list[tuple[str, str]] = [
        ("items.json", "validate_items"),
        ("realms.json", "validate_realms"),
        ("combat_rules.json", "validate_combat_rules"),
        ("techniques.json", "validate_techniques"),
        ("monsters.json", "validate_monsters"),
        ("areas.json", "validate_areas"),
        ("recipes.json", "validate_recipes"),
        ("dungeons.json", "validate_dungeons"),
        ("shop.json", "validate_shop"),
        ("equipment_forge.json", "validate_equipment_forge"),
        ("affixes.json", "validate_affixes"),
        ("hunt_targets.json", "validate_hunt_targets"),
        ("gather_nodes.json", "validate_gather_nodes"),
    ]

    for filename, validator_name in validators:
        raw = _load_json(filename)
        if raw is None:
            results[filename] = ["file not found"]
            all_ok = False
            continue

        validator = globals()[validator_name]
        errs = validator(raw)
        if errs:
            results[filename] = errs
            all_ok = False
        else:
            results[filename] = []

    gear_raw = _load_json("gear_catalog.json")
    if gear_raw is not None:
        errs = validate_gear_catalog(gear_raw)
        if errs:
            results["gear_catalog.json"] = errs
            all_ok = False
        else:
            results["gear_catalog.json"] = []
    else:
        results["gear_catalog.json"] = ["file not found"]
        all_ok = False

    if not all_ok and raise_on_error:
        msg_parts = []
        for filename, errs in results.items():
            if errs:
                msg_parts.append(f"  {filename}:")
                for e in errs:
                    msg_parts.append(f"    - {e}")
        raise RuntimeError("Config validation failed:\n" + "\n".join(msg_parts))

    return results
