from __future__ import annotations

import discord

from ..ui.formatting import (
    OUTCOME_EMOJI,
    format_combat_log_lines,
    format_hp_block,
    format_status_badges,
)
from .engine import CombatState


def build_combat_embed(
    title: str,
    state: CombatState,
    *,
    footer: str | None = None,
) -> discord.Embed:
    player_low = state.player.hp <= max(1, state.player.max_hp * 0.3)
    color = discord.Color.dark_red() if player_low and not state.finished else discord.Color.gold()
    if state.finished:
        color = discord.Color.green() if state.victory else discord.Color.red()

    embed = discord.Embed(
        title=f"⚔️ {title}",
        description=format_combat_log_lines(state.log, limit=6),
        color=color,
    )

    embed.add_field(
        name="❤️ You",
        value=(
            f"{format_hp_block('You', state.player.hp, state.player.max_hp, icon='❤️', bar_fill='🟩', include_header=False)}\n"
            f"Status: {format_status_badges(state.player.statuses)}\n"
            f"Turn **{state.turn}**"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"👹 {state.opponent_name}",
        value=(
            f"{format_hp_block(state.opponent_name, state.opponent.hp, state.opponent.max_hp, icon='👹', bar_fill='🟥', include_header=False)}\n"
            f"Status: {format_status_badges(state.opponent.statuses)}"
        ),
        inline=True,
    )

    if state.finished:
        if state.victory:
            outcome = f"{OUTCOME_EMOJI['victory']} **Victory**"
        elif state.fled:
            outcome = f"{OUTCOME_EMOJI['fled']} **Fled**"
        else:
            outcome = f"{OUTCOME_EMOJI['defeat']} **Defeat**"
        embed.add_field(name="Outcome", value=outcome, inline=False)

    embed.set_footer(text=footer or "✨ Techniques · ⏭ Pass Turn · 🏃 Flee")
    return embed


def build_hunt_combat_embed(start, state: CombatState | None = None) -> discord.Embed:
    if state is None:
        is_elite = getattr(start, "combat_tier", "normal") == "elite"
        elite_line = ""
        if is_elite:
            from ..hunt import hunt_elite_encounter_warning

            elite_line = f"\n\n{hunt_elite_encounter_warning(start.beast_name)}\n"
        embed = discord.Embed(
            title=f"🐺 Hunt — {start.area_name}",
            description=(
                f"_{start.flavor}_"
                f"{elite_line}\n"
                f"👹 **{start.beast_name}** emerges from the wild!\n"
                f"{format_hp_block(start.beast_name, start.beast_hp, start.beast_hp, icon='👹', bar_fill='🟥')}"
            ),
            color=discord.Color.gold() if is_elite else discord.Color.dark_green(),
        )
        embed.add_field(
            name="❤️ Your HP",
            value=format_hp_block("You", start.player_hp, start.player_max_hp, include_header=False),
            inline=True,
        )
        embed.set_footer(text="Choose a technique to begin the fight.")
        return embed
    return build_combat_embed(f"Hunt — {start.area_name}", state)


def build_adventure_combat_embed(pending, state: CombatState) -> discord.Embed:
    title = f"Adventure — {pending.area_name}"
    footer = f"📍 Segment {pending.segment}/{pending.segments_total}"
    return build_combat_embed(title, state, footer=footer)
