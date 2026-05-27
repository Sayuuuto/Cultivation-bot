from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .game import (
    BreakthroughPreview,
    CULTIVATE_LUCK_MAX,
    CULTIVATE_LUCK_MIN,
    compute_breakthrough_preview,
    apply_offline_progress,
    cultivate_base_qi_gain,
    passive_qi_per_minute,
    qi_cap,
    to_utc,
)
from .karma import karma_tier_label
from .models import Player
from .modifiers import CharacterModifiers


@dataclass(frozen=True)
class CultivatePreview:
    passive_qi_pending: int
    passive_minutes: int
    passive_cap_minutes: int
    passive_qi_per_minute: float
    active_qi_min: int
    active_qi_max: int
    active_qi_typical: int
    qi_mult: float
    qi_cap: int


def preview_passive_qi(
    player: Player,
    now,
    offline_cap_minutes: int,
    cap_mult: float = 1.0,
) -> tuple[int, int, int]:
    """Returns (qi_pending, minutes_away, cap_minutes)."""
    if player.last_active_at is None:
        return 0, 0, int(offline_cap_minutes * cap_mult)

    now = to_utc(now)
    last_active = to_utc(player.last_active_at)
    if now <= last_active:
        return 0, 0, int(offline_cap_minutes * cap_mult)

    minutes = int((now - last_active).total_seconds() / 60)
    cap_minutes = int(offline_cap_minutes * cap_mult)
    capped_minutes = min(minutes, cap_minutes)
    qi = apply_offline_progress(player, now, offline_cap_minutes, cap_mult=cap_mult)
    return qi, capped_minutes, cap_minutes


def preview_cultivate_qi(
    player: Player,
    mod: CharacterModifiers,
    cfg: Config,
    now,
) -> CultivatePreview:
    qi_mult = mod.cultivate_qi_mult * mod.qi_gathering_mult
    base = cultivate_base_qi_gain(player.realm_index)
    active_min = int(base * CULTIVATE_LUCK_MIN * qi_mult)
    active_max = int(base * CULTIVATE_LUCK_MAX * qi_mult)
    active_typical = int(base * qi_mult)

    passive_qi, passive_minutes, passive_cap = preview_passive_qi(
        player,
        now,
        cfg.offline_cap_minutes,
        cap_mult=mod.offline_cap_mult,
    )

    return CultivatePreview(
        passive_qi_pending=passive_qi,
        passive_minutes=passive_minutes,
        passive_cap_minutes=passive_cap,
        passive_qi_per_minute=passive_qi_per_minute(player.realm_index),
        active_qi_min=active_min,
        active_qi_max=active_max,
        active_qi_typical=active_typical,
        qi_mult=qi_mult,
        qi_cap=qi_cap(player.realm_index, player.substage, player),
    )


def _pct(value: float) -> str:
    return f"{int(round(value * 100))}%"


def _signed_pct(value: float) -> str:
    pct = int(round(abs(value) * 100))
    if value > 0:
        return f"+{pct}%"
    if value < 0:
        return f"−{pct}%"
    return "0%"


def _format_qi_rate(per_minute: float) -> str:
    if per_minute < 10:
        return f"{per_minute:.1f}"
    return str(int(round(per_minute)))


def format_passive_qi_rate_line(preview: CultivatePreview) -> str:
    rate = _format_qi_rate(preview.passive_qi_per_minute)
    lines = [
        f"**{rate} Qi/min** while away — banked on your next action "
        f"(up to **{preview.passive_cap_minutes}** min stored)."
    ]
    if preview.passive_qi_pending > 0:
        lines.append(
            f"Ready now: **+{preview.passive_qi_pending} Qi** from **{preview.passive_minutes}** min away."
        )
    else:
        lines.append("Nothing banked yet — qi gathers whenever you are inactive.")
    return "\n".join(lines)


def format_passive_qi_line(preview: CultivatePreview) -> str:
    """Short passive summary (e.g. after an action applied banked qi)."""
    if preview.passive_qi_pending <= 0:
        rate = _format_qi_rate(preview.passive_qi_per_minute)
        return f"Passive income: **{rate} Qi/min** while away (nothing banked right now)."
    return (
        f"**+{preview.passive_qi_pending} Qi** banked from **{preview.passive_minutes} min** away — "
        f"collects on your next action."
    )


def format_active_cultivate_line(preview: CultivatePreview, mod: CharacterModifiers) -> str:
    pill_bits: list[str] = []
    if mod.qi_gathering_mult > 1.0:
        pill_bits.append(f"Qi Gathering ×{mod.qi_gathering_mult:.2f}")
    if mod.cultivate_qi_mult != 1.0:
        pill_bits.append(f"dao ×{mod.cultivate_qi_mult:.2f}")
    bonus = f" ({' · '.join(pill_bits)})" if pill_bits else ""
    return (
        f"**`/cultivate`**: **{preview.active_qi_min}–{preview.active_qi_max} Qi** per use "
        f"(typical **~{preview.active_qi_typical}**){bonus}.\n"
        "_Pills boost active cultivation only — not passive Qi/min._"
    )


def format_total_cultivate_line(preview: CultivatePreview, mod: CharacterModifiers) -> str:
    """Combined hint when opening `/cultivate` — passive bank pays out with the command."""
    active = format_active_cultivate_line(preview, mod).split("\n", 1)[0]
    if preview.passive_qi_pending <= 0:
        return active
    return (
        f"{active}\n"
        f"Also collects **+{preview.passive_qi_pending} passive Qi** banked from time away."
    )


def format_breakthrough_chance_breakdown(preview: BreakthroughPreview, player: Player) -> str:
    lines = [
        f"**Base chance:** {_pct(preview.base_success)}",
        f"**Your chance:** {_pct(preview.success_chance)}",
    ]
    modifiers: list[str] = []
    if preview.karma_bonus:
        tier = karma_tier_label(player.karma).split("(")[0].strip()
        modifiers.append(f"{tier} {_signed_pct(preview.karma_bonus)}")
    if preview.qi_fill_bonus > 0.001:
        modifiers.append(f"qi surplus {_signed_pct(preview.qi_fill_bonus)}")
    if preview.stability_bonus > 0.001:
        modifiers.append(f"gear/affix {_signed_pct(preview.stability_bonus)}")
    if preview.clarity_bonus > 0.001:
        modifiers.append(
            f"Clarity pill ×{preview.clarity_charges} {_signed_pct(preview.clarity_bonus)}"
        )
    if preview.realm_penalty > 0.001:
        modifiers.append(f"higher realm {_signed_pct(-preview.realm_penalty)}")
    if modifiers:
        lines.append("**Modifiers:** " + " · ".join(modifiers))
    else:
        lines.append("**Modifiers:** none beyond base odds")
    if preview.can_attempt:
        lines.append(f"If you fail: lose about **{preview.estimated_fail_setback} Qi**.")
    else:
        lines.append(f"Need **{preview.qi_required} Qi** to attempt (you have **{player.qi}**).")
    return "\n".join(lines)


def format_breakthrough_chance_line(preview: BreakthroughPreview, player: Player) -> str:
    return format_breakthrough_chance_breakdown(preview, player)


def build_breakthrough_preview_embed(
    player: Player,
    preview: BreakthroughPreview,
) -> "discord.Embed":
    import discord

    if preview.can_attempt:
        title = "Breakthrough — Commit?"
        color = discord.Color.gold()
        surplus = player.qi - preview.qi_required
        qi_line = f"**{player.qi} / {preview.qi_required}** Qi"
        if surplus > 0:
            qi_line += f" (+{surplus} surplus)"
        description = f"Your qi is ready ({qi_line}). Review the odds, then commit or hold back."
    else:
        title = "Breakthrough — Not Ready"
        color = discord.Color.orange()
        description = "Gather more qi before you risk a breakthrough attempt."

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(
        name="Odds",
        value=format_breakthrough_chance_breakdown(preview, player),
        inline=False,
    )
    embed.add_field(
        name="Realm",
        value=f"**{player.qi}** qi · attempting from current stage",
        inline=False,
    )
    if preview.can_attempt:
        embed.set_footer(text="Commit to roll breakthrough · Hold back to cancel.")
    return embed


def add_cultivation_preview_fields(
    embed,
    player: Player,
    mod: CharacterModifiers,
    cfg: Config,
    now,
    *,
    session=None,
) -> tuple[CultivatePreview, BreakthroughPreview]:
    cultivate = preview_cultivate_qi(player, mod, cfg, now)
    breakthrough = compute_breakthrough_preview(
        player,
        mod,
        session=session,
        player_id=player.id,
    )
    embed.add_field(name="Qi progress", value=f"**{player.qi} / {cultivate.qi_cap}**", inline=True)
    embed.add_field(
        name="🌙 Passive Qi (time away)",
        value=format_passive_qi_rate_line(cultivate),
        inline=False,
    )
    embed.add_field(
        name="🧘 /cultivate (active)",
        value=format_active_cultivate_line(cultivate, mod),
        inline=False,
    )
    embed.add_field(
        name="Breakthrough odds",
        value=format_breakthrough_chance_line(breakthrough, player),
        inline=False,
    )
    return cultivate, breakthrough
