from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .game import (
    BreakthroughPreview,
    compute_breakthrough_preview,
    apply_offline_progress,
    compute_stamina_regen,
    cultivate_base_qi_gain,
    energy_stamina_multiplier,
    qi_cap,
    to_utc,
)
from .models import Player
from .modifiers import CharacterModifiers


CULTIVATE_LUCK_MIN = 0.85
CULTIVATE_LUCK_MAX = 1.15
CULTIVATE_STAMINA_COST = 8


@dataclass(frozen=True)
class CultivatePreview:
    passive_qi_pending: int
    passive_minutes: int
    passive_cap_minutes: int
    active_qi_min: int
    active_qi_max: int
    active_qi_typical: int
    total_qi_min: int
    total_qi_max: int
    preview_stamina: int
    stamina_multiplier: float
    qi_mult: float
    stamina_cost: int
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


def preview_stamina(player: Player, now) -> int:
    regen = compute_stamina_regen(player.stamina_last_updated_at, now)
    return min(100, player.stamina + regen)


def preview_cultivate_qi(
    player: Player,
    mod: CharacterModifiers,
    cfg: Config,
    now,
) -> CultivatePreview:
    qi_mult = mod.cultivate_qi_mult * mod.qi_gathering_mult
    stamina_eff = mod.stamina_efficiency
    stamina_cost = max(1, int(CULTIVATE_STAMINA_COST / stamina_eff))

    current_stamina = preview_stamina(player, now)
    stamina_after_cost = max(0, current_stamina - stamina_cost)

    base = cultivate_base_qi_gain(player.realm_index)
    mult_low = energy_stamina_multiplier(0)
    mult_high = energy_stamina_multiplier(100)
    mult_current = energy_stamina_multiplier(stamina_after_cost)

    active_min = int(base * mult_low * CULTIVATE_LUCK_MIN * qi_mult)
    active_max = int(base * mult_high * CULTIVATE_LUCK_MAX * qi_mult)
    active_typical = int(base * mult_current * qi_mult)

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
        active_qi_min=active_min,
        active_qi_max=active_max,
        active_qi_typical=active_typical,
        total_qi_min=active_min + passive_qi,
        total_qi_max=active_max + passive_qi,
        preview_stamina=current_stamina,
        stamina_multiplier=mult_current,
        qi_mult=qi_mult,
        stamina_cost=stamina_cost,
        qi_cap=qi_cap(player.realm_index, player.substage),
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


def format_passive_qi_line(preview: CultivatePreview) -> str:
    if preview.passive_qi_pending <= 0:
        return "No passive Qi banked — you were active recently."
    return (
        f"**+{preview.passive_qi_pending} Qi** banked from **{preview.passive_minutes} min** away "
        f"(cap **{preview.passive_cap_minutes} min**). Applied on your next action."
    )


def format_active_cultivate_line(preview: CultivatePreview, mod: CharacterModifiers) -> str:
    pill_bits: list[str] = []
    if mod.qi_gathering_mult > 1.0:
        pill_bits.append(f"Qi Gathering ×{mod.qi_gathering_mult:.2f}")
    if mod.cultivate_qi_mult != 1.0 and mod.qi_gathering_mult == 1.0:
        pill_bits.append(f"cultivate ×{mod.cultivate_qi_mult:.2f}")
    pill_text = f" · {' · '.join(pill_bits)}" if pill_bits else ""

    return (
        f"**{preview.active_qi_min}–{preview.active_qi_max} Qi** from cultivating "
        f"(typical **~{preview.active_qi_typical}** at **{preview.preview_stamina}** stamina, "
        f"cost **{preview.stamina_cost}**){pill_text}."
    )


def format_total_cultivate_line(preview: CultivatePreview) -> str:
    if preview.passive_qi_pending <= 0:
        return f"Next **`/cultivate`**: **{preview.active_qi_min}–{preview.active_qi_max} Qi** total."
    return (
        f"Next **`/cultivate`**: **{preview.total_qi_min}–{preview.total_qi_max} Qi** total "
        f"(active + passive)."
    )


def format_breakthrough_chance_line(preview: BreakthroughPreview, player: Player) -> str:
    parts = [_pct(preview.success_chance)]
    if preview.moral_bonus:
        parts.append(f"{player.moral_path.title()} {_signed_pct(preview.moral_bonus)}")
    if preview.stability_bonus > 0.001:
        parts.append(f"gear/affix {_signed_pct(preview.stability_bonus)}")
    if preview.clarity_bonus > 0.001:
        parts.append(f"Clarity pill {_signed_pct(preview.clarity_bonus)}")
    if preview.realm_penalty > 0.001:
        parts.append(f"realm {_signed_pct(-preview.realm_penalty)}")

    detail = " · ".join(parts[1:]) if len(parts) > 1 else "base odds"
    line = f"**{_pct(preview.success_chance)}** breakthrough chance ({detail})"
    if preview.can_attempt:
        line += f"\nIf you fail: lose about **{preview.estimated_fail_setback} Qi**."
    else:
        line += f"\nNeed **{preview.qi_required} Qi** to attempt (you have **{player.qi}**)."
    return line


def add_cultivation_preview_fields(
    embed,
    player: Player,
    mod: CharacterModifiers,
    cfg: Config,
    now,
) -> tuple[CultivatePreview, BreakthroughPreview]:
    cultivate = preview_cultivate_qi(player, mod, cfg, now)
    breakthrough = compute_breakthrough_preview(player, mod)
    embed.add_field(name="Qi progress", value=f"**{player.qi} / {cultivate.qi_cap}**", inline=True)
    embed.add_field(
        name="Passive Qi",
        value=format_passive_qi_line(cultivate),
        inline=False,
    )
    embed.add_field(
        name="Active cultivate",
        value=format_active_cultivate_line(cultivate, mod),
        inline=False,
    )
    embed.add_field(
        name="Breakthrough odds",
        value=format_breakthrough_chance_line(breakthrough, player),
        inline=False,
    )
    return cultivate, breakthrough
