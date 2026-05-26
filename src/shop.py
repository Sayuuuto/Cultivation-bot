from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import discord
from sqlalchemy.orm import Session

from .equipment import get_or_create_slot
from .inventory import add_item, get_item_name
from .models import Player

SHOP_PATH = Path(__file__).resolve().parent.parent / "config" / "shop.json"

CATEGORY_ORDER = ("pill", "supply", "equipment")
CATEGORY_TITLES = {
    "pill": "Pills & elixirs",
    "supply": "Supplies",
    "equipment": "Forged gear (fixed stats)",
}


@dataclass(frozen=True)
class ShopListing:
    shop_id: str
    name: str
    price: int
    category: str
    description: str
    listing_type: str  # item | equipment
    item_id: str | None = None
    quantity: int = 1
    slot: str | None = None
    stats: dict[str, int] | None = None


_shop_catalog: dict[str, ShopListing] | None = None


def load_shop_catalog() -> dict[str, ShopListing]:
    global _shop_catalog
    if _shop_catalog is not None:
        return _shop_catalog

    with SHOP_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)

    catalog: dict[str, ShopListing] = {}
    for shop_id, data in raw.items():
        listing_type = data.get("type", "item")
        catalog[shop_id] = ShopListing(
            shop_id=shop_id,
            name=data["name"],
            price=int(data["price"]),
            category=data.get("category", "supply"),
            description=data.get("description", ""),
            listing_type=listing_type,
            item_id=data.get("item_id"),
            quantity=int(data.get("quantity", 1)),
            slot=data.get("slot"),
            stats=data.get("stats"),
        )
    _shop_catalog = catalog
    return catalog


def get_shop_listing(shop_id: str) -> ShopListing | None:
    return load_shop_catalog().get(shop_id)


def list_shop_listings() -> list[ShopListing]:
    catalog = load_shop_catalog()
    order = {cat: idx for idx, cat in enumerate(CATEGORY_ORDER)}
    return sorted(
        catalog.values(),
        key=lambda row: (order.get(row.category, 99), row.price, row.name.lower()),
    )


def _format_stats(stats: dict[str, int] | None) -> str:
    if not stats:
        return ""
    bits = [f"{key.title()} {value}" for key, value in stats.items() if value > 0]
    return " · ".join(bits) if bits else "modest qi"


def build_shop_embed(player: Player) -> discord.Embed:
    embed = discord.Embed(
        title="Spirit Stone Shop",
        description=(
            f"Your balance: **{player.spirit_stones}** spirit stones.\n"
            "Buy with **`/shop buy item:<name>`** · haste pills stack with `/use` · "
            "gear replaces that slot with fixed shop stats."
        ),
        color=discord.Color.gold(),
    )

    grouped: dict[str, list[str]] = {cat: [] for cat in CATEGORY_ORDER}
    for listing in list_shop_listings():
        line = f"• **{listing.name}** — **{listing.price}** stones\n  _{listing.description}_"
        if listing.listing_type == "equipment" and listing.stats:
            line += f"\n  Stats: {_format_stats(listing.stats)}"
        grouped.setdefault(listing.category, []).append(line)

    for category in CATEGORY_ORDER:
        lines = grouped.get(category, [])
        if lines:
            embed.add_field(
                name=CATEGORY_TITLES.get(category, category.title()),
                value="\n".join(lines),
                inline=False,
            )

    embed.set_footer(text="Example: /shop buy item:Void Pulse Pill")
    return embed


def buy_from_shop(
    session: Session,
    player: Player,
    shop_id: str,
    quantity: int = 1,
) -> tuple[bool, str]:
    listing = get_shop_listing(shop_id)
    if listing is None:
        return False, "That item is not sold here."

    if quantity < 1:
        return False, "Quantity must be at least 1."

    if listing.listing_type == "equipment":
        quantity = 1

    total_cost = listing.price * quantity
    if player.spirit_stones < total_cost:
        return False, (
            f"You need **{total_cost}** spirit stones (you have **{player.spirit_stones}**). "
            "Try **`/daily`**, **`/cultivate`**, **`/adventure`**, or **`/duel`**."
        )

    if listing.listing_type == "equipment":
        assert listing.slot and listing.item_id and listing.stats is not None
        row = get_or_create_slot(session, player.id, listing.slot)
        row.item_id = listing.item_id
        row.stat_power = int(listing.stats.get("power", 0))
        row.stat_defense = int(listing.stats.get("defense", 0))
        row.stat_fortune = int(listing.stats.get("fortune", 0))
        row.stat_insight = int(listing.stats.get("insight", 0))
        session.add(row)
        player.spirit_stones -= total_cost
        session.add(player)
        stats_text = _format_stats(listing.stats)
        return True, (
            f"You purchase **{listing.name}** for **{total_cost}** spirit stones. "
            f"It is equipped in your **{listing.slot}** slot ({stats_text}). "
            f"Balance: **{player.spirit_stones}** stones."
        )

    assert listing.item_id is not None
    add_item(session, player.id, listing.item_id, listing.quantity * quantity)
    player.spirit_stones -= total_cost
    session.add(player)
    item_name = get_item_name(listing.item_id)
    qty = listing.quantity * quantity
    return True, (
        f"You purchase **{qty}× {item_name}** for **{total_cost}** spirit stones. "
        f"Use **`/use`** on pills or check **`/inventory`**. "
        f"Balance: **{player.spirit_stones}** stones."
    )


def resolve_shop_id(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None

    catalog = load_shop_catalog()
    if text in catalog:
        return text

    lower = text.lower()
    for shop_id, listing in catalog.items():
        if listing.name.lower() == lower:
            return shop_id

    matches: list[str] = []
    tokens = [part for part in lower.replace("_", " ").split() if part]
    for shop_id, listing in catalog.items():
        haystack = f"{shop_id.replace('_', ' ')} {listing.name}".lower()
        if tokens:
            if all(token in haystack for token in tokens):
                matches.append(shop_id)
        elif lower in haystack:
            matches.append(shop_id)

    if len(matches) == 1:
        return matches[0]
    return None
