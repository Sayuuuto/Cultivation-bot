from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .combat.catalog import get_technique_by_manual, load_technique_catalog
from .combat.rarity import SOURCE_MAX_RARITY, rarity_rank
from .karma import karma_tier, manual_weight_multiplier
from .combat.loadout import get_learned_technique_ids
from .drop_sources import format_missing_materials_message
from .inventory import add_item, get_item_name, has_items, remove_item
from .models import DungeonRun, Player, utcnow

MANUAL_POOLS_PATH = Path(__file__).resolve().parent.parent / "config" / "manual_pools.json"

FRAGMENT_ITEM_ID = "technique_fragment"
WEEKLY_MANUAL_DAYS = 7

MANUAL_CRAFT_INPUTS = {
    FRAGMENT_ITEM_ID: 3,
    "blank_scroll": 1,
    "spirit_ink": 1,
}

CULTIVATE_FRAGMENT_CHANCE = 0.04
CULTIVATE_MANUAL_CHANCE = 0.005
BREAKTHROUGH_FRAGMENT_CHANCE = 0.15
BREAKTHROUGH_MANUAL_CHANCE = 0.08
BREAKTHROUGH_FAIL_FRAGMENT_CHANCE = 0.05

RARE_EVENT_META_KEYS = frozenset(
    {"effect", "charges", "hours", "spirit_stones", "manual_pool", "manual_chance"}
)

_pools: dict[str, list[tuple[str, int]]] | None = None


def load_manual_pools() -> dict[str, list[tuple[str, int]]]:
    global _pools
    if _pools is not None:
        return _pools

    with MANUAL_POOLS_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)

    parsed: dict[str, list[tuple[str, int]]] = {}
    for pool_id, data in raw.items():
        entries = [(entry["item_id"], int(entry["weight"])) for entry in data.get("manuals", [])]
        if entries:
            parsed[pool_id] = entries
    _pools = parsed
    return parsed


def invalidate_manual_pool_cache() -> None:
    global _pools
    _pools = None


def _pick_weighted_manual(pool: list[tuple[str, int]], rng: random.Random) -> str | None:
    if not pool:
        return None
    total = sum(weight for _, weight in pool)
    roll = rng.randint(1, total)
    acc = 0
    for item_id, weight in pool:
        acc += weight
        if roll <= acc:
            return item_id
    return pool[-1][0]


def pick_manual_from_pool(
    pool_id: str,
    rng: random.Random,
    *,
    session: Session | None = None,
    player_id: int | None = None,
    prefer_unlearned: bool = True,
    karma: int = 0,
    max_rarity: str | None = None,
) -> str | None:
    pools = load_manual_pools()
    pool = list(pools.get(pool_id, []))
    if not pool:
        return None

    if max_rarity is not None:
        cap = rarity_rank(max_rarity)
        pool = [
            (item_id, weight)
            for item_id, weight in pool
            if (tech := get_technique_by_manual(item_id)) is not None
            and rarity_rank(tech.rarity) <= cap
        ]
        if not pool:
            return None

    if prefer_unlearned and session is not None and player_id is not None:
        learned = get_learned_technique_ids(session, player_id)
        unlearned = [
            (item_id, weight)
            for item_id, weight in pool
            if (tech := get_technique_by_manual(item_id)) is not None
            and tech.technique_id not in learned
        ]
        if unlearned:
            pool = unlearned

    weighted: list[tuple[str, int]] = []
    for item_id, weight in pool:
        tech = get_technique_by_manual(item_id)
        alignment = tech.alignment if tech else "neutral"
        adjusted = max(1, int(weight * manual_weight_multiplier(karma, alignment)))
        weighted.append((item_id, adjusted))

    return _pick_weighted_manual(weighted, rng)


def grant_manual_drop(
    session: Session,
    player_id: int,
    manual_item_id: str,
    drops: dict[str, int],
) -> str:
    tech = get_technique_by_manual(manual_item_id)
    if tech is not None and tech.technique_id in get_learned_technique_ids(session, player_id):
        drops[FRAGMENT_ITEM_ID] = drops.get(FRAGMENT_ITEM_ID, 0) + 2
        return (
            f"Your dao already holds **{tech.name}** — the imprint crumbles into "
            f"**{get_item_name(FRAGMENT_ITEM_ID)}**."
        )

    drops[manual_item_id] = drops.get(manual_item_id, 0) + 1
    return f"You obtained **{get_item_name(manual_item_id)}**."


def roll_manual_pool_reward(
    session: Session,
    player_id: int,
    pool_id: str,
    rng: random.Random,
    drops: dict[str, int],
    *,
    chance: float = 1.0,
) -> str | None:
    if chance < 1.0 and rng.random() > chance:
        return None
    player = session.get(Player, player_id)
    karma = player.karma if player is not None else 0
    manual_id = pick_manual_from_pool(
        pool_id, rng, session=session, player_id=player_id, karma=karma
    )
    if manual_id is None:
        return None
    return grant_manual_drop(session, player_id, manual_id, drops)


def normalize_manual_drops(session: Session, player_id: int, drops: dict[str, int]) -> dict[str, int]:
    """Convert duplicate manuals in a drop dict into fragments."""
    normalized: dict[str, int] = {}
    for item_id, qty in drops.items():
        tech = get_technique_by_manual(item_id)
        if tech is not None and tech.technique_id in get_learned_technique_ids(session, player_id):
            normalized[FRAGMENT_ITEM_ID] = normalized.get(FRAGMENT_ITEM_ID, 0) + qty * 2
            continue
        normalized[item_id] = normalized.get(item_id, 0) + qty
    return normalized


def apply_rare_event_manual_reward(
    session: Session,
    player: Player,
    rewards: dict[str, int | str | float],
    drops: dict[str, int],
    messages: list[str],
    rng: random.Random,
) -> None:
    pool_id = rewards.get("manual_pool")
    if not pool_id:
        return
    chance = float(rewards.get("manual_chance", 1.0))
    note = roll_manual_pool_reward(session, player.id, str(pool_id), rng, drops, chance=chance)
    if note:
        messages.append(note)


def had_weekly_dungeon_manual(session: Session, player_id: int, dungeon_id: str) -> bool:
    cutoff = utcnow() - timedelta(days=WEEKLY_MANUAL_DAYS)
    stmt = (
        select(DungeonRun)
        .where(
            DungeonRun.player_id == player_id,
            DungeonRun.dungeon_id == dungeon_id,
            DungeonRun.outcome == "success",
            DungeonRun.created_at >= cutoff,
        )
        .order_by(DungeonRun.created_at.desc())
    )
    for run in session.execute(stmt).scalars():
        try:
            payload = json.loads(run.rewards_json or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("weekly_manual"):
            return True
    return False


def roll_weekly_dungeon_manual(
    session: Session,
    player: Player,
    dungeon_id: str,
    rng: random.Random,
    drops: dict[str, int],
) -> tuple[str | None, str | None]:
    if had_weekly_dungeon_manual(session, player.id, dungeon_id):
        return None, None
    manual_id = pick_manual_from_pool(
        "dungeon_earth", rng, session=session, player_id=player.id, karma=player.karma
    )
    if manual_id is None:
        return None, None
    note = grant_manual_drop(session, player.id, manual_id, drops)
    return manual_id, note


def roll_cultivate_enlightenment(
    session: Session,
    player: Player,
    rng: random.Random,
) -> tuple[dict[str, int], str]:
    drops: dict[str, int] = {}
    messages: list[str] = []

    if rng.random() <= CULTIVATE_FRAGMENT_CHANCE:
        drops[FRAGMENT_ITEM_ID] = 1
        messages.append("A line of scripture flashes in your mind — a **Technique Fragment** coalesces.")

    if rng.random() <= CULTIVATE_MANUAL_CHANCE:
        note = roll_manual_pool_reward(
            session, player.id, "cultivate_enlightenment", rng, drops
        )
        if note:
            messages.append(note)

    if not drops:
        return {}, ""
    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)
    return drops, " ".join(messages)


def roll_breakthrough_enlightenment(
    session: Session,
    player: Player,
    rng: random.Random,
    *,
    success: bool,
) -> tuple[dict[str, int], str]:
    drops: dict[str, int] = {}
    messages: list[str] = []

    if success:
        if rng.random() <= BREAKTHROUGH_FRAGMENT_CHANCE:
            drops[FRAGMENT_ITEM_ID] = 1
            messages.append("Heaven's insight leaves a **Technique Fragment** in your palm.")
        if rng.random() <= BREAKTHROUGH_MANUAL_CHANCE:
            pool_id = breakthrough_pool_for_karma(player.karma, realm_index=player.realm_index)
            note = roll_manual_pool_reward(session, player.id, pool_id, rng, drops)
            if note:
                messages.append(note)
    elif karma_tier(player.karma) == "demonic" and rng.random() <= BREAKTHROUGH_FAIL_FRAGMENT_CHANCE:
        drops[FRAGMENT_ITEM_ID] = 1
        messages.append("In the backlash you glimpse forbidden script — a **Technique Fragment** remains.")

    if not drops:
        return {}, ""
    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)
    return drops, " ".join(messages)


def craft_pool_for_realm(realm_index: int) -> str:
    return "craft_earth" if realm_index >= 1 else "craft_mortal"


def breakthrough_pool_for_karma(karma: int, *, realm_index: int) -> str:
    tier = karma_tier(karma)
    if tier == "righteous":
        return "righteous_breakthrough"
    if tier == "demonic":
        return "demonic_breakthrough"
    return "breakthrough_success" if realm_index >= 1 else "cultivate_enlightenment"


@dataclass
class ManualCraftResult:
    success: bool
    crafted: dict[str, int] = field(default_factory=dict)
    message: str = ""


def craft_manual_from_fragments(
    session: Session,
    player: Player,
    rng: random.Random | None = None,
) -> ManualCraftResult:
    rng = rng or random.Random()
    if not has_items(session, player.id, MANUAL_CRAFT_INPUTS):
        return ManualCraftResult(
            success=False,
            message=format_missing_materials_message(
                session, player.id, MANUAL_CRAFT_INPUTS, action="manual"
            ),
        )

    for item_id, qty in MANUAL_CRAFT_INPUTS.items():
        remove_item(session, player.id, item_id, qty)

    pool_id = craft_pool_for_realm(player.realm_index)
    drops: dict[str, int] = {}
    note = roll_manual_pool_reward(session, player.id, pool_id, rng, drops)
    if not drops:
        return ManualCraftResult(success=False, message="The binding failed — no manual formed.")

    crafted: dict[str, int] = {}
    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)
        crafted[item_id] = qty

    manual_name = get_item_name(next(iter(crafted)))
    message = f"You bind the fragments into **{manual_name}**."
    if note:
        message = note
    return ManualCraftResult(success=True, crafted=crafted, message=message)


def roll_shop_unidentified_manual(
    session: Session,
    player: Player,
    rng: random.Random,
) -> tuple[str | None, str]:
    manual_id = pick_manual_from_pool(
        "shop_unidentified",
        rng,
        session=session,
        player_id=player.id,
        karma=player.karma,
        max_rarity=SOURCE_MAX_RARITY["shop_gamble"],
    )
    if manual_id is None:
        return None, "The scroll crumbles to ash — no technique answers."

    drops: dict[str, int] = {}
    note = grant_manual_drop(session, player.id, manual_id, drops)
    add_item(session, player.id, manual_id, 1)
    return manual_id, note or f"The scroll reveals **{get_item_name(manual_id)}**."
