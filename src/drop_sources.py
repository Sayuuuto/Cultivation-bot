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
        "blackwind_key",
        DropSource(label="Alchemy recipe", via="`/craft key`"),
    )

    _item_sources = index
    return index


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
    lines: list[str] = []
    for item_id, need in sorted(inputs.items()):
        have = get_item_quantity(session, player_id, item_id)
        if have >= need:
            continue
        name = get_item_name(item_id)
        lines.append(f"• **{name}** — you have **{have}/{need}**\n  ↳ {format_item_drop_hints(item_id)}")

    if not lines:
        return "You are missing materials."

    if action == "forge":
        header = "You don't have enough materials to **forge** this piece."
    elif action == "key":
        header = "You don't have enough materials to **craft this key**."
    else:
        header = "You don't have enough materials to **craft** this recipe."

    footer = "Use **`/areas`** to compare zones, or **`/recipes`** to plan what to farm."
    return f"{header}\n\n" + "\n".join(lines) + f"\n\n{footer}"


def invalidate_drop_source_cache() -> None:
    global _item_sources
    _item_sources = None
