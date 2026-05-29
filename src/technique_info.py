from __future__ import annotations

import discord
from sqlalchemy.orm import Session

from .combat.catalog import TechniqueDef, get_technique, get_technique_by_manual
from .combat.loadout import PASSIVE_SLOT, get_learned_technique_ids, get_learned_techniques, get_loadout
from .combat.rarity import RARITY_EMOJI, RARITY_LABEL, rarity_damage_multiplier
from .combat.triggers import get_technique_effects
from .drop_sources import get_drop_sources
from .inventory import get_item_def, get_item_name, get_item_quantity
from .ui.formatting import TECHNIQUE_EMOJI

_ROLE_LABEL = {
    "applier": "Applier",
    "finisher": "Finisher",
    "payoff": "Payoff",
    "control": "Control",
    "sustain": "Sustain",
    "utility": "Utility",
}

_STAT_LABELS = {
    "external_strength": "External Strength",
    "internal_strength": "Internal Strength",
    "spiritual_sense": "Spiritual Sense",
    "agility": "Agility",
    "defense": "Defense",
}


_DAMAGE_EFFECT_TYPES = frozenset({"damage", "multi_hit", "lifesteal", "steal_stack_if_status"})


def _technique_damage_effects(tech: TechniqueDef) -> list:
    return [effect for effect in get_technique_effects(tech) if effect.type in _DAMAGE_EFFECT_TYPES]


def technique_base_power(tech: TechniqueDef) -> int | None:
    """Comparable base hit power before stat scaling and defense mitigation."""
    if tech.slot_type == "passive":
        return None
    if tech.damage_type == "none" and tech.base_damage <= 0 and not _technique_damage_effects(tech):
        return None

    power = float(tech.base_damage) * rarity_damage_multiplier(tech.rarity)
    for effect in get_technique_effects(tech):
        if effect.type == "multi_hit":
            hits = int(effect.params.get("hits", 2))
            hit_ratio = float(effect.params.get("hit_ratio", 0.55))
            power *= hits * hit_ratio
            break

    if power <= 0:
        return None
    return max(1, int(round(power)))


def _format_scaling_fragment(tech: TechniqueDef, *, markdown: bool) -> str:
    if tech.scaling_ratio <= 0:
        return ""
    stat = _STAT_LABELS.get(tech.scaling_stat, tech.scaling_stat.replace("_", " ").title())
    ratio = f"{tech.scaling_ratio:g}"
    if markdown:
        return f" · +**{ratio}** per **{stat}**"
    return f" · +{ratio} per {stat}"


def _format_damage_bonus_notes(tech: TechniqueDef, *, markdown: bool) -> list[str]:
    notes: list[str] = []
    for effect in get_technique_effects(tech):
        if effect.type == "multi_hit":
            hits = int(effect.params.get("hits", 2))
            hit_pct = int(round(float(effect.params.get("hit_ratio", 0.55)) * 100))
            note = f"{hits} hits × {hit_pct}% each"
            notes.append(f"**{note}**" if markdown else note)
            continue
        bonus = effect.params.get("bonus_ratio")
        if not bonus:
            continue
        pct = int(round(float(bonus) * 100))
        if effect.params.get("requires_bleeding") or effect.params.get("requires_status") == "bleed":
            note = f"+{pct}% vs bleeding"
        elif effect.params.get("requires_burning"):
            note = f"+{pct}% vs burning"
        elif effect.params.get("requires_status") == "poison":
            note = f"+{pct}% vs poison"
        else:
            note = f"+{pct}% bonus damage"
        notes.append(f"**{note}**" if markdown else note)
    return notes


def format_technique_base_power(tech: TechniqueDef, *, markdown: bool = True) -> str | None:
    """Player-facing base power line for comparing technique strength."""
    power = technique_base_power(tech)
    if power is None:
        return None

    scaling = _format_scaling_fragment(tech, markdown=markdown)
    notes = _format_damage_bonus_notes(tech, markdown=markdown)
    note_text = ""
    if notes:
        joiner = " · " if markdown else " · "
        note_text = joiner + joiner.join(notes)

    if markdown:
        return f"Base power **{power}**{scaling}{note_text}"
    return f"Base power {power}{scaling}{note_text}"


def format_art_type_label(tech: TechniqueDef) -> str:
    """Player-facing active vs passive distinction."""
    if tech.slot_type == "passive":
        base = (
            "**Passive art** — always on while equipped in your **passive slot**. "
            "You do not press a button to use it in combat."
        )
    else:
        base = (
            "**Active art** — equip to **slots 1–4**. "
            "You choose when to use it in combat; each active has its own cooldown."
        )
    if tech.karma_on_use:
        base += " Using this art outside adventures can shift your karma."
    return base


def format_technique_combat_summary(tech: TechniqueDef) -> str:
    """Plain-language combat behavior for player-facing cards."""
    lines: list[str] = []

    if tech.slot_type == "passive":
        lines.append("**Always on** while slotted in your passive slot — no button, no cooldown.")
    else:
        cd = tech.cooldown
        lines.append(
            f"**Manual use in combat** — {'no cooldown' if cd <= 0 else f'**{cd}**-turn cooldown after use'}."
        )

    base_power = format_technique_base_power(tech, markdown=True)
    if base_power:
        lines.append(base_power)
    elif tech.damage_type and tech.damage_type != "none":
        stat = _STAT_LABELS.get(tech.scaling_stat, tech.scaling_stat.replace("_", " ").title())
        lines.append(f"Deals **{tech.damage_type}** damage scaling with **{stat}**.")

    if tech.status_id and tech.status_chance > 0:
        pct = int(round(tech.status_chance * 100))
        lines.append(f"**{pct}%** chance to inflict **{tech.status_id.title()}** on hit.")
        from .combat.rules import load_combat_rules

        rule = load_combat_rules().statuses.get(tech.status_id)
        if tech.status_id == "stun":
            lines.append("Stunned foes **cannot act** on their turn.")
        elif rule is not None and rule.skip_turn_chance > 0:
            pct = int(round(rule.skip_turn_chance * 100))
            lines.append(
                f"Each turn, afflicted foes have **{pct}%** chance to lose their action to fear."
            )
        elif rule is not None and rule.propagates:
            spread = int(round(rule.spread_chance * 100))
            lines.append(f"**Burn** can leap to other foes (**{spread}%** chance per carrier).")
        elif rule is not None and rule.damage_mult < 1.0:
            weaken = int(round((1.0 - rule.damage_mult) * 100))
            lines.append(f"Afflicted foes deal **{weaken}%** less damage.")

    if tech.heal_ratio > 0:
        pct = int(round(tech.heal_ratio * 100))
        lines.append(f"Can restore **{pct}%** of damage dealt as HP under the right conditions.")

    for trig in tech.passive_triggers:
        if trig.type == "on_hit_bleed_chance":
            pct = int(float(trig.params.get("chance", 0)) * 100)
            lines.append(f"Your attacks have **{pct}%** chance to inflict **Bleed**.")
        elif trig.type == "burn_damage_bonus":
            pct = int(float(trig.params.get("bonus", 0)) * 100)
            lines.append(f"Burn techniques deal **+{pct}%** damage.")
        elif trig.type == "poison_damage_bonus":
            pct = int(float(trig.params.get("bonus", 0)) * 100)
            lines.append(f"Poison techniques deal **+{pct}%** damage.")
        elif trig.type == "heal_below_threshold":
            pct = int(float(trig.params.get("heal_pct", 0)) * 100)
            threshold = int(float(trig.params.get("threshold", 0.3)) * 100)
            lines.append(
                f"When you fall below **{threshold}%** HP, heal **{pct}%** of your max HP "
                f"(cooldown **{int(trig.params.get('cooldown', 0))}** turns)."
            )
        elif trig.type == "cleanse_stun_shield":
            pct = int(float(trig.params.get("shield_pct", 0)) * 100)
            lines.append(f"When **Stunned** or **Sealed**, gain a shield worth **{pct}%** of max HP.")

    if len(lines) == 1 and tech.damage_type == "none":
        lines.append("Support or trigger effects — read the description above.")

    return "\n".join(lines)


def format_technique_effect_plain(tech: TechniqueDef) -> str:
    """Single-block effect text for skill card images (no markdown)."""
    if tech.slot_type == "passive":
        trigger_lines: list[str] = []
        for trig in tech.passive_triggers:
            if trig.type == "on_hit_bleed_chance":
                pct = int(float(trig.params.get("chance", 0)) * 100)
                trigger_lines.append(f"Your attacks have a {pct}% chance to cause bleeding.")
            elif trig.type == "burn_damage_bonus":
                pct = int(float(trig.params.get("bonus", 0)) * 100)
                trigger_lines.append(f"Burn techniques deal +{pct}% damage.")
            elif trig.type == "poison_damage_bonus":
                pct = int(float(trig.params.get("bonus", 0)) * 100)
                trigger_lines.append(f"Poison techniques deal +{pct}% damage.")
            elif trig.type == "heal_below_threshold":
                pct = int(float(trig.params.get("heal_pct", 0)) * 100)
                threshold = int(float(trig.params.get("threshold", 0.3)) * 100)
                trigger_lines.append(
                    f"When below {threshold}% HP, heal {pct}% max HP "
                    f"(cooldown {int(trig.params.get('cooldown', 0))} turns)."
                )
            elif trig.type == "cleanse_stun_shield":
                pct = int(float(trig.params.get("shield_pct", 0)) * 100)
                trigger_lines.append(
                    f"When stunned or sealed, gain a shield worth {pct}% of max HP."
                )
        if trigger_lines:
            return " ".join(trigger_lines)
        desc = (tech.description or "").strip()
        return desc if desc else "Passive effect while equipped in the passive slot."

    parts: list[str] = []
    base_power = format_technique_base_power(tech, markdown=False)
    if base_power:
        parts.append(f"{base_power}.")
    elif tech.damage_type and tech.damage_type != "none":
        stat = _STAT_LABELS.get(tech.scaling_stat, tech.scaling_stat.replace("_", " ").title())
        parts.append(f"Deals {tech.damage_type} damage scaling with {stat}.")
    if tech.status_id and tech.status_chance > 0:
        pct = int(round(tech.status_chance * 100))
        status = tech.status_id.replace("_", " ")
        if status == "bleed":
            status = "bleeding"
        parts.append(f"{pct}% chance to cause {status}.")
    if tech.heal_ratio > 0:
        pct = int(round(tech.heal_ratio * 100))
        parts.append(f"Can restore {pct}% of damage dealt as HP.")
    if parts:
        return " ".join(parts)
    desc = (tech.description or "").strip()
    return desc if desc else "Support or trigger effects."


def _technique_header_tags(tech: TechniqueDef) -> str:
    emoji = TECHNIQUE_EMOJI.get(tech.category, "📖")
    rarity = RARITY_LABEL.get(tech.rarity, tech.rarity.title())
    rarity_emoji = RARITY_EMOJI.get(tech.rarity, "⚪")
    role = _ROLE_LABEL.get(tech.role, tech.role.title())
    align = {"righteous": "☀️ Righteous", "demonic": "🌑 Demonic", "neutral": "⚖️ Neutral"}.get(
        tech.alignment, "⚖️ Neutral"
    )
    tier = tech.tier.title()
    realm = "Mortal" if tech.min_realm <= 0 else f"Realm **{tech.min_realm}+**"
    return (
        f"{emoji} **{tech.category.title()}** · {rarity_emoji} {rarity} · {tier} tier · "
        f"**{role}** · {align} · {realm}"
    )


def _player_technique_status(
    session: Session,
    player_id: int,
    tech: TechniqueDef,
    *,
    manual_item_id: str | None = None,
) -> str:
    learned = tech.technique_id in get_learned_technique_ids(session, player_id)
    loadout = get_loadout(session, player_id)
    slot = next((s for s, tid in loadout.items() if tid == tech.technique_id), None)
    qty = get_item_quantity(session, player_id, manual_item_id) if manual_item_id else 0

    parts: list[str] = []
    if learned:
        parts.append("✅ **Studied** — you know this art.")
    elif qty > 0:
        parts.append(f"📜 **Manual in bag** (×{qty}) — unlock it from **`/techniques`**.")
    else:
        parts.append("❓ **Not studied** — find a manual first.")

    if slot:
        if slot == PASSIVE_SLOT:
            parts.append("⚔️ **Equipped** in your **passive slot** (always on in combat).")
        else:
            parts.append(f"⚔️ **Equipped** in **active slot {slot}** (manual use in combat).")
    elif learned:
        if tech.slot_type == "passive":
            parts.append("Not equipped — open **`/techniques`** → **Equip Skill** → **passive slot**.")
        else:
            parts.append("Not equipped — open **`/techniques`** → **Equip Skill** → **slots 1–4**.")

    return "\n".join(parts)


def append_manual_technique_fields(embed: discord.Embed, tech: TechniqueDef) -> None:
    embed.description = tech.description or embed.description
    embed.add_field(name="Art type", value=format_art_type_label(tech), inline=False)
    embed.add_field(name="Path", value=_technique_header_tags(tech), inline=False)
    embed.add_field(name="⚔️ In combat", value=format_technique_combat_summary(tech), inline=False)


def build_technique_detail_embed(
    tech: TechniqueDef,
    *,
    session: Session,
    player_id: int,
    manual_item_id: str | None = None,
) -> discord.Embed:
    color = {
        "sword": discord.Color.red(),
        "fire": discord.Color.orange(),
        "body": discord.Color.green(),
        "soul": discord.Color.purple(),
        "utility": discord.Color.blue(),
        "passive": discord.Color.gold(),
    }.get(tech.category, discord.Color.dark_purple())

    embed = discord.Embed(
        title=f"{TECHNIQUE_EMOJI.get(tech.category, '📖')} {tech.name}",
        description=tech.description or "_No scripture recorded for this art._",
        color=color,
    )
    embed.add_field(name="Art type", value=format_art_type_label(tech), inline=False)
    embed.add_field(name="Path", value=_technique_header_tags(tech), inline=False)
    embed.add_field(name="⚔️ In combat", value=format_technique_combat_summary(tech), inline=False)

    embed.add_field(
        name="📋 Your status",
        value=_player_technique_status(session, player_id, tech, manual_item_id=manual_item_id),
        inline=False,
    )

    if manual_item_id:
        sources = get_drop_sources(manual_item_id)
        if sources:
            obtain = "\n".join(f"• **{src.label}** — {src.via}" for src in sources[:4])
            embed.add_field(name="📍 How to find more manuals", value=obtain, inline=False)

    embed.set_footer(text="`/techniques` — equip, unlock manuals, and manage your library")
    return embed


def resolve_technique_inspect_target(
    session: Session,
    player_id: int,
    raw: str,
) -> tuple[TechniqueDef | None, str | None]:
    text = raw.strip()
    if not text:
        return None, None

    normalized = text.lower().replace(" ", "_").replace("-", "_")

    tech = get_technique(normalized)
    if tech is not None:
        return tech, tech.manual_item_id

    tech = get_technique_by_manual(normalized)
    if tech is not None and get_item_quantity(session, player_id, normalized) > 0:
        return tech, normalized

    lower = text.lower()
    candidates: list[tuple[TechniqueDef, str | None]] = []

    for learned in get_learned_techniques(session, player_id):
        if learned.name.lower() == lower or learned.technique_id == normalized:
            candidates.append((learned, learned.manual_item_id))

    for stack_item_id, _label in _manual_options_raw(session, player_id):
        item = get_item_def(stack_item_id)
        if item is None:
            continue
        t = get_technique_by_manual(stack_item_id)
        if t is None:
            continue
        haystack = f"{t.technique_id} {t.name} {stack_item_id} {item.name}".lower()
        if lower in haystack or all(part in haystack for part in lower.split()):
            candidates.append((t, stack_item_id))

    if len(candidates) == 1:
        return candidates[0]
    return None, None


def _manual_options_raw(session: Session, player_id: int) -> list[tuple[str, str]]:
    from .command_choices import list_player_manuals

    return list_player_manuals(session, player_id)


def list_technique_inspect_options(session: Session, player_id: int) -> list[tuple[str, str]]:
    """Autocomplete: learned arts + unread manuals in bag."""
    options: dict[str, str] = {}

    for tech in get_learned_techniques(session, player_id):
        kind = "Passive" if tech.slot_type == "passive" else "Active"
        label = f"{tech.name} ({kind})"
        options[tech.technique_id] = label

    basic = get_technique("basic_strike")
    if basic is not None and basic.technique_id not in options:
        options[basic.technique_id] = f"{basic.name} (Active)"

    for item_id, label in _manual_options_raw(session, player_id):
        tech = get_technique_by_manual(item_id)
        if tech is None:
            continue
        if tech.technique_id in options:
            continue
        kind = "Passive" if tech.slot_type == "passive" else "Active"
        options[item_id] = f"{label} · {kind}"

    rows = list(options.items())
    rows.sort(key=lambda row: row[1].lower())
    return rows
