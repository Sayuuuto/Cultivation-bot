from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "drop_rarity.json"

VALID_RARITIES = ("common", "uncommon", "rare", "legendary")
COMBAT_TIERS = ("normal", "elite", "boss")


@dataclass(frozen=True)
class LootDropEntry:
    item_id: str
    rarity: str
    min_qty: int
    max_qty: int


@dataclass(frozen=True)
class DropRarityConfig:
    base_chance: dict[str, float]
    tier_chance_multiplier: dict[str, float]
    luck_chance_per_point: float
    drop_luck_scale: float
    realm_above_area_bonus: float
    realm_below_area_penalty: float
    qty_luck_bonus: float
    qty_variance: float
    gather_bonus_roll_chance: float
    gather_bonus_roll_count: int


@lru_cache(maxsize=1)
def load_drop_rarity_config() -> DropRarityConfig:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    return DropRarityConfig(
        base_chance={str(k): float(v) for k, v in raw.get("base_chance", {}).items()},
        tier_chance_multiplier={
            str(k): float(v) for k, v in raw.get("tier_chance_multiplier", {}).items()
        },
        luck_chance_per_point=float(raw.get("luck_chance_per_point", 0.006)),
        drop_luck_scale=float(raw.get("drop_luck_scale", 1.0)),
        realm_above_area_bonus=float(raw.get("realm_above_area_bonus", 0.025)),
        realm_below_area_penalty=float(raw.get("realm_below_area_penalty", 0.015)),
        qty_luck_bonus=float(raw.get("qty_luck_bonus", 0.004)),
        qty_variance=float(raw.get("qty_variance", 0.2)),
        gather_bonus_roll_chance=float(raw.get("gather_bonus_roll_chance", 0.22)),
        gather_bonus_roll_count=int(raw.get("gather_bonus_roll_count", 2)),
    )


def parse_loot_drop(raw: dict) -> LootDropEntry | None:
    item_id = str(raw.get("item_id", "")).strip()
    if not item_id:
        return None
    rarity = str(raw.get("rarity", "common")).lower()
    if rarity not in VALID_RARITIES:
        # Legacy hunt tables used weight as a flat percent — map bands to rarity.
        legacy_weight = raw.get("weight")
        if legacy_weight is not None:
            w = int(legacy_weight)
            if w >= 45:
                rarity = "common"
            elif w >= 20:
                rarity = "uncommon"
            elif w >= 10:
                rarity = "rare"
            else:
                rarity = "legendary"
        else:
            rarity = "common"
    return LootDropEntry(
        item_id=item_id,
        rarity=rarity,
        min_qty=int(raw.get("min", raw.get("min_qty", 1))),
        max_qty=int(raw.get("max", raw.get("max_qty", 1))),
    )


def parse_loot_table(raw_list: list[dict]) -> tuple[LootDropEntry, ...]:
    entries: list[LootDropEntry] = []
    for raw in raw_list:
        entry = parse_loot_drop(raw)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _realm_chance_factor(player_realm_index: int, area_min_realm: int, cfg: DropRarityConfig) -> float:
    diff = player_realm_index - area_min_realm
    if diff > 0:
        return 1.0 + diff * cfg.realm_above_area_bonus
    if diff < 0:
        return max(0.55, 1.0 + diff * cfg.realm_below_area_penalty)
    return 1.0


def effective_drop_chance(
    rarity: str,
    *,
    combat_tier: str = "normal",
    luck: float = 0.0,
    drop_luck: float = 0.0,
    player_realm_index: int = 0,
    area_min_realm: int = 0,
    extra_mult: float = 1.0,
) -> float:
    cfg = load_drop_rarity_config()
    base = cfg.base_chance.get(rarity, cfg.base_chance.get("common", 0.5))
    tier_mult = cfg.tier_chance_multiplier.get(combat_tier, 1.0)
    luck_mult = 1.0 + luck * cfg.luck_chance_per_point + drop_luck * cfg.drop_luck_scale
    realm_mult = _realm_chance_factor(player_realm_index, area_min_realm, cfg)
    chance = base * tier_mult * luck_mult * realm_mult * extra_mult
    return min(0.95, max(0.01, chance))


def _roll_qty(
    entry: LootDropEntry,
    rng: random.Random,
    *,
    qty_mult: float,
    luck: float,
) -> int:
    cfg = load_drop_rarity_config()
    lo, hi = entry.min_qty, entry.max_qty
    if hi < lo:
        hi = lo
    base = rng.randint(lo, hi) if hi > lo else lo
    luck_qty = 1.0 + luck * cfg.qty_luck_bonus
    scaled = base * qty_mult * luck_qty
    if cfg.qty_variance > 0:
        jitter = (rng.random() * 2.0 - 1.0) * cfg.qty_variance
        scaled *= 1.0 + jitter
    return max(1, int(scaled))


def roll_creature_loot(
    drops: tuple[LootDropEntry, ...],
    rng: random.Random,
    *,
    combat_tier: str = "normal",
    luck: float = 0.0,
    drop_luck: float = 0.0,
    player_realm_index: int = 0,
    area_min_realm: int = 0,
    qty_mult: float = 1.0,
    skip_manuals: bool = True,
) -> dict[str, int]:
    """Independent rolls per drop entry (hunt, adventure combat, dungeon foes)."""
    if not drops:
        return {}
    result: dict[str, int] = {}
    for entry in drops:
        if skip_manuals and entry.item_id.startswith("manual_"):
            continue
        chance = effective_drop_chance(
            entry.rarity,
            combat_tier=combat_tier,
            luck=luck,
            drop_luck=drop_luck,
            player_realm_index=player_realm_index,
            area_min_realm=area_min_realm,
        )
        if rng.random() > chance:
            continue
        qty = _roll_qty(entry, rng, qty_mult=qty_mult, luck=luck)
        result[entry.item_id] = result.get(entry.item_id, 0) + qty
    return result


def roll_weighted_loot_pool(
    drops: tuple[LootDropEntry, ...],
    rng: random.Random,
    *,
    luck: float = 0.0,
    drop_luck: float = 0.0,
    player_realm_index: int = 0,
    area_min_realm: int = 0,
    qty_mult: float = 1.0,
) -> tuple[str, int] | None:
    """Pick one entry using rarity as weight, then roll whether it drops."""
    if not drops:
        return None
    weights: list[float] = []
    for entry in drops:
        w = effective_drop_chance(
            entry.rarity,
            luck=luck,
            drop_luck=drop_luck,
            player_realm_index=player_realm_index,
            area_min_realm=area_min_realm,
        )
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        return None
    roll = rng.random() * total
    acc = 0.0
    chosen = drops[-1]
    for entry, w in zip(drops, weights):
        acc += w
        if roll <= acc:
            chosen = entry
            break
    qty = _roll_qty(chosen, rng, qty_mult=qty_mult, luck=luck)
    return chosen.item_id, qty


def roll_bonus_loot(
    drops: tuple[LootDropEntry, ...],
    rng: random.Random,
    *,
    rolls: int,
    luck: float = 0.0,
    drop_luck: float = 0.0,
    player_realm_index: int = 0,
    area_min_realm: int = 0,
    qty_mult: float = 1.0,
) -> dict[str, int]:
    """Extra independent rolls (gather bonus, dungeon room spillover)."""
    merged: dict[str, int] = {}
    for _ in range(max(0, rolls)):
        rolled = roll_creature_loot(
            drops,
            rng,
            luck=luck,
            drop_luck=drop_luck,
            player_realm_index=player_realm_index,
            area_min_realm=area_min_realm,
            qty_mult=qty_mult,
        )
        for item_id, qty in rolled.items():
            merged[item_id] = merged.get(item_id, 0) + qty
    return merged


def merge_loot_dicts(*dicts: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for d in dicts:
        for item_id, qty in d.items():
            merged[item_id] = merged.get(item_id, 0) + qty
    return merged
