from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import discord
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InventoryItem, Player


ITEMS_PATH = Path(__file__).resolve().parent.parent / "config" / "items.json"

CATEGORY_ORDER = ("material", "pill", "manual", "key", "special")

CATEGORY_META: dict[str, tuple[str, str]] = {
    "material": ("🌿", "Materials"),
    "pill": ("💊", "Pills"),
    "manual": ("📜", "Manuals"),
    "key": ("🗝️", "Keys"),
    "special": ("✨", "Special"),
}


@dataclass(frozen=True)
class ItemDef:
    item_id: str
    name: str
    category: str
    description: str


_item_catalog: dict[str, ItemDef] | None = None


def load_item_catalog() -> dict[str, ItemDef]:
    global _item_catalog
    if _item_catalog is not None:
        return _item_catalog

    with ITEMS_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)

    catalog: dict[str, ItemDef] = {}
    for item_id, data in raw.items():
        catalog[item_id] = ItemDef(
            item_id=item_id,
            name=data["name"],
            category=data.get("category", "material"),
            description=data.get("description", ""),
        )
    _item_catalog = catalog
    return catalog


def get_item_def(item_id: str) -> ItemDef | None:
    return load_item_catalog().get(item_id)


def get_item_name(item_id: str) -> str:
    item = get_item_def(item_id)
    return item.name if item is not None else item_id.replace("_", " ").title()


def get_player_inventory(session: Session, player_id: int) -> list[InventoryItem]:
    stmt = (
        select(InventoryItem)
        .where(InventoryItem.player_id == player_id, InventoryItem.quantity > 0)
        .order_by(InventoryItem.item_id)
    )
    return list(session.execute(stmt).scalars().all())


def _get_or_create_stack(session: Session, player_id: int, item_id: str) -> InventoryItem:
    stmt = select(InventoryItem).where(
        InventoryItem.player_id == player_id,
        InventoryItem.item_id == item_id,
    )
    stack = session.execute(stmt).scalar_one_or_none()
    if stack is None:
        stack = InventoryItem(player_id=player_id, item_id=item_id, quantity=0)
        session.add(stack)
        session.flush()
    return stack


def add_item(session: Session, player_id: int, item_id: str, quantity: int) -> int:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if get_item_def(item_id) is None:
        raise ValueError(f"unknown item_id: {item_id}")

    stack = _get_or_create_stack(session, player_id, item_id)
    stack.quantity += quantity
    session.add(stack)
    return stack.quantity


def remove_item(session: Session, player_id: int, item_id: str, quantity: int) -> bool:
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    stack = _get_or_create_stack(session, player_id, item_id)
    if stack.quantity < quantity:
        return False

    stack.quantity -= quantity
    session.add(stack)
    return True


def get_item_quantity(session: Session, player_id: int, item_id: str) -> int:
    stmt = select(InventoryItem).where(
        InventoryItem.player_id == player_id,
        InventoryItem.item_id == item_id,
    )
    stack = session.execute(stmt).scalar_one_or_none()
    return 0 if stack is None else stack.quantity


def has_items(session: Session, player_id: int, required: dict[str, int]) -> bool:
    for item_id, qty in required.items():
        if get_item_quantity(session, player_id, item_id) < qty:
            return False
    return True


def _format_name_qty_block(names_and_qty: list[tuple[str, int]]) -> str:
    """Monospace inventory grid — names only, quantities aligned."""
    if not names_and_qty:
        return "_empty_"
    max_name = max(len(name) for name, _ in names_and_qty)
    lines = [f"{name.ljust(max_name)}  ×{qty}" for name, qty in names_and_qty]
    return "```\n" + "\n".join(lines) + "\n```"


def build_inventory_embed(player: Player, stacks: list[InventoryItem]) -> discord.Embed:
    catalog = load_item_catalog()

    if not stacks:
        return discord.Embed(
            title=f"🎒 {player.dao_name} — Storage Ring",
            description=(
                "Your bag is empty.\n\n"
                "Fill it through **`/gather`**, **`/hunt`**, **`/adventure`**, or **`/daily`**."
            ),
            color=discord.Color.dark_grey(),
        )

    grouped: dict[str, list[tuple[str, int]]] = {cat: [] for cat in CATEGORY_ORDER}
    other: list[tuple[str, int]] = []
    total_qty = 0

    for stack in stacks:
        item = catalog.get(stack.item_id)
        name = item.name if item is not None else stack.item_id.replace("_", " ").title()
        total_qty += stack.quantity
        category = item.category if item is not None else "material"
        row = (name, stack.quantity)
        if category in grouped:
            grouped[category].append(row)
        else:
            other.append(row)

    categories_present = sum(1 for cat in CATEGORY_ORDER if grouped[cat])
    if other:
        categories_present += 1

    embed = discord.Embed(
        title=f"🎒 {player.dao_name} — Storage Ring",
        description=(
            f"**{len(stacks)}** item type{'s' if len(stacks) != 1 else ''} · "
            f"**{total_qty}** total stack{'s' if total_qty != 1 else ''}\n"
            f"_Inspect anything with **`/item <name>`**._"
        ),
        color=discord.Color.dark_teal(),
    )

    for category in CATEGORY_ORDER:
        rows = grouped[category]
        if not rows:
            continue
        rows.sort(key=lambda row: row[0].lower())
        emoji, label = CATEGORY_META[category]
        embed.add_field(
            name=f"{emoji} {label} ({len(rows)})",
            value=_format_name_qty_block(rows),
            inline=True,
        )

    if other:
        other.sort(key=lambda row: row[0].lower())
        embed.add_field(
            name="📦 Other",
            value=_format_name_qty_block(other),
            inline=True,
        )

    embed.set_footer(text=f"{categories_present} categories · /item <name> for full details")
    return embed


def format_inventory_embed(player: Player, stacks: list[InventoryItem], **kwargs) -> tuple[str, str]:
    """Legacy helper — prefer build_inventory_embed()."""
    embed = build_inventory_embed(player, stacks)
    return embed.title or f"{player.dao_name} — Inventory", embed.description or ""
