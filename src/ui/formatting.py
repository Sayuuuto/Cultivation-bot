from __future__ import annotations

STATUS_EMOJI: dict[str, str] = {
    "burn": "🔥",
    "bleed": "🩸",
    "poison": "☠️",
    "stun": "💫",
    "seal": "🔒",
    "fear": "😱",
    "dodge": "💨",
}

TECHNIQUE_EMOJI: dict[str, str] = {
    "sword": "⚔️",
    "fire": "🔥",
    "body": "🛡️",
    "soul": "👁️",
    "utility": "✨",
    "passive": "💠",
}

OUTCOME_EMOJI = {
    "success": "✅",
    "partial": "🟡",
    "fail": "❌",
    "victory": "🏆",
    "defeat": "💀",
    "fled": "🏃",
}

RARE_EVENT_FLAIR: dict[str, tuple[str, str]] = {
    "hidden_herb_patch": ("🌿", "Hidden Herb Patch"),
    "wandering_elder": ("🧙", "Wandering Elder"),
    "ancient_cache": ("🏺", "Ancient Cache"),
    "ambush": ("🗡️", "Ambush"),
    "abandoned_cart": ("🛒", "Abandoned Cart"),
    "hidden_moonwell": ("🌙", "Hidden Moonwell"),
    "inheritance_fragment": ("📜", "Inheritance Fragment"),
    "market_day": ("🏪", "Market Day"),
    "lost_child": ("👧", "Lost Child"),
    "deep_grove": ("🌲", "Deep Grove"),
    "sinking_road": ("🕳️", "Sinking Road"),
    "cursed_shrine": ("⛩️", "Cursed Shrine"),
}


def format_hp_bar(hp: int, max_hp: int, *, length: int = 10, fill: str = "🟩", empty: str = "⬛") -> str:
    if max_hp <= 0:
        return empty * length
    ratio = max(0.0, min(1.0, hp / max_hp))
    filled = int(round(ratio * length))
    filled = max(0, min(length, filled))
    if hp > 0 and filled == 0:
        filled = 1
    return fill * filled + empty * (length - filled)


def format_hp_block(
    name: str,
    hp: int,
    max_hp: int,
    *,
    icon: str = "❤️",
    bar_fill: str = "🟩",
    include_header: bool = True,
) -> str:
    pct = 0 if max_hp <= 0 else int(hp / max_hp * 100)
    bar = format_hp_bar(hp, max_hp, fill=bar_fill)
    body = f"{bar} **{hp}/{max_hp}** ({pct}%)"
    if include_header:
        return f"{icon} **{name}**\n{body}"
    return body


def format_status_badges(statuses) -> str:
    if not statuses:
        return "_none_"
    parts: list[str] = []
    for status in statuses:
        emoji = STATUS_EMOJI.get(status.status_id, "•")
        stacks = f"×{status.stacks}" if status.stacks > 1 else ""
        parts.append(f"{emoji} {status.status_id.title()}{stacks}")
    return " · ".join(parts)


def format_combat_log_lines(lines: list[str], *, limit: int = 6) -> str:
    if not lines:
        return "_The battle begins…_"
    styled: list[str] = []
    for line in lines[-limit:]:
        styled.append(f"{_emoji_for_log_line(line)} {line}")
    return "\n".join(styled)


def _emoji_for_log_line(line: str) -> str:
    lower = line.lower()
    if "flee" in lower or "retreat" in lower:
        return "🏃"
    if "dodge" in lower or "miss" in lower:
        return "💨"
    if "crit" in lower or "critical" in lower:
        return "💥"
    if "heal" in lower or "barrier" in lower or "restore" in lower:
        return "💚"
    if "burn" in lower or "fire" in lower or "ember" in lower:
        return "🔥"
    if "bleed" in lower:
        return "🩸"
    if "poison" in lower:
        return "☠️"
    if "stun" in lower or "seal" in lower or "fear" in lower:
        return "💫"
    if "defeat" in lower or "fall" in lower:
        return "💀"
    if "victory" in lower or "win" in lower:
        return "🏆"
    if "strike" in lower or "hit" in lower or "damage" in lower:
        return "⚔️"
    return "▫️"


def format_loot_lines(drops: dict[str, int], name_fn) -> str:
    if not drops:
        return "_Nothing extra this time._"
    return "\n".join(f"🎁 **{name_fn(item_id)}** ×{qty}" for item_id, qty in sorted(drops.items()))


def format_qi_bar(qi: int, cap: int, *, length: int = 10) -> str:
    return format_hp_bar(qi, cap, length=length, fill="🔵", empty="⬛")


def technique_button_emoji(category: str) -> str:
    return TECHNIQUE_EMOJI.get(category, "⚔️")


def banner(title: str, emoji: str, body: str) -> str:
    return f"{emoji} **{title}**\n{body}"
