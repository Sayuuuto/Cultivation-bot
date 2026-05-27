from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .content import get_areas, get_dungeons, load_all_content
from .inventory import get_item_name, get_item_quantity


@dataclass(frozen=True)
class DropSource:
    label: str
    via: str  # short command hint


_item_sources: dict[str, list[DropSource]] | None = None


def _add_source(index: dict[str, list[DropSource]], item_id: str, source: DropSource) -> None:
    bucket = index.setdefault(item_id, [])
    if source not in bucket:
        bucket.append(source)


def _build_item_sources() -> dict[str, list[DropSource]]:
    global _item_sources
    if _item_sources is not None:
        return _item_sources

    load_all_content()
    index: dict[str, list[DropSource]] = {}

    for area in get_areas().values():
        adventure = DropSource(label=area.name, via="`/adventure`")
        for drop in area.drops:
            _add_source(index, drop.item_id, adventure)

    from .gather import get_gather_areas
    from .hunt import get_hunt_areas

    for area_id, gather_def in get_gather_areas().items():
        area = get_areas().get(area_id)
        label = area.name if area is not None else area_id
        gather = DropSource(label=label, via="`/gather`")
        for node in gather_def.nodes + gather_def.rare_nodes:
            _add_source(index, node.item_id, gather)

    for area_id, hunt_def in get_hunt_areas().items():
        area = get_areas().get(area_id)
        label = area.name if area is not None else area_id
        hunt = DropSource(label=label, via="`/hunt`")
        for beast in hunt_def.beasts:
            for drop in beast.drops:
                _add_source(index, drop.item_id, hunt)

    from .adventure import RARE_EVENT_REWARDS

    for area in get_areas().values():
        for event in area.rare_events:
            rewards = RARE_EVENT_REWARDS.get(event.id, {})
            for key in rewards:
                if key in ("effect", "charges", "hours", "spirit_stones"):
                    continue
                _add_source(
                    index,
                    key,
                    DropSource(label=f"{area.name} (rare event)", via="`/adventure`"),
                )

    for dungeon in get_dungeons().values():
        for drop in dungeon.guaranteed_drops:
            _add_source(
                index,
                drop.item_id,
                DropSource(label=f"{dungeon.name} (guaranteed)", via="`/dungeon`"),
            )
        for drop in dungeon.bonus_drops:
            _add_source(
                index,
                drop.item_id,
                DropSource(label=f"{dungeon.name} (bonus loot)", via="`/dungeon`"),
            )
        _add_source(
            index,
            "manual_iron_cleave",
            DropSource(label=f"{dungeon.name} (weekly boss manual)", via="`/dungeon`"),
        )

    from .shop import load_shop_catalog

    for listing in load_shop_catalog().values():
        if listing.listing_type in {"item", "manual_gamble"} and listing.item_id:
            _add_source(
                index,
                listing.item_id,
                DropSource(label="Spirit Stone Shop", via="`/shop buy`"),
            )
        elif listing.listing_type == "manual_gamble":
            for manual_id in _manual_item_ids():
                _add_source(
                    index,
                    manual_id,
                    DropSource(label="Unidentified Scroll (shop gamble)", via="`/shop buy`"),
                )

    from .manuals import FRAGMENT_ITEM_ID, MANUAL_CRAFT_INPUTS

    _add_source(
        index,
        FRAGMENT_ITEM_ID,
        DropSource(label="Enlightenment & hunts", via="`/cultivate` · `/hunt` · `/adventure`"),
    )
    for item_id in MANUAL_CRAFT_INPUTS:
        if item_id != FRAGMENT_ITEM_ID:
            _add_source(
                index,
                item_id,
                DropSource(label="Gathering & rare events", via="`/gather` · `/adventure`"),
            )
    _add_source(
        index,
        "script_shard",
        DropSource(label="Moonwell Ruins", via="`/gather` · `/adventure`"),
    )
    for manual_id in _manual_item_ids():
        _add_source(
            index,
            manual_id,
            DropSource(label="Enlightenment", via="`/cultivate` · `/breakthrough`"),
        )

    _add_source(
        index,
        "blackwind_key",
        DropSource(label="Alchemy recipe", via="`/craft key`"),
    )

    _item_sources = index
    return index


def _manual_item_ids() -> list[str]:
    from .combat.catalog import load_technique_catalog

    return [
        tech.manual_item_id
        for tech in load_technique_catalog().values()
        if tech.manual_item_id
    ]


def get_drop_sources(item_id: str) -> list[DropSource]:
    return list(_build_item_sources().get(item_id, []))


def format_item_drop_hints(item_id: str) -> str:
    sources = get_drop_sources(item_id)
    if not sources:
        return "Check **`/areas`**, **`/recipes`**, or **`/dungeon`**."
    parts = [f"**{src.label}** — {src.via}" for src in sources]
    return " · ".join(parts)


def format_missing_materials_message(
    session: Session,
    player_id: int,
    inputs: dict[str, int],
    *,
    action: str = "craft",
) -> str:
    """Build a player-facing message listing shortages and where to farm."""
    ready: list[str] = []
    missing: list[str] = []
    for item_id, need in sorted(inputs.items()):
        have = get_item_quantity(session, player_id, item_id)
        name = get_item_name(item_id)
        if have >= need:
            ready.append(f"✓ **{name}** — {have}/{need}")
            continue
        missing.append(f"• **{name}** — you have **{have}/{need}**\n  ↳ {format_item_drop_hints(item_id)}")

    if not missing:
        return "You are missing materials."

    if action == "forge":
        header = "You don't have enough materials to **forge** this piece."
    elif action == "key":
        header = "You don't have enough materials to **craft this key**."
    elif action == "manual":
        header = "To **bind a technique manual** (`/craft manual`), you still need:"
    else:
        header = "You don't have enough materials to **craft** this recipe."

    parts = [header]
    if ready:
        parts.append("\n**Already have:**\n" + "\n".join(ready))
    parts.append("\n**Still need:**\n" + "\n".join(missing))
    parts.append("\nUse **`/item <name>`** for details · **`/shop buy`** for blank scrolls · **`/gather`** for spirit ink.")
    return "\n".join(parts)


def invalidate_drop_source_cache() -> None:
    global _item_sources
    _item_sources = None
