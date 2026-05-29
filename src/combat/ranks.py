from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..inventory import get_item_name, get_item_quantity, remove_item
from ..models import Player, PlayerTechnique
from ..realms import get_technique_rank_cap
from .catalog import TechniqueDef, get_technique
from .rarity import rarity_rank
from .rules import load_combat_rules

UPGRADE_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "technique_upgrade.json"


@lru_cache(maxsize=1)
def _load_upgrade_config() -> dict:
    with UPGRADE_CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def rarity_cost_mult(rarity: str) -> float:
    return float(_load_upgrade_config().get("rarity_cost_mult", {}).get(rarity, 1.0))


def category_material(category: str) -> str:
    materials = _load_upgrade_config().get("category_materials", {})
    return str(materials.get(category, "technique_fragment"))


@dataclass(frozen=True)
class RankCost:
    stones: int
    materials: dict[str, int]


def rank_cost(tech: TechniqueDef, current_rank: int) -> RankCost:
    cfg = _load_upgrade_config()
    next_rank = current_rank + 1
    base = (
        int(cfg.get("stone_base", 20))
        + tech.min_realm * int(cfg.get("stone_realm_mult", 12))
        + rarity_rank(tech.rarity) * int(cfg.get("stone_rarity_mult", 10))
    )
    stones = int(base * (next_rank**2) * rarity_cost_mult(tech.rarity))
    material_id = category_material(tech.category)
    divisor = max(1, int(cfg.get("material_qty_divisor", 2)))
    materials: dict[str, int] = {material_id: max(1, next_rank // divisor)}
    fragment_rank = int(cfg.get("fragment_from_rank", 0))
    if fragment_rank > 0 and next_rank >= fragment_rank:
        materials["technique_fragment"] = materials.get("technique_fragment", 0) + max(1, next_rank // 3)
    return RankCost(stones=stones, materials=materials)


def _player_technique(session: Session, player_id: int, technique_id: str) -> PlayerTechnique | None:
    stmt = select(PlayerTechnique).where(
        PlayerTechnique.player_id == player_id,
        PlayerTechnique.technique_id == technique_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def technique_rank_multiplier(rank: int) -> float:
    rank = max(1, min(10, rank))
    return 1.0 + (rank - 1) * 0.035


def rank_evolution_summary(tech: TechniqueDef, rank: int) -> str:
    if rank >= 10:
        return tech.rank_effects.get(10, {}).get("summary", "the art reaches its perfected form")
    if rank >= 5:
        return tech.rank_effects.get(5, {}).get("summary", "the art's pattern sharpens")
    return ""


def upgrade_technique_rank(session: Session, player: Player, technique_id: str) -> tuple[bool, str]:
    if not load_combat_rules().enabled("technique_ranks"):
        return False, "Technique ranks are dormant in this realm."
    tech = get_technique(technique_id)
    if tech is None:
        return False, "That technique is unknown."
    row = _player_technique(session, player.id, technique_id)
    if row is None:
        return False, f"You have not learned **{tech.name}** yet."
    current = max(1, int(row.rank or 1))
    cap = get_technique_rank_cap(player.realm_index)
    if current >= cap:
        return False, f"**{tech.name}** is at your realm's rank limit (**{cap}**)."
    if current >= 10:
        return False, f"**{tech.name}** has reached rank **10**."

    cost = rank_cost(tech, current)
    material_missing = {
        item_id: qty
        for item_id, qty in cost.materials.items()
        if get_item_quantity(session, player.id, item_id) < qty
    }
    if player.spirit_stones < cost.stones:
        if material_missing:
            from ..drop_sources import format_missing_materials_message

            msg = format_missing_materials_message(
                session, player.id, material_missing, action="rank_upgrade"
            )
            return False, msg + f"\nAlso need **{cost.stones}** spirit stones (you hold **{player.spirit_stones}**)."
        return (
            False,
            f"Tempering **{tech.name}** needs **{cost.stones}** spirit stones "
            f"(you hold **{player.spirit_stones}**).",
        )
    if material_missing:
        from ..drop_sources import format_missing_materials_message

        return False, format_missing_materials_message(
            session, player.id, material_missing, action="rank_upgrade"
        )

    player.spirit_stones -= cost.stones
    for item_id, qty in cost.materials.items():
        remove_item(session, player.id, item_id, qty)
    row.rank = current + 1
    session.add(player)
    session.add(row)
    message = f"**{tech.name}** rises to rank **{row.rank}**."
    evolution = rank_evolution_summary(tech, row.rank)
    if evolution:
        message += f" {evolution.capitalize()}."
    return True, message


def invalidate_rank_config_cache() -> None:
    _load_upgrade_config.cache_clear()
