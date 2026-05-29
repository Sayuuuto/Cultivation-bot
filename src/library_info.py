from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import discord

from .combat.catalog import TechniqueDef, load_technique_catalog
from .combat.rarity import RARITY_EMOJI, RARITY_LABEL
from .technique_info import format_art_type_label, format_technique_combat_summary
from .content import get_areas, get_dungeons, load_all_content
from .inventory import load_item_catalog
from .karma import KARMA_DEMONIC_THRESHOLD, KARMA_RIGHTEOUS_THRESHOLD
from .player_guides import guide_text
from .manuals import (
    BREAKTHROUGH_FAIL_FRAGMENT_CHANCE,
    BREAKTHROUGH_MANUAL_CHANCE,
    CULTIVATE_FRAGMENT_CHANCE,
    CULTIVATE_MANUAL_CHANCE,
    MANUAL_CRAFT_INPUTS,
    load_manual_pools,
)

CONFIG_ROOT = Path(__file__).resolve().parent.parent / "config"

CATEGORY_ORDER = ("sword", "fire", "body", "soul", "utility", "passive")
CATEGORY_TITLES = {
    "sword": "Sword",
    "fire": "Fire",
    "body": "Body",
    "soul": "Soul",
    "utility": "Utility",
    "passive": "Passives",
}
CATEGORY_EMOJI = {
    "sword": "⚔️",
    "fire": "🔥",
    "body": "💪",
    "soul": "👁️",
    "utility": "✨",
    "passive": "📜",
}
CATEGORY_COLORS: dict[str, int] = {
    "sword": 0xE74C3C,
    "fire": 0xE67E22,
    "body": 0x2ECC71,
    "soul": 0x9B59B6,
    "utility": 0x3498DB,
    "passive": 0xF1C40F,
}
ALIGNMENT_EMOJI = {
    "righteous": "☀️",
    "demonic": "😈",
    "neutral": "⚖️",
}
SOURCE_ICONS = {
    "hunt": "🐺",
    "adventure": "🌿",
    "cultivate": "🧘",
    "breakthrough": "⚡",
    "dungeon": "🏚️",
    "shop": "🏪",
    "craft": "📜",
}
TIER_LABEL = {"mortal": "Mortal", "earth": "Earth"}
ROLE_LABEL = {
    "applier": "Applier",
    "finisher": "Finisher",
    "payoff": "Payoff",
    "control": "Control",
    "sustain": "Sustain",
    "utility": "Utility",
}

# Short player-facing summaries for weighted pools (avoids listing every adventure choice).
POOL_SUMMARY: dict[str, str] = {
    "elder_mortal": "Rare event **Wandering Elder** on `/adventure`",
    "inheritance_earth": "Rare event **Inheritance Fragment** on `/adventure` (Moonwell+)",
    "righteous_elder": "Righteous `/adventure` choices — help elders, heal rivals, free beasts",
    "demonic_elder": "Demonic `/adventure` choices — rob, execute, accept dark offers",
    "neutral_wanderer": "Neutral `/adventure` trade / information choices",
    "cultivate_enlightenment": (
        f"Passive cultivation enlightenment (~{CULTIVATE_MANUAL_CHANCE * 100:.1f}%) · "
        "**Heavenly Glimpse** dao event · Mortal-tier `/breakthrough`"
    ),
    "breakthrough_success": (
        f"Successful `/breakthrough` (~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}%, Earth realm+, neutral karma)"
    ),
    "righteous_breakthrough": (
        f"Successful `/breakthrough` (~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}%, karma **+{KARMA_RIGHTEOUS_THRESHOLD}+**)"
    ),
    "demonic_breakthrough": (
        f"Successful `/breakthrough` (~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}%, karma **{KARMA_DEMONIC_THRESHOLD}** or lower)"
    ),
    "craft_mortal": "`/craft manual` binding pool (Mortal realm)",
    "craft_earth": "`/craft manual` binding pool (Earth realm+)",
    "shop_unidentified": "**Unidentified Scroll** — `/shop buy` (75 stones, **Common–Uncommon** only)",
    "dungeon_earth": "**Blackwind Cavern** weekly boss manual (once per 7 days, Earth pool)",
    "hunt_bamboo_elite": "**Mist Fang Wolf** elite `/hunt` pool (Mortal Grove)",
    "hunt_ashen_elite": "**Fire Mantis** elite `/hunt` pool (Qi Refining Cliffs)",
    "hunt_moonwell_elite": "**Ruin Devourer** elite `/hunt` pool (Foundation Ruins)",
    "hunt_mistwood_elite": "**Mist Hound** elite `/hunt` pool (Mortal Grove)",
    "hunt_verdant_elite": "**Canopy Serpent** elite `/hunt` pool (Nascent Soul Peak)",
    "hunt_swamp_elite": "**Swamp Hollow King** elite `/hunt` pool (Core Formation Swamp)",
}


@dataclass
class ManualEntry:
    manual_item_id: str
    technique: TechniqueDef
    sources: list[str] = field(default_factory=list)


def _add_source(index: dict[str, list[str]], manual_id: str, source: str) -> None:
    bucket = index.setdefault(manual_id, [])
    if source not in bucket:
        bucket.append(source)


def _load_json(name: str) -> dict:
    with (CONFIG_ROOT / name).open(encoding="utf-8") as f:
        return json.load(f)


def _collect_pool_references() -> set[str]:
    """Pool IDs that are actually wired in game config/code."""
    wired: set[str] = set()

    from .adventure import RARE_EVENT_REWARDS

    for rewards in RARE_EVENT_REWARDS.values():
        pool_id = rewards.get("manual_pool")
        if pool_id:
            wired.add(str(pool_id))

    encounters = _load_json("adventure_encounters.json")
    for area_encounters in encounters.values():
        for encounter in area_encounters:
            for choice in encounter.get("choices", []):
                pool_id = choice.get("manual_pool")
                if pool_id:
                    wired.add(str(pool_id))

    cultivate_events = _load_json("cultivate_events.json")
    for event in cultivate_events.get("events", []):
        pool_id = event.get("manual_pool")
        if pool_id:
            wired.add(str(pool_id))

    wired.update(
        {
            "cultivate_enlightenment",
            "breakthrough_success",
            "righteous_breakthrough",
            "demonic_breakthrough",
            "craft_mortal",
            "craft_earth",
            "shop_unidentified",
            "dungeon_earth",
            "hunt_bamboo_elite",
            "hunt_ashen_elite",
            "hunt_moonwell_elite",
            "hunt_mistwood_elite",
            "hunt_verdant_elite",
            "hunt_swamp_elite",
        }
    )
    return wired


def _build_manual_source_index() -> dict[str, list[str]]:
    load_all_content()
    load_item_catalog()
    index: dict[str, list[str]] = {}
    wired_pools = _collect_pool_references()
    pools = load_manual_pools()

    for pool_id, entries in pools.items():
        if pool_id not in wired_pools:
            continue
        summary = POOL_SUMMARY.get(pool_id)
        if not summary:
            continue
        for manual_id, _weight in entries:
            _add_source(index, manual_id, summary)

    hunt = _load_json("hunt_targets.json")
    for area_id, data in hunt.items():
        area = get_areas().get(area_id)
        area_name = area.name if area else area_id
        for beast in data.get("beasts", []):
            if beast.get("combat_tier") != "elite":
                continue
            pool_id = {
                "mortal_grove": "hunt_bamboo_elite",
                "qi_refining_cliffs": "hunt_ashen_elite",
                "foundation_ruins": "hunt_moonwell_elite",
                "core_formation_swamp": "hunt_swamp_elite",
                "nascent_soul_peak": "hunt_verdant_elite",
                "spirit_severing_abyss": "hunt_swamp_elite",
                "void_refinement_expanse": "hunt_moonwell_elite",
                "immortal_ascension_gate": "hunt_verdant_elite",
                "heavenly_transcendence_domain": "hunt_ashen_elite",
                "immortal_monarch_court": "hunt_swamp_elite",
            }.get(area_id)
            if not pool_id:
                continue
            summary = POOL_SUMMARY.get(pool_id)
            if not summary:
                continue
            for manual_id, _weight in pools.get(pool_id, []):
                _add_source(index, manual_id, summary)

    shop = _load_json("shop.json")
    for listing in shop.values():
        if listing.get("type") == "item" and listing.get("category") == "manual":
            item_id = listing.get("item_id")
            if item_id:
                price = listing.get("price", "?")
                _add_source(
                    index,
                    item_id,
                    f"Spirit Stone Shop — **{listing.get('name', item_id)}** ({price} stones, `/shop buy`)",
                )
        elif listing.get("type") == "manual_gamble":
            pass  # handled per-manual via shop_unidentified pool in build_manual_catalog

    for dungeon in get_dungeons().values():
        for drop in dungeon.bonus_drops:
            if drop.item_id.startswith("manual_"):
                pct = int(drop.chance * 100) if drop.chance <= 1 else int(drop.chance)
                _add_source(
                    index,
                    drop.item_id,
                    f"**{dungeon.name}** bonus loot (`/dungeon`, ~{pct}% on clear)",
                )

    return index


def build_manual_catalog() -> list[ManualEntry]:
    catalog = load_technique_catalog()
    sources = _build_manual_source_index()
    shop_pool_ids = {manual_id for manual_id, _ in load_manual_pools().get("shop_unidentified", [])}
    shop_summary = POOL_SUMMARY["shop_unidentified"]
    entries: list[ManualEntry] = []
    for tech in catalog.values():
        if not tech.manual_item_id:
            continue
        manual_sources = list(sources.get(tech.manual_item_id, []))
        if tech.manual_item_id in shop_pool_ids and shop_summary not in manual_sources:
            manual_sources.append(shop_summary)
        entries.append(
            ManualEntry(
                manual_item_id=tech.manual_item_id,
                technique=tech,
                sources=manual_sources,
            )
        )
    entries.sort(
        key=lambda e: (
            CATEGORY_ORDER.index(e.technique.category) if e.technique.category in CATEGORY_ORDER else 99,
            e.technique.tier,
            e.technique.name,
        )
    )
    return entries


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _chip(label: str) -> str:
    return f"`{label}`"


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _subtext(text: str) -> str:
    return f"-# {text}"


def _technique_tags(entry: ManualEntry) -> str:
    tech = entry.technique
    slot = "Passive" if tech.slot_type == "passive" else "Active"
    tier = TIER_LABEL.get(tech.tier, tech.tier.title())
    rarity = RARITY_LABEL.get(getattr(tech, "rarity", "common"), "Common")
    rarity_emoji = RARITY_EMOJI.get(getattr(tech, "rarity", "common"), "⚪")
    dao = ALIGNMENT_EMOJI.get(tech.alignment, "⚖️")
    role = ROLE_LABEL.get(getattr(tech, "role", ""), "")
    role_bit = f" · {_chip(role)}" if role else ""
    realm_bit = f" · realm **{tech.min_realm}+**"
    return f"{rarity_emoji} {_chip(rarity)} · {_chip(tier)} · {_chip(slot)}{role_bit}{realm_bit} · {dao}"


def _technique_field_name(entry: ManualEntry) -> str:
    tech = entry.technique
    emoji = CATEGORY_EMOJI.get(tech.category, "📖")
    tier = TIER_LABEL.get(tech.tier, tech.tier.title())
    dao = ALIGNMENT_EMOJI.get(tech.alignment, "⚖️")
    return f"{emoji} {tech.name}  ·  {tier}  ·  {dao}"


def _technique_card(entry: ManualEntry, *, include_obtain: bool = True) -> str:
    tech = entry.technique
    lines = [
        f"**Tags** · {_technique_tags(entry)}",
        f"**Description**\n{_quote(tech.description)}",
        f"**Art type**\n{_quote(format_art_type_label(tech))}",
        f"**In combat**\n{_quote(format_technique_combat_summary(tech))}",
    ]
    if include_obtain:
        if entry.sources:
            obtain_lines = [f"{idx}. {src}" for idx, src in enumerate(entry.sources, start=1)]
            obtain = "\n".join(obtain_lines)
        else:
            obtain = "1. `/shop buy` · `/hunt` elites · `/adventure` · `/dungeon` · `/craft manual`"
        lines.append(f"**Obtain**\n{obtain}")
        lines.append(_subtext("Duplicate manuals you already know become 2× Technique Fragment."))
    if tech.min_realm > 0:
        lines.append(
            _subtext(
                f"Above your realm, drops may arrive **sealed** — {guide_text('sealed_manual', 'summary')}"
            )
        )
    return _truncate("\n\n".join(lines), 1024)


def _technique_bullet(entry: ManualEntry) -> str:
    tech = entry.technique
    return (
        f"▸ **{tech.name}** · {_technique_tags(entry)}\n"
        f"{_quote(tech.description)}"
    )


def _group_entries_by_category(entries: list[ManualEntry]) -> dict[str, list[ManualEntry]]:
    grouped: dict[str, list[ManualEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.technique.category, []).append(entry)
    return grouped


def build_category_list(entries: list[ManualEntry]) -> str:
    return "\n".join(_technique_bullet(entry) for entry in entries)


def build_master_catalog_text() -> str:
    entries = build_manual_catalog()
    sections: list[str] = []
    for category in CATEGORY_ORDER:
        cat_entries = _group_entries_by_category(entries).get(category, [])
        if not cat_entries:
            continue
        emoji = CATEGORY_EMOJI.get(category, "📖")
        title = CATEGORY_TITLES.get(category, category.title())
        bullets = "\n".join(_technique_bullet(entry) for entry in cat_entries)
        sections.append(
            f"**{emoji} {title.upper()}**\n"
            f"{_subtext(f'{len(cat_entries)} manual(s) in this path')}\n\n"
            f"{bullets}"
        )
    return "\n\n".join(sections)


def _split_text_chunks(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_master_catalog_embeds() -> list[discord.Embed]:
    text = build_master_catalog_text()
    chunks = _split_text_chunks(text)
    embeds: list[discord.Embed] = []
    for idx, chunk in enumerate(chunks):
        suffix = f" ({idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
        embeds.append(
            discord.Embed(
                title=f"📋 Master Catalog{suffix}",
                description=chunk,
                color=discord.Color.blurple(),
            ).set_author(name="Scripture Pavilion · All 26 Manuals")
        )
    if embeds:
        embeds[-1].set_footer(text="26 manuals · see category pages below for full obtain paths")
    return embeds


def format_manual_entry(entry: ManualEntry) -> str:
    """Plain-text block (legacy / page export)."""
    return f"**{entry.technique.name}** — {_technique_tags(entry)}\n{_technique_card(entry)}"


def build_sources_overview_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🗺️ Where Manuals Come From",
        description=(
            "Every technique beyond **Basic Strike** needs a manual scroll.\n"
            f"{_quote('Study with `/learn`, slot with `/equip-technique`, review with `/techniques`.')}"
        ),
        color=discord.Color.dark_gold(),
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['hunt']} `/hunt` · Elite beasts",
        value=_quote("Mist Fang Wolf, Fire Mantis, Ruin Devourer drop manuals on victory."),
        inline=True,
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['adventure']} `/adventure` · Story",
        value=_quote("Karma-weighted pools on moral choices + rare Wandering Elder & Inheritance Fragment."),
        inline=True,
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['cultivate']} `/cultivate` · Dao",
        value=_quote(
            f"~{CULTIVATE_MANUAL_CHANCE * 100:.1f}% manual chance; Heavenly Glimpse and other dao events."
        ),
        inline=True,
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['breakthrough']} `/breakthrough` · Ascend",
        value=_quote(
            f"~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}% manual on success; pool shifts with karma tier."
        ),
        inline=True,
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['dungeon']} `/dungeon` · Cavern",
        value=_quote("Blackwind Cavern bonus loot + one weekly boss manual."),
        inline=True,
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['shop']} `/shop buy` · Market",
        value=_quote("Technique pamphlets + Unidentified Scroll gamble (75 stones)."),
        inline=True,
    )
    embed.add_field(
        name=f"{SOURCE_ICONS['craft']} `/craft manual` · Bind",
        value=_quote("Random Mortal/Earth pool from fragments + blank scroll + spirit ink."),
        inline=False,
    )
    embed.set_footer(text="Tip: matching karma + dao alignment greatly improves adventure drop weights.")
    return embed


def build_elite_hunt_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎯 Elite Hunt Manual Drops",
        description=(
            "Guaranteed manual rolls when you **defeat** these elites with `/hunt`.\n"
            f"{_subtext('Elite hunts are the most reliable way to target specific manuals.')}"
        ),
        color=discord.Color.dark_grey(),
    )
    embed.add_field(
        name="🐺 Mist Fang Wolf",
        value="**Area:** Mortal Grove\n**Manuals:** __Swift Slash__, __Keen Focus__",
        inline=True,
    )
    embed.add_field(
        name="🦗 Fire Mantis",
        value="**Area:** Qi Refining Cliffs\n**Manuals:** __Flame Burst__, __Iron Cleave__",
        inline=True,
    )
    embed.add_field(
        name="👁️ Ruin Devourer",
        value="**Area:** Foundation Ruins\n**Manuals:** __Void Pulse__, __Blood Feast__",
        inline=True,
    )
    embed.add_field(
        name="Quick tip",
        value=_quote("Win the fight — manuals roll on **victory**, not on engage or flee."),
        inline=False,
    )
    embed.set_footer(text="Use /techniques in-game for your personal loadout and unread manuals.")
    return embed


def build_library_intro_markdown() -> str:
    frag = MANUAL_CRAFT_INPUTS.get("technique_fragment", 3)
    scroll = MANUAL_CRAFT_INPUTS.get("blank_scroll", 1)
    ink = MANUAL_CRAFT_INPUTS.get("spirit_ink", 1)
    return (
        "# 📚 Scripture Pavilion — Technique Archive\n\n"
        "-# Your sect's living manual — reposted with `/post-library`\n\n"
        "**Basic Strike** is free at the start. Every other technique needs a **manual** scroll.\n\n"
        "> 💡 **`/learn`** consumes a manual · **`/equip-technique`** slots it · **`/techniques`** shows your build\n\n"
        "### Study & equip\n"
        "• **`/learn manual:<name>`** — consume a manual from your bag\n"
        "• **`/equip-technique`** — **active slots 1–4** or **passive slot** (labels show art type)\n"
        "• **`/techniques`** — full build view with art types and study/equip menus\n\n"
        "### Button combat (`/hunt` & `/adventure` fights)\n"
        "> Engage with your technique buttons, **Pass Turn**, or **Flee**.\n"
        "> Loadout matters — passives and status combos define your build.\n\n"
        "### Duplicates\n"
        "If you already know a technique, duplicate manuals crumble into **2× Technique Fragment**.\n\n"
        "### Bind your own manual (`/craft manual`)\n"
        f"**{frag}× Technique Fragment** + **{scroll}× Blank Scroll** + **{ink}× Spirit Ink** → "
        "random manual (Mortal pool at realm 0, Earth pool at realm 1+).\n\n"
        "### Karma & manual drops\n"
        f"Adventure moral choices shift karma (−100…+100). At **+{KARMA_RIGHTEOUS_THRESHOLD}+** Righteous or "
        f"**{KARMA_DEMONIC_THRESHOLD}** Demonic, weighted manual pools favor matching alignments "
        "(☀️ righteous arts for righteous daoists, 😈 demonic arts for demonic).\n"
        "-# Neutral techniques are slightly easier for everyone to roll.\n\n"
        "### Where manuals come from\n"
        "• **`/hunt`** — elite beasts: Mist Fang Wolf, Fire Mantis, Ruin Devourer\n"
        "• **`/adventure`** — karma choices, combat segments, rare events (Wandering Elder, Inheritance Fragment)\n"
        f"• **`/cultivate`** — ~{CULTIVATE_FRAGMENT_CHANCE * 100:.0f}% fragment · ~{CULTIVATE_MANUAL_CHANCE * 100:.1f}% manual · "
        "dao events including **Heavenly Glimpse**\n"
        f"• **`/breakthrough`** — ~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}% manual on success "
        f"(pool varies by karma tier); demonic failures can still yield fragments (~{BREAKTHROUGH_FAIL_FRAGMENT_CHANCE * 100:.0f}%)\n"
        "• **`/dungeon`** — Blackwind Cavern bonus rolls + one weekly boss manual\n"
        "• **`/shop buy`** — technique pamphlets (Swift Slash, Ember Palm) & Unidentified Scroll gamble\n"
    )


def build_karma_guide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="☯️ Karma & Manual Pools",
        description=(
            "Karma comes from **`/adventure`** moral choices — not chosen at `/start`.\n"
            f"{_subtext('Your dao path shapes which manuals you are most likely to roll.')}"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name=f"☀️ Righteous (+{KARMA_RIGHTEOUS_THRESHOLD}+)",
        value=(
            "**Adventure**\n"
            f"{_quote('Purifying Breath · Mountain Guard · Iron Will · Lotus Revival…')}\n"
            "**Breakthrough** · Righteous pool"
        ),
        inline=True,
    )
    embed.add_field(
        name="⚖️ Neutral",
        value=(
            "**Adventure**\n"
            f"{_quote('Swift Slash · Mist Step · Iron Cleave · Rising Tempo…')}\n"
            "**Breakthrough** · Earth or enlightenment pool"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"😈 Demonic ({KARMA_DEMONIC_THRESHOLD}−)",
        value=(
            "**Adventure**\n"
            f"{_quote('Sanguine Drain · Hemorrhage Art · Undying Vow · Blood Feast…')}\n"
            "**Breakthrough** · Demonic pool"
        ),
        inline=True,
    )
    embed.add_field(
        name="Drop weight bias",
        value=_quote(
            "Matching karma + technique dao = much higher roll weight.\n"
            "Opposing karma + dao = reduced weight.\n"
            "Neutral techniques get a small bonus for all paths."
        ),
        inline=False,
    )
    return embed


def _build_category_embed(
    category: str,
    entries: list[ManualEntry],
    *,
    part: int,
    parts: int,
    total: int,
) -> discord.Embed:
    emoji = CATEGORY_EMOJI.get(category, "📖")
    title = CATEGORY_TITLES.get(category, category.title())
    suffix = f" ({part}/{parts})" if parts > 1 else ""
    color = discord.Color(CATEGORY_COLORS.get(category, 0x2ECC71))
    embed = discord.Embed(
        title=f"{emoji} {title} Manuals{suffix}",
        description=(
            f"**{total} scroll{'s' if total != 1 else ''}** on the {title.lower()} path.\n"
            f"{_subtext('Each card: Tags · Description · Art type · In combat · Obtain')}"
        ),
        color=color,
    )
    embed.set_author(name=f"{emoji} {title} Path · Scripture Pavilion")
    for entry in entries:
        embed.add_field(
            name=_technique_field_name(entry),
            value=_technique_card(entry),
            inline=False,
        )
    embed.set_footer(text="Dao: ☀️ righteous · ⚖️ neutral · 😈 demonic")
    return embed


def build_category_embeds(category: str, entries: list[ManualEntry]) -> list[discord.Embed]:
    """Split large categories so each embed stays under Discord's size cap."""
    chunk_size = 5
    total = len(entries)
    if total <= chunk_size:
        return [_build_category_embed(category, entries, part=1, parts=1, total=total)]
    embeds: list[discord.Embed] = []
    parts = (total + chunk_size - 1) // chunk_size
    for idx in range(0, total, chunk_size):
        chunk = entries[idx : idx + chunk_size]
        embeds.append(
            _build_category_embed(category, chunk, part=idx // chunk_size + 1, parts=parts, total=total)
        )
    return embeds


def build_library_pages_text() -> list[tuple[str, str]]:
    pages: list[tuple[str, str]] = [("Introduction", build_library_intro_markdown())]
    pages.append(("Master catalog", build_master_catalog_text()))
    entries = build_manual_catalog()
    by_category = _group_entries_by_category(entries)

    for category in CATEGORY_ORDER:
        cat_entries = by_category.get(category, [])
        if not cat_entries:
            continue
        title = CATEGORY_TITLES.get(category, category.title())
        listing = build_category_list(cat_entries)
        details = "\n\n".join(format_manual_entry(e) for e in cat_entries)
        pages.append((title, f"{listing}\n\n{details}"))
    return pages


def build_library_embeds() -> list[discord.Embed]:
    embeds: list[discord.Embed] = []
    frag = MANUAL_CRAFT_INPUTS.get("technique_fragment", 3)

    intro = discord.Embed(
        title="📚 Scripture Pavilion",
        description=(
            "**Basic Strike** is free. All other arts need a **manual** scroll.\n\n"
            f"{_quote('`/learn` study · `/equip-technique` slot · `/techniques` review your build')}\n"
            f"{_subtext('Hunt & adventure fights use button combat — techniques, Pass Turn, Flee.')}"
        ),
        color=discord.Color.dark_purple(),
    )
    intro.set_author(name="How to read this library")
    intro.add_field(
        name="🏷️ Tags",
        value=f"{_chip('Tier')} · {_chip('Slot')} · dao emoji · {_chip('Role')}",
        inline=True,
    )
    intro.add_field(
        name="📖 Description",
        value="Block-quoted effect text",
        inline=True,
    )
    intro.add_field(
        name="⚔️ In combat",
        value="What the art does when equipped",
        inline=True,
    )
    intro.add_field(
        name="🗺️ Obtain",
        value="Numbered drop sources",
        inline=True,
    )
    intro.add_field(
        name=f"📜 Bind (`/craft manual`)",
        value=(
            f"**{frag}× Technique Fragment** + **1× Blank Scroll** + **1× Spirit Ink**\n"
            f"{_subtext('Mortal realm → Mortal pool · Earth+ → Earth pool')}"
        ),
        inline=False,
    )
    intro.add_field(
        name="♻️ Duplicates",
        value=_quote("Already learned? The scroll crumbles into **2× Technique Fragment**."),
        inline=False,
    )
    embeds.append(intro)

    embeds.extend(build_master_catalog_embeds())

    embeds.append(build_sources_overview_embed())
    embeds.append(build_karma_guide_embed())

    entries = build_manual_catalog()
    by_category = _group_entries_by_category(entries)

    for category in CATEGORY_ORDER:
        cat_entries = by_category.get(category, [])
        if not cat_entries:
            continue
        embeds.extend(build_category_embeds(category, cat_entries))

    embeds.append(build_elite_hunt_embed())
    return embeds
