from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..game import CultivateResult, qi_cap
from ..inventory import get_item_name
from ..models import Player
from .formatting import (
    OUTCOME_EMOJI,
    RARE_EVENT_FLAIR,
    banner,
    format_combat_log_lines,
    format_hp_block,
    format_loot_lines,
    format_qi_bar,
)

if TYPE_CHECKING:
    from ..adventure import AdventureResult, PendingAdventure


def _choice_hint(choice) -> str:
    if getattr(choice, "route_tag", None):
        tone = getattr(choice, "route_tone", None)
        tone_hint = f" · {tone.title()} route" if tone else ""
        return f"Route{tone_hint}"
    risk = "Low risk"
    if getattr(choice, "fail_chance", 0.0) >= 0.16:
        risk = "High risk"
    elif getattr(choice, "fail_chance", 0.0) >= 0.1:
        risk = "Medium risk"
    reward = "steady rewards"
    if getattr(choice, "drop_mult", 1.0) >= 1.25 or getattr(choice, "spirit_stones", 0) > 0:
        reward = "richer rewards"
    elif getattr(choice, "drop_mult", 1.0) < 0.9:
        reward = "safer rewards"
    return f"{risk} · {reward}"


def _choice_hint_lines(pending: PendingAdventure) -> list[str]:
    return [
        f"**{choice.label}** — {_choice_hint(choice)}"
        for choice in pending.choices[:4]
    ]


def build_cultivate_embed(
    res: CultivateResult,
    player: Player,
    *,
    realm_display: str,
    passive_qi: int = 0,
    applied_drops: dict[str, int] | None = None,
) -> discord.Embed:
    cap = qi_cap(player.realm_index, player.substage, player)
    drops = applied_drops if applied_drops is not None else (res.bonus_drops or {})

    if res.event_id:
        color = discord.Color.gold()
        title = f"{res.event_emoji} Cultivation — {res.event_title}"
        description = banner(res.event_title, res.event_emoji, res.event_message)
    else:
        color = discord.Color.green()
        title = "🧘 Cultivation Complete"
        description = res.message

    embed = discord.Embed(title=title, description=description, color=color)

    active_qi = res.active_qi_gain if res.active_qi_gain else max(0, res.qi_gain - passive_qi)
    embed.add_field(
        name="🌙 Passive Qi",
        value=(
            f"**+{passive_qi} Qi** from formation bank"
            if passive_qi > 0
            else "_Formation bank was empty this cultivate._"
        ),
        inline=True,
    )
    active_line = f"**+{active_qi} Qi**"
    if res.event_id and res.event_qi_mult != 1.0:
        active_line += f" _(×{res.event_qi_mult:.1f} dao event)_"
    embed.add_field(
        name="🧘 /cultivate",
        value=active_line,
        inline=True,
    )
    embed.add_field(
        name="🌀 Qi pool",
        value=f"**+{res.qi_gain}** total this action\n{format_qi_bar(player.qi, cap)}\n**{player.qi}/{cap}**",
        inline=False,
    )
    embed.add_field(name="💎 Spirit stones", value=str(player.spirit_stones), inline=True)

    if drops:
        embed.add_field(
            name="🎁 Cultivation rewards",
            value=format_loot_lines(drops, get_item_name),
            inline=False,
        )

    if res.meridian_note:
        embed.add_field(name="🌀 Meridians", value=res.meridian_note, inline=False)

    embed.add_field(name="🏔️ Realm", value=realm_display, inline=False)
    embed.set_footer(text="🌿 Gather · ⚔️ Hunt · 📜 Adventure while you recover")
    return embed


def build_hunt_result_embed(area_name: str, state, hunt_res, *, log_lines: list[str] | None = None) -> discord.Embed:
    if state.victory:
        color = discord.Color.green()
        outcome = f"{OUTCOME_EMOJI['victory']} **Victory**"
    elif state.fled:
        color = discord.Color.orange()
        outcome = f"{OUTCOME_EMOJI['fled']} **Fled**"
    else:
        color = discord.Color.red()
        outcome = f"{OUTCOME_EMOJI['defeat']} **Defeat**"

    combined_log = (log_lines or state.log)[-8:] + hunt_res.messages[-2:]
    embed = discord.Embed(
        title=f"⚔️ Hunt — {area_name}",
        description=format_combat_log_lines(combined_log, limit=8),
        color=color,
    )
    embed.add_field(
        name="❤️ Your condition",
        value=format_hp_block("You", state.player.hp, state.player.max_hp, include_header=False),
        inline=True,
    )
    embed.add_field(name="Outcome", value=outcome, inline=True)
    if hunt_res.drops:
        embed.add_field(
            name="🎁 Spoils",
            value=format_loot_lines(hunt_res.drops, get_item_name),
            inline=False,
        )
    return embed


def build_adventure_embed_from_pending(pending: PendingAdventure) -> discord.Embed:
    icon = "⚔️" if pending.encounter_type == "combat" else "📜"
    recent = pending.messages[-4:] if pending.messages else []
    description = format_combat_log_lines(recent, limit=4) if recent else pending.prompt
    embed = discord.Embed(
        title=f"🗺️ Adventure — {pending.area_name}",
        description=description,
        color=discord.Color.teal(),
    )
    embed.add_field(
        name=f"{icon} Segment {pending.segment}/{pending.segments_total}",
        value=pending.prompt,
        inline=False,
    )
    if pending.route_label:
        embed.add_field(name="Route", value=f"**{pending.route_label}**", inline=True)
    hints = _choice_hint_lines(pending)
    if hints and pending.encounter_type != "combat":
        embed.add_field(name="Path choices", value="\n".join(hints), inline=False)
    if pending.encounter_type == "combat" and pending.monster_name:
        player_hp = format_hp_block("You", pending.player_hp or 0, pending.player_max_hp or 1, bar_fill="🟩")
        foe_hp = format_hp_block(
            pending.monster_name,
            pending.opponent_hp or 0,
            pending.opponent_max_hp or 1,
            icon="👹",
            bar_fill="🟥",
        )
        embed.add_field(name="⚔️ Combat", value=f"{player_hp}\n\n{foe_hp}", inline=False)
        embed.set_footer(text="✨ Techniques · ⏭ Pass Turn · 🏃 Flee")
    else:
        embed.set_footer(text="Choose wisely — risky paths can fail the run or boost loot.")
    return embed


def build_adventure_embed_from_result(res: AdventureResult, qi: int) -> discord.Embed:
    if res.failed_run:
        color = discord.Color.red()
        outcome_icon = OUTCOME_EMOJI["fail"]
    elif res.outcome == "success":
        color = discord.Color.green()
        outcome_icon = OUTCOME_EMOJI["success"]
    else:
        color = discord.Color.orange()
        outcome_icon = OUTCOME_EMOJI["partial"]

    styled_messages: list[str] = list(res.messages)

    embed = discord.Embed(
        title=f"🗺️ Adventure — {res.area_name}",
        description=format_combat_log_lines(styled_messages, limit=10),
        color=color,
    )
    embed.add_field(
        name="Outcome",
        value=f"{outcome_icon} **{res.outcome.title()}**",
        inline=True,
    )
    embed.add_field(
        name="Segments",
        value=f"**{res.segments_cleared}/{res.target_segments}** cleared",
        inline=True,
    )
    cap_hint = f"**{qi}** Qi"
    if res.qi_delta:
        cap_hint += f" _(+{res.qi_delta} this run)_"
    embed.add_field(name="🌀 Qi", value=cap_hint, inline=True)
    if res.drops:
        embed.add_field(
            name="🎁 Loot",
            value=format_loot_lines(res.drops, get_item_name),
            inline=False,
        )
    if res.route_label:
        steps = " → ".join(res.route_steps[:5]) if res.route_steps else "Path completed"
        embed.add_field(
            name="Route",
            value=f"**{res.route_label}**\n{steps}",
            inline=False,
        )
    if res.rare_events:
        flair = " · ".join(
            f"{RARE_EVENT_FLAIR.get(eid, ('✨', eid))[0]} {RARE_EVENT_FLAIR.get(eid, ('✨', eid.replace('_', ' ').title()))[1]}"
            for eid in res.rare_events
        )
        embed.add_field(name="✨ Rare events", value=flair, inline=False)
    return embed
