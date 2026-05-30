from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from .area_risk import underleveled_drop_bonus
from .character import get_character_modifiers
from .combat_stats import compute_combat_stats, realm_baseline_stats
from .cooperative_dungeons import CooperativeDungeonDef
from .models import Player

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "spirit_stone_drops.json"


@dataclass(frozen=True)
class HuntStoneConfig:
    drop_chance: dict[str, float]
    base_by_area_realm: tuple[int, ...]
    tier_amount_mult: dict[str, float]
    fortune_qty_cap: float
    fortune_sqrt_scale: float
    drop_luck_as_luck: float
    overlevel_realms_before_penalty: int
    overlevel_qty_mult: float
    qty_jitter_min: float
    qty_jitter_max: float


@dataclass(frozen=True)
class CoopDungeonStoneConfig:
    clear_guaranteed_by_realm: tuple[int, ...]
    room_spill_chance: float
    room_spill_clear_ratio: float
    fortune_qty_cap: float
    fortune_sqrt_scale: float
    drop_luck_as_luck: float


@dataclass(frozen=True)
class SoloDungeonStoneConfig:
    clear_ratio_of_coop: float


@dataclass(frozen=True)
class SpiritStoneDropConfig:
    hunt: HuntStoneConfig
    coop_dungeon: CoopDungeonStoneConfig
    solo_dungeon: SoloDungeonStoneConfig


@lru_cache(maxsize=1)
def load_spirit_stone_drop_config() -> SpiritStoneDropConfig:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    hunt = raw.get("hunt", {})
    coop = raw.get("coop_dungeon", {})
    solo = raw.get("solo_dungeon", {})
    return SpiritStoneDropConfig(
        hunt=HuntStoneConfig(
            drop_chance={str(k): float(v) for k, v in hunt.get("drop_chance", {}).items()},
            base_by_area_realm=tuple(int(x) for x in hunt.get("base_by_area_realm", [])),
            tier_amount_mult={str(k): float(v) for k, v in hunt.get("tier_amount_mult", {}).items()},
            fortune_qty_cap=float(hunt.get("fortune_qty_cap", 0.5)),
            fortune_sqrt_scale=float(hunt.get("fortune_sqrt_scale", 0.22)),
            drop_luck_as_luck=float(hunt.get("drop_luck_as_luck", 150)),
            overlevel_realms_before_penalty=int(hunt.get("overlevel_realms_before_penalty", 3)),
            overlevel_qty_mult=float(hunt.get("overlevel_qty_mult", 0.4)),
            qty_jitter_min=float(hunt.get("qty_jitter_min", 0.9)),
            qty_jitter_max=float(hunt.get("qty_jitter_max", 1.1)),
        ),
        coop_dungeon=CoopDungeonStoneConfig(
            clear_guaranteed_by_realm=tuple(int(x) for x in coop.get("clear_guaranteed_by_realm", [])),
            room_spill_chance=float(coop.get("room_spill_chance", 0.25)),
            room_spill_clear_ratio=float(coop.get("room_spill_clear_ratio", 0.12)),
            fortune_qty_cap=float(coop.get("fortune_qty_cap", 0.4)),
            fortune_sqrt_scale=float(coop.get("fortune_sqrt_scale", 0.22)),
            drop_luck_as_luck=float(coop.get("drop_luck_as_luck", 150)),
        ),
        solo_dungeon=SoloDungeonStoneConfig(
            clear_ratio_of_coop=float(solo.get("clear_ratio_of_coop", 0.6)),
        ),
    )


def invalidate_spirit_stone_drop_cache() -> None:
    load_spirit_stone_drop_config.cache_clear()


def _table_lookup(table: tuple[int, ...], realm_index: int) -> int:
    if not table:
        return 0
    idx = max(0, min(len(table) - 1, realm_index))
    return int(table[idx])


def _reference_luck(realm_index: int) -> float:
    baseline = realm_baseline_stats(realm_index)
    return max(1.0, float(baseline.get("luck", 12)))


def fortune_qty_mult(
    luck: float,
    drop_luck: float,
    ref_luck: float,
    *,
    cap: float,
    sqrt_scale: float,
    drop_luck_as_luck: float,
) -> float:
    fortune = luck + drop_luck * drop_luck_as_luck
    if ref_luck <= 0 or fortune <= ref_luck:
        return 1.0
    bonus = min(cap, math.sqrt(fortune / ref_luck - 1.0) * sqrt_scale)
    return 1.0 + bonus


def _jitter_amount(base: float, rng: random.Random, lo: float, hi: float) -> int:
    return max(1, int(base * rng.uniform(lo, hi)))


def roll_hunt_spirit_stones(
    rng: random.Random,
    *,
    area_min_realm: int,
    player_realm_index: int,
    combat_tier: str,
    gap: int,
    luck: float,
    drop_luck: float,
) -> int:
    cfg = load_spirit_stone_drop_config().hunt
    tier = combat_tier if combat_tier in cfg.drop_chance else "normal"
    if rng.random() > cfg.drop_chance.get(tier, cfg.drop_chance.get("normal", 0.8)):
        return 0

    base = _table_lookup(cfg.base_by_area_realm, area_min_realm)
    base = int(base * cfg.tier_amount_mult.get(tier, 1.0))
    ref_luck = _reference_luck(area_min_realm)
    mult = fortune_qty_mult(
        luck,
        drop_luck,
        ref_luck,
        cap=cfg.fortune_qty_cap,
        sqrt_scale=cfg.fortune_sqrt_scale,
        drop_luck_as_luck=cfg.drop_luck_as_luck,
    )
    if player_realm_index >= area_min_realm + cfg.overlevel_realms_before_penalty:
        mult *= cfg.overlevel_qty_mult
    if gap > 0:
        mult *= underleveled_drop_bonus(gap)
    return _jitter_amount(base * mult, rng, cfg.qty_jitter_min, cfg.qty_jitter_max)


def grant_hunt_spirit_stones(
    session: Session,
    player: Player,
    rng: random.Random,
    *,
    area_min_realm: int,
    combat_tier: str,
    gap: int,
) -> tuple[int, str | None]:
    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    stones = roll_hunt_spirit_stones(
        rng,
        area_min_realm=area_min_realm,
        player_realm_index=player.realm_index,
        combat_tier=combat_tier,
        gap=gap,
        luck=stats.luck,
        drop_luck=mod.drop_luck,
    )
    if stones <= 0:
        return 0, None
    player.spirit_stones += stones
    session.add(player)
    return stones, f"💎 **+{stones}** spirit stones."


def roll_coop_clear_spirit_stones(
    rng: random.Random,
    *,
    dungeon_realm: int,
    luck: float,
    drop_luck: float,
) -> int:
    cfg = load_spirit_stone_drop_config().coop_dungeon
    base = _table_lookup(cfg.clear_guaranteed_by_realm, dungeon_realm)
    ref_luck = _reference_luck(dungeon_realm)
    mult = fortune_qty_mult(
        luck,
        drop_luck,
        ref_luck,
        cap=cfg.fortune_qty_cap,
        sqrt_scale=cfg.fortune_sqrt_scale,
        drop_luck_as_luck=cfg.drop_luck_as_luck,
    )
    hunt_cfg = load_spirit_stone_drop_config().hunt
    return _jitter_amount(
        base * mult,
        rng,
        hunt_cfg.qty_jitter_min,
        hunt_cfg.qty_jitter_max,
    )


def roll_coop_room_spill_stones(
    rng: random.Random,
    *,
    dungeon_realm: int,
    luck: float,
    drop_luck: float,
) -> int:
    cfg = load_spirit_stone_drop_config().coop_dungeon
    if rng.random() > cfg.room_spill_chance:
        return 0
    clear_base = _table_lookup(cfg.clear_guaranteed_by_realm, dungeon_realm)
    spill_base = max(1, int(clear_base * cfg.room_spill_clear_ratio))
    ref_luck = _reference_luck(dungeon_realm)
    mult = fortune_qty_mult(
        luck,
        drop_luck,
        ref_luck,
        cap=cfg.fortune_qty_cap,
        sqrt_scale=cfg.fortune_sqrt_scale,
        drop_luck_as_luck=cfg.drop_luck_as_luck,
    )
    hunt_cfg = load_spirit_stone_drop_config().hunt
    return _jitter_amount(
        spill_base * mult,
        rng,
        hunt_cfg.qty_jitter_min,
        hunt_cfg.qty_jitter_max,
    )


def grant_coop_room_spill(
    session: Session,
    members: list,
    dungeon: CooperativeDungeonDef,
    rng: random.Random,
) -> list[str]:
    lines: list[str] = []
    for member in members:
        player = session.get(Player, member.player_id)
        if player is None:
            continue
        mod = get_character_modifiers(session, player)
        stats = compute_combat_stats(player, session, mod)
        stones = roll_coop_room_spill_stones(
            rng,
            dungeon_realm=dungeon.realm_index,
            luck=stats.luck,
            drop_luck=mod.drop_luck,
        )
        if stones <= 0:
            continue
        player.spirit_stones += stones
        session.add(player)
        name = getattr(member, "dao_name", None) or player.dao_name
        lines.append(f"💎 **{name}** secures **{stones}** spirit stones from the vault.")
    return lines


def grant_coop_dungeon_clear_stones(
    session: Session,
    members: list,
    dungeon: CooperativeDungeonDef,
    rng: random.Random,
) -> list[str]:
    lines: list[str] = []
    for member in members:
        player = session.get(Player, member.player_id)
        if player is None:
            continue
        mod = get_character_modifiers(session, player)
        stats = compute_combat_stats(player, session, mod)
        stones = roll_coop_clear_spirit_stones(
            rng,
            dungeon_realm=dungeon.realm_index,
            luck=stats.luck,
            drop_luck=mod.drop_luck,
        )
        player.spirit_stones += stones
        session.add(player)
        name = getattr(member, "dao_name", None) or player.dao_name
        lines.append(f"💎 **{name}** — **+{stones}** spirit stones from the vault cache.")
    return lines


def grant_solo_dungeon_clear_stones(
    session: Session,
    player: Player,
    rng: random.Random,
    *,
    dungeon_min_realm: int,
) -> tuple[int, str | None]:
    cfg = load_spirit_stone_drop_config()
    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    stones = roll_coop_clear_spirit_stones(
        rng,
        dungeon_realm=dungeon_min_realm,
        luck=stats.luck,
        drop_luck=mod.drop_luck + mod.dungeon_luck,
    )
    stones = max(1, int(stones * cfg.solo_dungeon.clear_ratio_of_coop))
    player.spirit_stones += stones
    session.add(player)
    return stones, f"💎 **+{stones}** spirit stones from the vault cache."
