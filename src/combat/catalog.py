from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .effect_defs import EffectDef, PassiveTriggerDef, parse_effect, parse_passive_trigger
from .rarity import normalize_rarity

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "techniques.json"


@dataclass(frozen=True)
class TechniqueDef:
    technique_id: str
    name: str
    category: str
    tier: str
    rarity: str
    min_realm: int
    slot_type: str
    damage_type: str
    base_damage: int
    scaling_stat: str
    scaling_ratio: float
    cooldown: int
    status_id: str | None
    status_chance: float
    description: str
    manual_item_id: str | None
    alignment: str = "neutral"
    role: str = "finisher"
    heal_ratio: float = 0.0
    passive_on_bleed: dict | None = None
    passive_burn_bonus: float = 0.0
    passive_crit_bonus: float = 0.0
    effects: tuple[EffectDef, ...] = ()
    passive_triggers: tuple[PassiveTriggerDef, ...] = ()
    synergy_hint: str = ""
    targeting: str = "single"


def _parse_effects(raw: list[dict] | None) -> tuple[EffectDef, ...]:
    if not raw:
        return ()
    return tuple(parse_effect(entry) for entry in raw)


def _parse_passive_triggers(raw: list[dict] | None) -> tuple[PassiveTriggerDef, ...]:
    if not raw:
        return ()
    return tuple(parse_passive_trigger(entry) for entry in raw)


def _legacy_passive_triggers(data: dict[str, Any]) -> tuple[PassiveTriggerDef, ...]:
    triggers: list[PassiveTriggerDef] = []
    if data.get("passive_crit_bonus"):
        triggers.append(PassiveTriggerDef("passive", "crit_bonus", {"bonus": float(data["passive_crit_bonus"])}))
    if data.get("passive_burn_bonus"):
        triggers.append(
            PassiveTriggerDef("on_use", "burn_damage_bonus", {"bonus": float(data["passive_burn_bonus"])})
        )
    if data.get("passive_on_bleed"):
        triggers.append(
            PassiveTriggerDef(
                "on_turn_end",
                "heal_if_foe_status",
                {"status": "bleed", "heal_pct": float(data["passive_on_bleed"].get("heal_pct", 0.05))},
            )
        )
    return tuple(triggers)


@lru_cache(maxsize=1)
def load_technique_catalog() -> dict[str, TechniqueDef]:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    catalog: dict[str, TechniqueDef] = {}
    for technique_id, data in raw.items():
        effects = _parse_effects(data.get("effects"))
        passive_triggers = _parse_passive_triggers(data.get("passive_triggers"))
        if not passive_triggers:
            passive_triggers = _legacy_passive_triggers(data)
        catalog[technique_id] = TechniqueDef(
            technique_id=technique_id,
            name=data["name"],
            category=data["category"],
            tier=data["tier"],
            rarity=normalize_rarity(data.get("rarity")),
            min_realm=int(data.get("min_realm", 0)),
            slot_type=data["slot_type"],
            damage_type=data.get("damage_type", "physical"),
            base_damage=int(data.get("base_damage", 0)),
            scaling_stat=data.get("scaling_stat", "external_strength"),
            scaling_ratio=float(data.get("scaling_ratio", 0.0)),
            cooldown=int(data.get("cooldown", 0)),
            status_id=data.get("status_id"),
            status_chance=float(data.get("status_chance", 0.0)),
            description=data.get("description", ""),
            manual_item_id=data.get("manual_item_id"),
            alignment=str(data.get("alignment", "neutral")),
            role=str(data.get("role", "finisher")),
            heal_ratio=float(data.get("heal_ratio", 0.0)),
            passive_on_bleed=data.get("passive_on_bleed"),
            passive_burn_bonus=float(data.get("passive_burn_bonus", 0.0)),
            passive_crit_bonus=float(data.get("passive_crit_bonus", 0.0)),
            effects=effects,
            passive_triggers=passive_triggers,
            synergy_hint=str(data.get("synergy_hint", "")),
            targeting=str(data.get("targeting", "single")),
        )
    return catalog


def get_technique(technique_id: str) -> TechniqueDef | None:
    return load_technique_catalog().get(technique_id)


def get_technique_by_manual(manual_item_id: str) -> TechniqueDef | None:
    for tech in load_technique_catalog().values():
        if tech.manual_item_id == manual_item_id:
            return tech
    return None


def list_techniques_for_realm(realm_index: int) -> list[TechniqueDef]:
    return [t for t in load_technique_catalog().values() if t.min_realm <= realm_index]


def invalidate_technique_catalog_cache() -> None:
    load_technique_catalog.cache_clear()
