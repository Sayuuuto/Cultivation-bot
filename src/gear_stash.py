from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .equipment_tiers import EquipmentTierEntry, gear_is_active, path_label
from .inventory import add_item, get_item_name
from .models import EQUIPMENT_SLOTS, Player, PlayerEquipment, PlayerGearItem

RECYCLING_PATH = Path(__file__).resolve().parent.parent / "config" / "gear_recycling.json"


@dataclass
class GearView:
    gear_item_id: int | None
    slot: str
    item_id: str | None
    stat_power: int
    stat_defense: int
    stat_fortune: int
    stat_insight: int
    affix_id: str | None
    technique_tag: str | None
    gear_realm: int
    gear_grade: str


@dataclass
class EquipResult:
    success: bool
    message: str


@dataclass
class RecycleResult:
    success: bool
    message: str
    spirit_stones: int = 0
    affix_stones: int = 0


@lru_cache(maxsize=1)
def _load_recycling_cfg() -> dict:
    with RECYCLING_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def invalidate_gear_stash_cache() -> None:
    _load_recycling_cfg.cache_clear()


def gear_item_to_view(item: PlayerGearItem) -> GearView:
    return GearView(
        gear_item_id=item.id,
        slot=item.slot,
        item_id=item.item_id,
        stat_power=item.stat_power,
        stat_defense=item.stat_defense,
        stat_fortune=item.stat_fortune,
        stat_insight=item.stat_insight,
        affix_id=item.affix_id,
        technique_tag=item.technique_tag,
        gear_realm=item.gear_realm,
        gear_grade=item.gear_grade,
    )


def resolve_equipped_gear(session: Session, eq: PlayerEquipment) -> GearView | None:
    if eq.gear_item_id is not None:
        item = session.get(PlayerGearItem, eq.gear_item_id)
        if item is not None:
            return gear_item_to_view(item)
    if not eq.item_id:
        return None
    return GearView(
        gear_item_id=None,
        slot=eq.slot,
        item_id=eq.item_id,
        stat_power=eq.stat_power,
        stat_defense=eq.stat_defense,
        stat_fortune=eq.stat_fortune,
        stat_insight=eq.stat_insight,
        affix_id=eq.affix_id,
        technique_tag=eq.technique_tag,
        gear_realm=eq.gear_realm,
        gear_grade=eq.gear_grade,
    )


def sync_slot_from_gear_item(eq: PlayerEquipment, item: PlayerGearItem) -> None:
    eq.gear_item_id = item.id
    eq.item_id = item.item_id
    eq.stat_power = item.stat_power
    eq.stat_defense = item.stat_defense
    eq.stat_fortune = item.stat_fortune
    eq.stat_insight = item.stat_insight
    eq.affix_id = item.affix_id
    eq.technique_tag = item.technique_tag
    eq.gear_realm = item.gear_realm
    eq.gear_grade = item.gear_grade


def clear_slot_row(eq: PlayerEquipment) -> None:
    eq.gear_item_id = None
    eq.item_id = None
    eq.stat_power = 0
    eq.stat_defense = 0
    eq.stat_fortune = 0
    eq.stat_insight = 0
    eq.affix_id = None
    eq.technique_tag = None
    eq.gear_realm = 0
    eq.gear_grade = "external"


def create_gear_item(
    session: Session,
    player_id: int,
    entry: EquipmentTierEntry,
    rolled_stats: dict[str, int],
    *,
    realm_index: int,
    grade: str,
) -> PlayerGearItem:
    item = PlayerGearItem(
        player_id=player_id,
        slot=entry.slot,
        item_id=entry.item_id,
        stat_power=int(rolled_stats.get("power", 0)),
        stat_defense=int(rolled_stats.get("defense", 0)),
        stat_fortune=int(rolled_stats.get("fortune", 0)),
        stat_insight=int(rolled_stats.get("insight", 0)),
        technique_tag=entry.technique_tag,
        gear_realm=max(0, int(realm_index)),
        gear_grade=grade,
        equipped_in_slot=None,
    )
    session.add(item)
    session.flush()
    return item


def create_gear_item_from_shop(
    session: Session,
    player_id: int,
    *,
    slot: str,
    item_id: str,
    stats: dict[str, int],
    realm_index: int,
    grade: str = "external",
    technique_tag: str | None = None,
) -> PlayerGearItem:
    item = PlayerGearItem(
        player_id=player_id,
        slot=slot,
        item_id=item_id,
        stat_power=int(stats.get("power", 0)),
        stat_defense=int(stats.get("defense", 0)),
        stat_fortune=int(stats.get("fortune", 0)),
        stat_insight=int(stats.get("insight", 0)),
        technique_tag=technique_tag,
        gear_realm=max(0, int(realm_index)),
        gear_grade=grade,
        equipped_in_slot=None,
    )
    session.add(item)
    session.flush()
    return item


def list_stash(
    session: Session,
    player_id: int,
    *,
    slot: str | None = None,
) -> list[PlayerGearItem]:
    stmt = select(PlayerGearItem).where(
        PlayerGearItem.player_id == player_id,
        PlayerGearItem.equipped_in_slot.is_(None),
    )
    if slot is not None:
        stmt = stmt.where(PlayerGearItem.slot == slot.lower())
    stmt = stmt.order_by(PlayerGearItem.id.desc())
    return list(session.execute(stmt).scalars())


def list_all_gear_items(session: Session, player_id: int) -> list[PlayerGearItem]:
    stmt = (
        select(PlayerGearItem)
        .where(PlayerGearItem.player_id == player_id)
        .order_by(PlayerGearItem.equipped_in_slot.is_(None), PlayerGearItem.id.desc())
    )
    return list(session.execute(stmt).scalars())


def get_gear_item(session: Session, player_id: int, gear_item_id: int) -> PlayerGearItem | None:
    item = session.get(PlayerGearItem, gear_item_id)
    if item is None or item.player_id != player_id:
        return None
    return item


def format_gear_item_label(item: PlayerGearItem, *, prefix: str = "") -> str:
    name = get_item_name(item.item_id)
    path = path_label(item.gear_grade)
    stat_bits = []
    if item.stat_power:
        stat_bits.append(f"Pow {item.stat_power}")
    if item.stat_defense:
        stat_bits.append(f"Def {item.stat_defense}")
    if item.stat_fortune:
        stat_bits.append(f"Fort {item.stat_fortune}")
    if item.stat_insight:
        stat_bits.append(f"Ins {item.stat_insight}")
    stats_text = " · ".join(stat_bits) if stat_bits else "modest qi"
    worn = f" · worn {item.equipped_in_slot}" if item.equipped_in_slot else ""
    return f"{prefix}#{item.id} · {path} {name} ({stats_text}){worn}"


def equip_gear_item(
    session: Session,
    player_id: int,
    gear_item_id: int,
) -> EquipResult:
    from .equipment import get_or_create_slot

    item = get_gear_item(session, player_id, gear_item_id)
    if item is None:
        return EquipResult(False, "That gear piece is not in your stash.")

    slot = item.slot
    slot_row = get_or_create_slot(session, player_id, slot)

    if slot_row.gear_item_id is not None and slot_row.gear_item_id != item.id:
        previous = session.get(PlayerGearItem, slot_row.gear_item_id)
        if previous is not None:
            previous.equipped_in_slot = None
            session.add(previous)
    elif slot_row.gear_item_id is None and slot_row.item_id:
        pass

    item.equipped_in_slot = slot
    sync_slot_from_gear_item(slot_row, item)
    session.add(item)
    session.add(slot_row)

    name = get_item_name(item.item_id)
    path = path_label(item.gear_grade)
    return EquipResult(
        success=True,
        message=f"You wear **{name}** ({path}) in your **{slot}** slot.",
    )


def unequip_slot(session: Session, player_id: int, slot: str) -> EquipResult:
    from .equipment import get_or_create_slot

    slot = slot.lower()
    if slot not in EQUIPMENT_SLOTS:
        return EquipResult(False, f"Invalid slot. Choose: {', '.join(EQUIPMENT_SLOTS)}.")

    slot_row = get_or_create_slot(session, player_id, slot)
    if slot_row.gear_item_id is None and not slot_row.item_id:
        return EquipResult(False, f"Your **{slot}** slot is already empty.")

    if slot_row.gear_item_id is not None:
        item = session.get(PlayerGearItem, slot_row.gear_item_id)
        if item is not None:
            item.equipped_in_slot = None
            session.add(item)

    clear_slot_row(slot_row)
    session.add(slot_row)
    return EquipResult(success=True, message=f"You stow your **{slot}** gear into your stash.")


def recycle_spirit_stones_for_realm(gear_realm: int) -> int:
    cfg = _load_recycling_cfg()
    table = cfg.get("spirit_stones_by_realm", [])
    idx = max(0, min(int(gear_realm), len(table) - 1))
    return int(table[idx]) if table else 0


def recycle_gear_item(session: Session, player: Player, gear_item_id: int) -> RecycleResult:
    item = get_gear_item(session, player.id, gear_item_id)
    if item is None:
        return RecycleResult(False, "That gear piece is not in your stash.")
    if item.equipped_in_slot:
        return RecycleResult(False, f"Unequip **#{item.id}** first with **`/unequip`** or swap with **`/equip`**.")

    stones = recycle_spirit_stones_for_realm(item.gear_realm)
    affix_stones = 0
    if item.affix_id:
        affix_stones = int(_load_recycling_cfg().get("affix_stone_if_affixed", 1))
        add_item(session, player.id, "affix_stone", affix_stones)

    player.spirit_stones += stones
    session.add(player)
    session.delete(item)

    name = get_item_name(item.item_id)
    parts = [f"**{stones}** spirit stones"]
    if affix_stones:
        parts.append(f"**{affix_stones}× Affix Stone**")
    reward_text = " and ".join(parts)
    return RecycleResult(
        success=True,
        message=f"You break down **{name}** (#{gear_item_id}) into {reward_text}.",
        spirit_stones=stones,
        affix_stones=affix_stones,
    )


def gear_view_is_active(view: GearView, player_realm_index: int) -> bool:
    if not view.item_id:
        return False
    return gear_is_active(view, player_realm_index)


def stash_count(session: Session, player_id: int) -> int:
    stmt = select(PlayerGearItem).where(
        PlayerGearItem.player_id == player_id,
        PlayerGearItem.equipped_in_slot.is_(None),
    )
    return len(list(session.execute(stmt).scalars()))
