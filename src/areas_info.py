from __future__ import annotations

import discord

from .adventure import SEGMENTS_PER_RUN, STANCES
from .content import AreaDef, get_area, get_areas
from .game import REALMS
from .inventory import get_item_name
from .models import Player


def _realm_label(realm_index: int) -> str:
    idx = max(0, min(realm_index, len(REALMS) - 1))
    return REALMS[idx]


def _drop_rarity_label(weight: int) -> str:
    if weight >= 35:
        return "common"
    if weight >= 20:
        return "uncommon"
    return "rare"


def _format_drops(area: AreaDef) -> str:
    lines: list[str] = []
    for drop in area.drops:
        name = get_item_name(drop.item_id)
        if drop.min_qty == drop.max_qty:
            qty = f"×{drop.min_qty}"
        else:
            qty = f"×{drop.min_qty}–{drop.max_qty}"
        rarity = _drop_rarity_label(drop.weight)
        lines.append(f"• **{name}** {qty} ({rarity})")
    return "\n".join(lines)


def _format_stances() -> str:
    return (
        "**Cautious** — +success, −15% loot · safer farming\n"
        "**Balanced** — standard risk and rewards\n"
        "**Reckless** — −success, +25% loot · higher risk"
    )


def _player_realm_status(player: Player | None, area: AreaDef) -> str:
    if player is None:
        return f"Requires **{_realm_label(area.min_realm)}** or higher."
    if player.realm_index >= area.min_realm:
        return f"You qualify (**{_realm_label(player.realm_index)}**)."
    return (
        f"You need **{_realm_label(area.min_realm)}** or higher "
        f"(you are **{_realm_label(player.realm_index)}**)."
    )


def _format_area_field(area: AreaDef, player: Player | None) -> str:
    rare_pct = int(area.rare_event_chance * 100)
    return (
        f"**Difficulty:** {area.difficulty.title()} · **Recommended:** {area.recommended_text}\n"
        f"{_player_realm_status(player, area)}\n"
        f"**Materials per successful segment:**\n{_format_drops(area)}\n"
        f"**Rare events:** ~{rare_pct}% per segment · **Run length:** {SEGMENTS_PER_RUN} segments"
    )


def build_areas_embed(player: Player | None, area_id: str | None = None) -> discord.Embed:
    if area_id is not None:
        area = get_area(area_id)
        if area is None:
            embed = discord.Embed(
                title="Unknown Area",
                description="That area is not on the map.",
                color=discord.Color.red(),
            )
            return embed

        rare_lines = [f"• {e.message}" for e in area.rare_events]
        embed = discord.Embed(
            title=area.name,
            description=_format_area_field(area, player),
            color=discord.Color.dark_green(),
        )
        embed.add_field(
            name="Possible rare encounters",
            value="\n".join(rare_lines) if rare_lines else "None configured.",
            inline=False,
        )
        embed.add_field(name="Stances", value=_format_stances(), inline=False)
        embed.set_footer(text="Run `/gather` or `/hunt` (5 min) · `/adventure` for story runs · `/areas` for details")
        return embed

    embed = discord.Embed(
        title="Adventure Areas",
        description=(
            "Each area has different materials used for **pill crafting** and **dungeon keys**. "
            "Higher areas need stronger cultivation but drop better ingredients.\n\n"
            + _format_stances()
        ),
        color=discord.Color.dark_green(),
    )

    for area_id_key, area in get_areas().items():
        embed.add_field(
            name=f"{area.name} ({area.difficulty.title()})",
            value=_format_area_field(area, player),
            inline=False,
        )

    embed.add_field(
        name="Quick crafting guide",
        value=(
            "**Bamboo Grove** → **Qi Gathering** (3 herbs from `/gather`) · **Tempering** (2 cores from `/hunt`)\n"
            "**Ashen Cliff** → Swiftwind & Blood Ember pills; iron for keys\n"
            "**Moonwell Ruins** → Clarity, Moonwell Tonic, Root Reforging pill"
        ),
        inline=False,
    )
    embed.set_footer(text="Use `/areas area:<name>` for rare events · `/adventure` to explore")
    return embed
