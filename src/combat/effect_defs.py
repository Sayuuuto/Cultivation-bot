from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EffectDef:
    trigger: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PassiveTriggerDef:
    event: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


def parse_effect(raw: dict) -> EffectDef:
    params = {k: v for k, v in raw.items() if k not in {"trigger", "type"}}
    return EffectDef(trigger=str(raw.get("trigger", "on_use")), type=str(raw["type"]), params=params)


def parse_passive_trigger(raw: dict) -> PassiveTriggerDef:
    params = {k: v for k, v in raw.items() if k not in {"event", "type"}}
    return PassiveTriggerDef(event=str(raw["event"]), type=str(raw["type"]), params=params)
