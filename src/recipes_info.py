from __future__ import annotations

import discord

from .consumables import PILL_EFFECTS
from .content import RecipeDef, get_recipes
from .effects import EFFECT_DESCRIPTIONS, HASTE_EFFECTS
from .forge import get_forge_recipes
from .inventory import get_item_name
from .manuals import MANUAL_CRAFT_INPUTS

DISCORD_FIELD_CHAR_LIMIT = 1024
# Leave room for formatting; chunk well under the hard limit.
FIELD_CHUNK_BUDGET = 980


def _format_inputs(inputs: dict[str, int]) -> str:
    parts = [f"{get_item_name(item_id)} ×{qty}" for item_id, qty in sorted(inputs.items())]
    return ", ".join(parts) if parts else "None"


def _pill_effect_text(item_id: str) -> str:
    if item_id in PILL_EFFECTS:
        effect_id = str(PILL_EFFECTS[item_id]["effect_id"])
        return EFFECT_DESCRIPTIONS.get(effect_id, effect_id.replace("_", " ").title())
    if item_id in HASTE_EFFECTS:
        meta = HASTE_EFFECTS[item_id]
        minutes = meta["seconds_per_charge"] // 60
        charges = meta.get("default_charges", 1)
        label = meta["label"]
        if charges > 1:
            return f"{label}: −{minutes} min from cooldown ({charges} uses)"
        return f"{label}: −{minutes} min from next cooldown"
    if item_id == "root_reforging_pill":
        return "Rerolls your spirit root once."
    return "Special item."


def _format_recipe_line(recipe: RecipeDef) -> str:
    pct = int(recipe.success_chance * 100)
    inputs = _format_inputs(recipe.inputs)
    effect = _pill_effect_text(recipe.output_item_id)
    byproduct = ""
    if recipe.byproduct_item_id:
        byproduct = f" · Fail: {get_item_name(recipe.byproduct_item_id)}"
    return (
        f"**{recipe.name}** ({pct}%)\n"
        f"In: {inputs}\n"
        f"Out: {get_item_name(recipe.output_item_id)} ×{recipe.output_quantity} · {effect}{byproduct}"
    )


def _chunk_lines(lines: list[str], max_len: int = FIELD_CHUNK_BUDGET) -> list[str]:
    if not lines:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        extra = len(line) if not current else 2 + len(line)
        if current and current_len + extra > max_len:
            chunks.append("\n\n".join(current))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _add_chunked_fields(embed: discord.Embed, base_name: str, lines: list[str]) -> None:
    chunks = _chunk_lines(lines)
    if not chunks:
        return
    for index, chunk in enumerate(chunks):
        if len(chunks) == 1:
            name = base_name
        else:
            name = f"{base_name} ({index + 1}/{len(chunks)})"
        embed.add_field(name=name, value=chunk[:DISCORD_FIELD_CHAR_LIMIT], inline=False)


def build_recipes_embed(recipe_type: str | None = None) -> discord.Embed:
    recipes = get_recipes()
    if recipe_type is not None:
        filtered = [r for r in recipes.values() if r.recipe_type == recipe_type]
        title = f"Recipes — {recipe_type.title()}"
    else:
        filtered = list(recipes.values())
        title = "Alchemy & Crafting Recipes"

    pills = [r for r in filtered if r.recipe_type == "pill"]
    keys = [r for r in filtered if r.recipe_type == "key"]
    other = [r for r in filtered if r.recipe_type not in {"pill", "key"}]

    embed = discord.Embed(
        title=title,
        description=(
            "Materials come from **`/adventure`**. Higher areas unlock stronger pills. "
            "Cooldown pills shave time off timers — stack them before a busy session."
        ),
        color=discord.Color.dark_teal(),
    )

    if pills:
        _add_chunked_fields(embed, "Pills", [_format_recipe_line(r) for r in pills])
    if keys:
        _add_chunked_fields(embed, "Keys", [_format_recipe_line(r) for r in keys])
    if other:
        _add_chunked_fields(embed, "Other", [_format_recipe_line(r) for r in other])

    if recipe_type in (None, "forge"):
        forge = get_forge_recipes()
        forge_lines: list[str] = []
        for slot, data in forge.items():
            inputs = _format_inputs(data["inputs"])
            ranges = data["stat_ranges"]
            range_text = ", ".join(f"{k} {v[0]}–{v[1]}" for k, v in ranges.items())
            forge_lines.append(
                f"**{data['name']}** ({slot})\nIn: {inputs}\nStats: {range_text}"
            )
        _add_chunked_fields(embed, "Equipment forging (`/forge`)", forge_lines)

    if recipe_type is None:
        manual_inputs = _format_inputs(MANUAL_CRAFT_INPUTS)
        embed.add_field(
            name="Technique manual binding (`/craft manual`)",
            value=(
                f"In: {manual_inputs}\n"
                "Out: random technique manual (realm-weighted pool)\n"
                "Fragments drop from **`/cultivate`**, **`/hunt`**, and **`/adventure`**. "
                "Scroll and ink from **`/gather`**."
            ),
            inline=False,
        )

    embed.set_footer(text="Craft with /craft pill · /craft key · /craft manual · Forge with /forge")
    return embed
