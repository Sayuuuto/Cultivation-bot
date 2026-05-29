from __future__ import annotations



from dataclasses import dataclass



from sqlalchemy import select

from sqlalchemy.orm import Session



from .inventory import get_item_name

from .models import Player, PlayerEquipment

from .ui.formatting import format_compact_number





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





def stats_from_gear_view(view, *, active: bool = True) -> EquipmentStats:

    if not view.item_id or not active:

        return EquipmentStats()

    return EquipmentStats(

        power=view.stat_power,

        defense=view.stat_defense,

        fortune=view.stat_fortune,

        insight=view.stat_insight,

    )





def stats_from_equipment_row(

    session: Session,

    eq: PlayerEquipment,

    *,

    active: bool = True,

) -> EquipmentStats:

    from .gear_stash import gear_view_is_active, resolve_equipped_gear



    view = resolve_equipped_gear(session, eq)

    if view is None:

        return EquipmentStats()

    if active and view.gear_realm is not None:

        pass

    return stats_from_gear_view(view, active=active)





def equipment_row_is_active(session: Session, eq: PlayerEquipment, player_realm_index: int) -> bool:

    from .gear_stash import gear_view_is_active, resolve_equipped_gear



    view = resolve_equipped_gear(session, eq)

    if view is None:

        return False

    return gear_view_is_active(view, player_realm_index)





def _get_player_equipment(session: Session, player_id: int) -> list[PlayerEquipment]:

    stmt = select(PlayerEquipment).where(PlayerEquipment.player_id == player_id)

    return list(session.execute(stmt).scalars().all())





def get_total_equipment_stats(session: Session, player_id: int, *, player_realm_index: int | None = None) -> EquipmentStats:

    total = EquipmentStats()

    for eq in _get_player_equipment(session, player_id):

        if player_realm_index is not None and not equipment_row_is_active(session, eq, player_realm_index):

            continue

        row = stats_from_equipment_row(session, eq, active=True)

        total.power += row.power

        total.defense += row.defense

        total.fortune += row.fortune

        total.insight += row.insight

    return total





def get_technique_tag_counts(session: Session, player_id: int, *, player_realm_index: int | None = None) -> dict[str, int]:

    from .gear_stash import resolve_equipped_gear



    counts: dict[str, int] = {}

    for eq in _get_player_equipment(session, player_id):

        view = resolve_equipped_gear(session, eq)

        if view is None or not view.technique_tag:

            continue

        if player_realm_index is not None and not equipment_row_is_active(session, eq, player_realm_index):

            continue

        tag = view.technique_tag.lower()

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





def format_equipment_slot_line(

    session: Session,

    eq: PlayerEquipment,

    *,

    player_realm_index: int | None = None,

) -> str:

    from .equipment_tiers import gear_status_label, path_label

    from .gear_stash import gear_view_is_active, resolve_equipped_gear



    view = resolve_equipped_gear(session, eq)

    if view is None or not view.item_id:

        return f"**{eq.slot.title()}** — empty"



    name = get_item_name(view.item_id)

    active = player_realm_index is None or gear_view_is_active(view, player_realm_index)

    stats = stats_from_gear_view(view, active=active)

    stat_bits = []

    if stats.power:

        stat_bits.append(f"Power {stats.power}")

    if stats.defense:

        stat_bits.append(f"Defense {stats.defense}")

    if stats.fortune:

        stat_bits.append(f"Fortune {stats.fortune}")

    if stats.insight:

        stat_bits.append(f"Insight {stats.insight}")

    if not active:

        inactive_bits = []

        if view.stat_power:

            inactive_bits.append(f"Power {view.stat_power}")

        if view.stat_defense:

            inactive_bits.append(f"Defense {view.stat_defense}")

        if view.stat_fortune:

            inactive_bits.append(f"Fortune {view.stat_fortune}")

        if view.stat_insight:

            inactive_bits.append(f"Insight {view.stat_insight}")

        stat_text = " · ".join(inactive_bits) if inactive_bits else "no rolled stats"

        stat_text = f"{stat_text} — inactive"

    else:

        stat_text = " · ".join(stat_bits) if stat_bits else "no rolled stats"

    grade = path_label(view.gear_grade or "external")

    status = gear_status_label(view, player_realm_index) if player_realm_index is not None else None

    grade_text = f" · {grade}" if active else ""

    status_text = f" · _{status}_" if status and not active else ""

    affix_text = f" · Affix: {view.affix_id}" if view.affix_id else ""

    return f"**{eq.slot.title()}** — {name}{grade_text} ({stat_text}){status_text}{affix_text}"





def format_gear_item_line(item, *, player_realm_index: int | None = None) -> str:

    from .equipment_tiers import gear_status_label, path_label

    from .gear_stash import gear_view_is_active



    name = get_item_name(item.item_id)

    view = item

    active = player_realm_index is None or gear_view_is_active(view, player_realm_index)

    stat_bits = []

    if item.stat_power:

        stat_bits.append(f"Power {item.stat_power}")

    if item.stat_defense:

        stat_bits.append(f"Defense {item.stat_defense}")

    if item.stat_fortune:

        stat_bits.append(f"Fortune {item.stat_fortune}")

    if item.stat_insight:

        stat_bits.append(f"Insight {item.stat_insight}")

    stat_text = " · ".join(stat_bits) if stat_bits else "modest qi"

    grade = path_label(item.gear_grade or "external")

    status = gear_status_label(item, player_realm_index) if player_realm_index is not None and not active else None

    status_text = f" · _{status}_" if status else ""

    affix_text = f" · Affix: {item.affix_id}" if item.affix_id else ""

    return f"**#{item.id}** · {name} · {grade} ({stat_text}){status_text}{affix_text}"





def format_stats_summary(session: Session, player_id: int, player=None, mod=None) -> str:

    from .character import get_character_modifiers

    from .combat_stats import STAT_KEYS, _load_realm_stats, _stat_from_realm, compute_combat_stats

    from .foundation import apply_foundation_bonuses

    from .models import Player



    if player is None or mod is None:

        player = session.get(Player, player_id)

        if player is None:

            return "No cultivator found."

        mod = get_character_modifiers(session, player)



    cfg = _load_realm_stats()

    realm_index = max(0, player.realm_index)

    substage = max(0, min(player.substage, 2))

    total = get_total_equipment_stats(session, player_id, player_realm_index=realm_index)

    realm_stats = {

        key: _stat_from_realm(key, realm_index, substage, cfg)

        for key in STAT_KEYS

    }

    trained_stats = dict(realm_stats)

    apply_foundation_bonuses(player, trained_stats)

    mapping = cfg["gear_mapping"]

    gear_breakdown = {

        "hp": 0,

        "defense": int(total.defense * mapping["defense_per_point"]),

        "internal_strength": int(total.power * mapping["power_internal_ratio"]),

        "external_strength": int(total.power * mapping["power_external_ratio"]),

        "agility": 0,

        "spiritual_sense": int(total.insight * mapping["insight_spiritual_sense_ratio"]),

        "comprehension": int(total.insight * mapping["insight_comprehension_ratio"]),

        "luck": int(total.fortune * mapping["fortune_luck_ratio"]),

    }

    combat = compute_combat_stats(player, session, mod)

    stat_rows = (

        ("HP", combat.max_hp, "hp"),

        ("Defense", combat.defense, "defense"),

        ("Internal", combat.internal_strength, "internal_strength"),

        ("External", combat.external_strength, "external_strength"),

        ("Agility", combat.agility, "agility"),

        ("Spirit Sense", combat.spiritual_sense, "spiritual_sense"),

        ("Comprehension", combat.comprehension, "comprehension"),

        ("Luck", combat.luck, "luck"),

    )

    lines = [

        "**Combat stats**",

        "`Stat               Final   Realm    Gear`",

    ]

    for label, final, key in stat_rows:

        gear = gear_breakdown[key]

        gear_text = f"+{format_compact_number(gear)}" if gear else "—"

        lines.append(

            f"`{label:<16} {format_compact_number(final):>7} "

            f"{format_compact_number(realm_stats[key]):>7} {gear_text:>7}`"

        )

    derived = cfg["derived"]

    realm_crit = (

        realm_stats["spiritual_sense"] * derived["crit_per_spiritual_sense"]

        + realm_stats["luck"] * derived["crit_per_luck"]

    )

    realm_dodge = realm_stats["agility"] * derived["dodge_per_agility"]

    lines.extend(

        [

            f"`{'Crit':<16} {combat.crit_chance * 100:>4.1f}% {realm_crit * 100:>5.1f}% {'—':>5}`",

            f"`{'Dodge':<16} {combat.dodge * 100:>4.1f}% {realm_dodge * 100:>5.1f}% {'—':>5}`",

            "",

            "**Gear totals**",

            format_stat_line("Power", total.power),

            format_stat_line("Defense", total.defense),

            format_stat_line("Fortune", total.fortune),

            format_stat_line("Insight", total.insight),

        ]

    )

    trained_delta = sum(max(0, trained_stats[k] - realm_stats[k]) for k in realm_stats)

    if trained_delta > 0:

        lines.append("")

        lines.append("_Realm is your stage baseline. Gear is forged equipment. Final also includes foundation training and active modifiers._")

    else:

        lines.append("")

        lines.append("_Realm is your stage baseline. Gear is forged equipment. Final also includes active modifiers._")

    return "\n".join(lines)





def format_gear_summary(session: Session, player_id: int, *, player_realm_index: int | None = None) -> str:
    from .gear_stash import list_stash, resolve_equipped_gear

    rows = _get_player_equipment(session, player_id)
    if player_realm_index is None:
        player = session.get(Player, player_id)
        player_realm_index = player.realm_index if player is not None else 0

    worn_lines = []
    for slot_row in rows:
        if resolve_equipped_gear(session, slot_row) is not None:
            worn_lines.append(format_equipment_slot_line(session, slot_row, player_realm_index=player_realm_index))

    stash = list_stash(session, player_id)

    lines: list[str] = []

    lines.append("**Worn**")

    if worn_lines:

        lines.extend(worn_lines)

    else:

        lines.append("_Nothing worn — **`/equip`** from your stash._")



    lines.append("")

    lines.append("**Stash**")

    if stash:

        display = stash[:12]

        lines.extend(format_gear_item_line(item, player_realm_index=player_realm_index) for item in display)

        if len(stash) > 12:

            lines.append(f"_…and {len(stash) - 12} more. **`/recycle`** old pieces for spirit stones._")

    else:

        lines.append("_Empty — **`/forge`** when you have materials._")



    total = get_total_equipment_stats(session, player_id, player_realm_index=player_realm_index)

    lines.extend(

        [

            "",

            "**Active totals**",

            format_stat_line("Power", total.power),

            format_stat_line("Defense", total.defense),

            format_stat_line("Fortune", total.fortune),

            format_stat_line("Insight", total.insight),

        ]

    )

    return "\n".join(lines)


