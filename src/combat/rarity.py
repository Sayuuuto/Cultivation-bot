from __future__ import annotations

RARITY_ORDER: dict[str, int] = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
    "legendary": 3,
}

RARITY_LABEL: dict[str, str] = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "legendary": "Legendary",
}

RARITY_EMOJI: dict[str, str] = {
    "common": "⚪",
    "uncommon": "🟢",
    "rare": "🔵",
    "legendary": "🟡",
}

# Active technique damage multiplier by rarity (passives use effect values in config).
RARITY_DAMAGE_MULT: dict[str, float] = {
    "common": 1.0,
    "uncommon": 1.06,
    "rare": 1.12,
    "legendary": 1.20,
}

# Max rarity allowed per acquisition channel.
SOURCE_MAX_RARITY: dict[str, str] = {
    "shop_direct": "common",
    "shop_gamble": "uncommon",
    "craft_mortal": "common",
    "craft_earth": "uncommon",
    "cultivate": "common",
    "breakthrough_neutral": "rare",
    "breakthrough_aligned": "rare",
    "adventure_elder": "uncommon",
    "adventure_inheritance": "rare",
    "adventure_moral": "legendary",
    "hunt_elite": "rare",
    "dungeon_weekly": "legendary",
    "sect_shop": "uncommon",
}


def normalize_rarity(raw: str | None) -> str:
    value = (raw or "common").lower()
    return value if value in RARITY_ORDER else "common"


def rarity_rank(rarity: str) -> int:
    return RARITY_ORDER.get(normalize_rarity(rarity), 0)


def rarity_damage_multiplier(rarity: str) -> float:
    return RARITY_DAMAGE_MULT.get(normalize_rarity(rarity), 1.0)


def rarity_at_most(rarity: str, max_rarity: str) -> bool:
    return rarity_rank(rarity) <= rarity_rank(max_rarity)


def filter_manual_pool(
    pool: list[tuple[str, int]],
    max_rarity: str,
    *,
    get_rarity_for_manual,
) -> list[tuple[str, int]]:
    """Drop manuals above max_rarity; re-roll weights unchanged."""
    filtered: list[tuple[str, int]] = []
    for item_id, weight in pool:
        rarity = get_rarity_for_manual(item_id)
        if rarity is not None and rarity_at_most(rarity, max_rarity):
            filtered.append((item_id, weight))
    return filtered
