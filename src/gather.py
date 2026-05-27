from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .combat_stats import compute_combat_stats, gather_quantity_bonus, gather_rare_bonus
from .loot import load_drop_rarity_config, parse_loot_table, roll_bonus_loot, roll_weighted_loot_pool
from .content import get_area
from .inventory import add_item, get_item_name
from .models import Player

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "gather_nodes.json"

_gather_config: dict | None = None


@dataclass(frozen=True)
class GatherNode:
    item_id: str
    weight: int
    min_qty: int
    max_qty: int
    message: str = ""
    rarity: str = "common"


@dataclass(frozen=True)
class GatherAreaDef:
    area_id: str
    flavor: str
    nodes: tuple[GatherNode, ...]
    rare_node_chance: float
    rare_nodes: tuple[GatherNode, ...]


@dataclass(frozen=True)
class GatherResult:
    success: bool
    area_name: str
    drops: dict[str, int]
    messages: list[str]
    rare_message: str | None = None


def _load_gather_config() -> dict[str, GatherAreaDef]:
    global _gather_config
    if _gather_config is None:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            raw = json.load(f)
        parsed: dict[str, GatherAreaDef] = {}
        for area_id, data in raw.items():
            nodes = tuple(
                GatherNode(
                    item_id=n["item_id"],
                    weight=n["weight"],
                    min_qty=n["min"],
                    max_qty=n["max"],
                    rarity=str(n.get("rarity", "common")),
                )
                for n in data["nodes"]
            )
            rare_nodes = tuple(
                GatherNode(
                    item_id=n["item_id"],
                    weight=n.get("weight", 1),
                    min_qty=n["min"],
                    max_qty=n["max"],
                    message=n.get("message", ""),
                    rarity=str(n.get("rarity", "uncommon")),
                )
                for n in data.get("rare_nodes", [])
            )
            parsed[area_id] = GatherAreaDef(
                area_id=area_id,
                flavor=data.get("flavor", "You search the wilds for materials."),
                nodes=nodes,
                rare_node_chance=float(data.get("rare_node_chance", 0.05)),
                rare_nodes=rare_nodes,
            )
        _gather_config = parsed
    return _gather_config


def get_gather_areas() -> dict[str, GatherAreaDef]:
    return _load_gather_config()


def get_gather_area(area_id: str) -> GatherAreaDef | None:
    return get_gather_areas().get(area_id)


def _validate_area(player: Player, area_id: str) -> tuple[str | None, str | None]:
    area = get_area(area_id)
    if area is None:
        return None, "That region is unknown."
    if player.realm_index < area.min_realm:
        return None, f"You are not ready for **{area.name}**. {area.recommended_text} recommended."
    return area.name, None


def _pick_weighted(nodes: tuple[GatherNode, ...], rng: random.Random) -> GatherNode | None:
    if not nodes:
        return None
    total = sum(n.weight for n in nodes)
    if total <= 0:
        return None
    roll = rng.randint(1, total)
    cumulative = 0
    for node in nodes:
        cumulative += node.weight
        if roll <= cumulative:
            return node
    return nodes[-1]


def _roll_qty(node: GatherNode, qty_mult: float, rng: random.Random) -> int:
    base = rng.randint(node.min_qty, node.max_qty)
    return max(1, int(base * qty_mult))


def run_gather(
    session: Session,
    player: Player,
    area_id: str,
    rng: random.Random | None = None,
) -> GatherResult:
    rng = rng or random.Random()
    area_name, err = _validate_area(player, area_id)
    if err:
        return GatherResult(success=False, area_name=area_id, drops={}, messages=[err])

    gather_def = get_gather_area(area_id)
    if gather_def is None:
        return GatherResult(
            success=False,
            area_name=area_name or area_id,
            drops={},
            messages=["No gather nodes are configured for this region."],
        )

    area = get_area(area_id)
    area_min_realm = area.min_realm if area is not None else 0
    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    qty_mult = gather_quantity_bonus(stats.comprehension)
    drop_cfg = load_drop_rarity_config()

    node = _pick_weighted(gather_def.nodes, rng)
    if node is None:
        return GatherResult(
            success=False,
            area_name=area_name or area_id,
            drops={},
            messages=["You find nothing worth harvesting."],
        )

    qty = _roll_qty(node, qty_mult, rng)
    add_item(session, player.id, node.item_id, qty)
    drops = {node.item_id: qty}

    messages = [
        gather_def.flavor,
        f"You gather **{get_item_name(node.item_id)}** ×{qty}.",
    ]

    node_table = parse_loot_table(
        [
            {
                "item_id": n.item_id,
                "rarity": n.rarity,
                "min": n.min_qty,
                "max": n.max_qty,
            }
            for n in gather_def.nodes
        ]
    )
    bonus_chance = drop_cfg.gather_bonus_roll_chance + gather_rare_bonus(stats.luck, mod.drop_luck)
    if node_table and rng.random() < bonus_chance:
        bonus = roll_bonus_loot(
            node_table,
            rng,
            rolls=drop_cfg.gather_bonus_roll_count,
            luck=stats.luck,
            drop_luck=mod.drop_luck,
            player_realm_index=player.realm_index,
            area_min_realm=area_min_realm,
            qty_mult=qty_mult,
        )
        for item_id, bonus_qty in bonus.items():
            add_item(session, player.id, item_id, bonus_qty)
            drops[item_id] = drops.get(item_id, 0) + bonus_qty
        if bonus:
            extras = ", ".join(f"**{get_item_name(i)}** ×{q}" for i, q in bonus.items())
            messages.append(f"Fortune smiles — extra haul: {extras}.")

    rare_message: str | None = None
    rare_chance = gather_def.rare_node_chance + gather_rare_bonus(stats.luck, mod.drop_luck)
    if gather_def.rare_nodes and rng.random() < rare_chance:
        rare_table = parse_loot_table(
            [
                {
                    "item_id": n.item_id,
                    "rarity": n.rarity,
                    "min": n.min_qty,
                    "max": n.max_qty,
                }
                for n in gather_def.rare_nodes
            ]
        )
        rolled = roll_weighted_loot_pool(
            rare_table,
            rng,
            luck=stats.luck,
            drop_luck=mod.drop_luck,
            player_realm_index=player.realm_index,
            area_min_realm=area_min_realm,
            qty_mult=qty_mult * 1.25,
        )
        if rolled is not None:
            rare_item, rare_qty = rolled
            add_item(session, player.id, rare_item, rare_qty)
            drops[rare_item] = drops.get(rare_item, 0) + rare_qty
            rare_node = next((n for n in gather_def.rare_nodes if n.item_id == rare_item), None)
            rare_message = (
                (rare_node.message if rare_node else "")
                or f"A rare find: **{get_item_name(rare_item)}** ×{rare_qty}."
            )
            messages.append(rare_message)

    from .game_sects import on_sect_activity

    primary_item = node.item_id
    sect_msgs = on_sect_activity(
        session,
        player,
        "gather",
        area_id=area_id,
        item_id=primary_item,
    )
    messages.extend(sect_msgs)

    from .foundation import roll_gather_meridian_insight

    meridian_msg = roll_gather_meridian_insight(player, stats.comprehension, rng)
    if meridian_msg:
        messages.append(meridian_msg)

    return GatherResult(
        success=True,
        area_name=area_name or area_id,
        drops=drops,
        messages=messages,
        rare_message=rare_message,
    )
