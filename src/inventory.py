from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InventoryItem, Player


ITEMS_PATH = Path(__file__).resolve().parent.parent / "config" / "items.json"

CATEGORY_ORDER = ("material", "pill", "key", "special")


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


def format_inventory_embed(player: Player, stacks: list[InventoryItem]) -> tuple[str, str]:
    catalog = load_item_catalog()

    if not stacks:
        return (
            f"{player.dao_name} — Inventory",
            "Your storage ring is empty. Venture into the world to gather materials.",
        )

    grouped: dict[str, list[str]] = {cat: [] for cat in CATEGORY_ORDER}
    other: list[str] = []

    for stack in stacks:
        item = catalog.get(stack.item_id)
        label = item.name if item is not None else stack.item_id
        line = f"• {label} ×{stack.quantity}"
        category = item.category if item is not None else "material"
        if category in grouped:
            grouped[category].append(line)
        else:
            other.append(line)

    sections: list[str] = []
    category_titles = {
        "material": "Materials",
        "pill": "Pills",
        "key": "Keys",
        "special": "Special",
    }
    for category in CATEGORY_ORDER:
        lines = grouped[category]
        if lines:
            sections.append(f"**{category_titles[category]}**\n" + "\n".join(lines))

    if other:
        sections.append("**Other**\n" + "\n".join(other))

    return f"{player.dao_name} — Inventory", "\n\n".join(sections)
