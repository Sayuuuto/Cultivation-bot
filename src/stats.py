from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .inventory import get_item_name
from .models import Player, PlayerEquipment


@dataclass
class EquipmentStats:
    power: int = 0
    defense: int = 0
    fortune: int = 0
    insight: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "power": self.power,
            "defense": self.defense,
            "fortune": self.fortune,
            "insight": self.insight,
        }


def stats_from_equipment_row(eq: PlayerEquipment) -> EquipmentStats:
    if not eq.item_id:
        return EquipmentStats()
    return EquipmentStats(
        power=eq.stat_power,
        defense=eq.stat_defense,
        fortune=eq.stat_fortune,
        insight=eq.stat_insight,
    )


def _get_player_equipment(session: Session, player_id: int) -> list[PlayerEquipment]:
    stmt = select(PlayerEquipment).where(PlayerEquipment.player_id == player_id)
    return list(session.execute(stmt).scalars().all())


def get_total_equipment_stats(session: Session, player_id: int) -> EquipmentStats:
    total = EquipmentStats()
    for eq in _get_player_equipment(session, player_id):
        row = stats_from_equipment_row(eq)
        total.power += row.power
        total.defense += row.defense
        total.fortune += row.fortune
        total.insight += row.insight
    return total


def get_technique_tag_counts(session: Session, player_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for eq in _get_player_equipment(session, player_id):
        if not eq.item_id or not eq.technique_tag:
            continue
        tag = eq.technique_tag.lower()
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def equipment_stats_to_modifiers(stats: EquipmentStats) -> dict[str, float]:
    """Map forged gear stats into character modifier keys."""
    return {
        "adventure_success": stats.power * 0.008,
        "pvp_power": stats.power * 0.004,
        "adventure_defense": stats.defense * 0.010,
        "drop_luck": stats.fortune * 0.012,
        "rare_event_mult": 1.0 + stats.insight * 0.020,
    }


def format_stat_line(label: str, value: int) -> str:
    if value <= 0:
        return f"**{label}** — —"
    return f"**{label}** — {value}"


def format_equipment_slot_line(eq: PlayerEquipment) -> str:
    if not eq.item_id:
        affix = f" · {eq.affix_id}" if eq.affix_id else ""
        return f"**{eq.slot.title()}** — empty{affix}"

    name = get_item_name(eq.item_id)
    stats = stats_from_equipment_row(eq)
    stat_bits = []
    if stats.power:
        stat_bits.append(f"Power {stats.power}")
    if stats.defense:
        stat_bits.append(f"Defense {stats.defense}")
    if stats.fortune:
        stat_bits.append(f"Fortune {stats.fortune}")
    if stats.insight:
        stat_bits.append(f"Insight {stats.insight}")
    stat_text = " · ".join(stat_bits) if stat_bits else "no rolled stats"
    affix_text = f" · Affix: {eq.affix_id}" if eq.affix_id else ""
    return f"**{eq.slot.title()}** — {name} ({stat_text}){affix_text}"


def format_stats_summary(session: Session, player_id: int, player=None, mod=None) -> str:
    from .character import get_character_modifiers
    from .combat_stats import compute_combat_stats, format_combat_stats_block
    from .models import Player

    if player is None or mod is None:
        player = session.get(Player, player_id)
        if player is None:
            return "No cultivator found."
        mod = get_character_modifiers(session, player)

    total = get_total_equipment_stats(session, player_id)
    from .foundation import format_foundation_summary

    combat = compute_combat_stats(player, session, mod)
    lines = [
        format_foundation_summary(player),
        "",
        "**Combat**",
        format_combat_stats_block(combat),
        "",
        "**Gear totals**",
        format_stat_line("Power", total.power),
        format_stat_line("Defense", total.defense),
        format_stat_line("Fortune", total.fortune),
        format_stat_line("Insight", total.insight),
    ]
    lines.append("")
    lines.append(
        "_Gear Power splits into internal & external strength. Fortune → Luck. "
        "Insight → Spirit Sense & Comprehension._"
    )
    return "\n".join(lines)
