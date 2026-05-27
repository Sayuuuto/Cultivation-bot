from __future__ import annotations

import discord
from sqlalchemy.orm import Session

from .consumables import PILL_EFFECTS
from .content import get_dungeons, get_recipes
from .drop_sources import get_drop_sources
from .effects import EFFECT_DESCRIPTIONS, HASTE_EFFECTS
from .forge import get_forge_recipes
from .inventory import CATEGORY_META, get_item_def, get_item_name, get_item_quantity, get_player_inventory
from .manuals import FRAGMENT_ITEM_ID, MANUAL_CRAFT_INPUTS


def recipes_using_item(item_id: str) -> list[str]:
    lines: list[str] = []
    for recipe in get_recipes().values():
        need = recipe.inputs.get(item_id, 0)
        if need <= 0:
            continue
        pct = int(recipe.success_chance * 100)
        lines.append(f"**{recipe.name}** ({pct}%) — needs ×{need} · `/craft {recipe.recipe_type}`")

    for slot, data in get_forge_recipes().items():
        need = data["inputs"].get(item_id, 0)
        if need <= 0:
            continue
        lines.append(f"**{data['name']}** ({slot}) — needs ×{need} · `/forge`")

    if item_id in MANUAL_CRAFT_INPUTS:
        need = MANUAL_CRAFT_INPUTS[item_id]
        lines.append(f"**Bind technique manual** — needs ×{need} · `/craft manual`")

    return lines


def manual_bind_progress_lines(session: Session, player_id: int) -> list[str]:
    lines: list[str] = []
    for item_id, need in sorted(MANUAL_CRAFT_INPUTS.items()):
        have = get_item_quantity(session, player_id, item_id)
        mark = "✓" if have >= need else "…"
        lines.append(f"{mark} **{get_item_name(item_id)}** — {have}/{need}")
    return lines


def can_bind_technique_manual_from_session(session: Session, player_id: int) -> bool:
    from .inventory import has_items

    return has_items(session, player_id, MANUAL_CRAFT_INPUTS)


def format_manual_bind_progress(session: Session, player_id: int) -> str | None:
    """Profile hint when the player is working toward `/craft manual`."""
    if can_bind_technique_manual_from_session(session, player_id):
        return "**Manual binding** — all materials ready; use **`/craft manual`**."

    has_any = any(
        get_item_quantity(session, player_id, item_id) > 0 for item_id in MANUAL_CRAFT_INPUTS
    )
    if not has_any:
        return None

    parts = manual_bind_progress_lines(session, player_id)
    missing = [
        get_item_name(item_id)
        for item_id, need in MANUAL_CRAFT_INPUTS.items()
        if get_item_quantity(session, player_id, item_id) < need
    ]
    tail = ""
    if missing:
        tail = f" — gather **{' + '.join(missing)}** via **`/gather`**"
    return "**Manual binding** — " + " · ".join(parts) + tail


def get_item_short_hint(session: Session | None, player_id: int | None, item_id: str) -> str:
    """One-line hint for autocomplete and legacy callers."""
    item = get_item_def(item_id)
    if item is None:
        return ""

    action = get_item_quick_action(item_id)
    if action:
        return action[1]

    recipe_lines = recipes_using_item(item_id)
    if recipe_lines:
        return recipe_lines[0].split(" — ", 1)[-1]

    sources = get_drop_sources(item_id)
    if sources:
        return f"Found via {sources[0].via}."

    return ""


def _category_color(category: str) -> discord.Color:
    return {
        "material": discord.Color.dark_teal(),
        "pill": discord.Color.green(),
        "manual": discord.Color.gold(),
        "key": discord.Color.purple(),
        "special": discord.Color.blue(),
    }.get(category, discord.Color.dark_teal())


def _dungeon_for_key(item_id: str) -> str | None:
    for dungeon in get_dungeons().values():
        if dungeon.key_item_id == item_id:
            return dungeon.name
    return None


def get_item_effect_text(item_id: str) -> str | None:
    if item_id in PILL_EFFECTS:
        effect_id = str(PILL_EFFECTS[item_id]["effect_id"])
        return EFFECT_DESCRIPTIONS.get(effect_id, "Temporary cultivation boost.")

    if item_id in HASTE_EFFECTS:
        effect_id = str(HASTE_EFFECTS[item_id]["effect_id"])
        label = str(HASTE_EFFECTS[item_id].get("label", get_item_name(item_id)))
        desc = EFFECT_DESCRIPTIONS.get(effect_id, "Reduces activity cooldown.")
        return f"**{label}** — {desc}"

    if item_id == "root_reforging_pill":
        return "Reroll your **spirit root** (one-time consumable)."

    if item_id == FRAGMENT_ITEM_ID:
        need = MANUAL_CRAFT_INPUTS[FRAGMENT_ITEM_ID]
        return f"Combine **×{need}** with a blank scroll and spirit ink to bind a random technique manual."

    item = get_item_def(item_id)
    if item is not None and item.category == "manual":
        from .combat.catalog import get_technique_by_manual
        from .technique_info import format_technique_combat_summary

        tech = get_technique_by_manual(item_id)
        if tech is not None:
            kind = "Passive art" if tech.slot_type == "passive" else "Active art"
            return (
                f"**{tech.name}** — {kind} ({tech.category.title()} · {tech.tier.title()} tier). "
                f"{tech.description} "
                f"Use **`/technique {tech.name}`** or **`/item {get_item_name(item_id)}`** for full details."
            )

    dungeon = _dungeon_for_key(item_id)
    if dungeon is not None:
        return f"Opens **{dungeon}** when you run **`/dungeon`**."

    return None


def get_item_quick_action(item_id: str) -> tuple[str, str] | None:
    """Return (command, player-facing label) for the primary use of an item."""
    item = get_item_def(item_id)
    if item is None:
        return None

    if item.category == "manual":
        return ("/learn", "Read the full art with **`/technique`** or **`/item`**, then study with **`/learn`**.")

    if item_id in PILL_EFFECTS or item_id in HASTE_EFFECTS or item_id == "root_reforging_pill":
        return ("/use", f"Consume with **`/use {get_item_name(item_id)}`**.")

    if item.category == "key":
        dungeon = _dungeon_for_key(item_id)
        if dungeon:
            return ("/dungeon", f"Enter **{dungeon}** with **`/dungeon`**.")
        return ("/dungeon", "Use at the matching **`/dungeon`** gate.")

    if item_id == FRAGMENT_ITEM_ID or item_id in MANUAL_CRAFT_INPUTS:
        return ("/craft manual", "Combine via **`/craft manual`** when you have all parts.")

    recipe_lines = recipes_using_item(item_id)
    if recipe_lines:
        if "craft manual" in recipe_lines[0].lower():
            return ("/craft manual", "Used when binding a technique manual.")
        if "/forge" in recipe_lines[0]:
            return ("/forge", "Forge ingredient — see **`/recipes`**.")
        return ("/craft pill", "Crafting material — see **`/recipes`** or **`/craft pill`**.")

    return None


def _format_sources_block(item_id: str) -> str:
    sources = get_drop_sources(item_id)
    if not sources:
        return "Check **`/areas`**, **`/recipes`**, or **`/shop`**."
    return "\n".join(f"• **{src.label}** — {src.via}" for src in sources)


def build_item_detail_embed(
    item_id: str,
    *,
    session: Session,
    player_id: int,
) -> discord.Embed | None:
    item = get_item_def(item_id)
    if item is None:
        return None

    have = get_item_quantity(session, player_id, item_id)
    emoji, category_label = CATEGORY_META.get(item.category, ("📦", "Item"))
    effect = get_item_effect_text(item_id)
    action = get_item_quick_action(item_id)
    recipe_lines = recipes_using_item(item_id)

    embed = discord.Embed(
        title=f"{emoji} {item.name}",
        description=item.description or "_No lore recorded for this item._",
        color=_category_color(item.category),
    )

    if item.category == "manual":
        from .combat.catalog import get_technique_by_manual
        from .technique_info import append_manual_technique_fields

        tech = get_technique_by_manual(item_id)
        if tech is not None:
            append_manual_technique_fields(embed, tech)

    embed.add_field(name="You carry", value=f"**×{have}**", inline=True)
    embed.add_field(name="Type", value=f"{emoji} {category_label}", inline=True)

    if effect:
        embed.add_field(name="⚡ Effect", value=effect, inline=False)

    if action:
        cmd, label = action
        embed.add_field(name="🎮 Quick action", value=f"{label}\n→ **`{cmd}`**", inline=False)

    if recipe_lines:
        embed.add_field(name="🔨 Used in crafting", value="\n".join(f"• {line}" for line in recipe_lines), inline=False)

    if item_id in MANUAL_CRAFT_INPUTS:
        progress = manual_bind_progress_lines(session, player_id)
        if progress:
            status = "✅ Ready to bind" if can_bind_technique_manual_from_session(session, player_id) else "⏳ In progress"
            embed.add_field(
                name=f"📜 Manual binding — {status}",
                value="\n".join(progress),
                inline=False,
            )

    embed.add_field(name="📍 How to obtain more", value=_format_sources_block(item_id), inline=False)

    embed.set_footer(text="Storage ring · /inventory lists names · /item or /technique for manual details")
    return embed


def resolve_inventory_item_id(session: Session, player_id: int, raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None

    normalized = text.lower().replace(" ", "_").replace("-", "_")
    if get_item_quantity(session, player_id, normalized) > 0:
        return normalized

    lower = text.lower()
    for stack in get_player_inventory(session, player_id):
        item = get_item_def(stack.item_id)
        if item is not None and item.name.lower() == lower:
            return stack.item_id

    tokens = [part for part in lower.replace("_", " ").split() if part]
    matches: list[str] = []
    for stack in get_player_inventory(session, player_id):
        item = get_item_def(stack.item_id)
        if item is None:
            continue
        haystack = f"{stack.item_id.replace('_', ' ')} {item.name}".lower()
        if tokens and all(token in haystack for token in tokens):
            matches.append(stack.item_id)
        elif lower in haystack:
            matches.append(stack.item_id)

    if len(matches) == 1:
        return matches[0]
    return None


def list_inventory_item_options(session: Session, player_id: int) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for stack in get_player_inventory(session, player_id):
        item = get_item_def(stack.item_id)
        if item is None:
            continue
        label = f"{item.name} ×{stack.quantity}"
        options.append((stack.item_id, label))
    options.sort(key=lambda row: row[1].lower())
    return options
