from __future__ import annotations

from dataclasses import dataclass

import discord

from .content import get_spirit_root_modifiers, load_all_content
from .discord_format import quote

DISCORD_FIELD_CHAR_LIMIT = 1024
FIELD_CHUNK_BUDGET = 980

# Human-readable stat labels and formatting hints.
STAT_DISPLAY: dict[str, tuple[str, str]] = {
    "cultivate_qi_mult": ("Cultivate qi", "mult"),
    "breakthrough_stability": ("Breakthrough stability", "add_pct"),
    "breakthrough_setback_mult": ("Breakthrough setback on fail", "mult"),
    "adventure_success": ("Adventure success", "add_pct"),
    "adventure_defense": ("Adventure defense", "add_pct"),
    "dungeon_damage": ("Dungeon damage", "add_pct"),
    "dungeon_defense": ("Dungeon defense", "add_pct"),
    "drop_luck": ("Drop luck", "add_pct"),
    "rare_event_mult": ("Rare event chance", "mult"),
    "pvp_power": ("PvP power", "add_pct"),
    "pvp_stones_mult": ("Duel stone winnings", "mult"),
    "stamina_efficiency": ("Stamina efficiency", "mult"),
    "clan_contribution_mult": ("Clan qi contribution", "mult"),
}


@dataclass(frozen=True)
class RootTierEntry:
    early_tier: str
    late_tier: str
    early_summary: str
    late_summary: str
    best_for: str


ROOT_TIERS: dict[str, RootTierEntry] = {
    "Pure Jade Root": RootTierEntry(
        early_tier="S",
        late_tier="A",
        early_summary="Best all-rounder for climbing realms safely.",
        late_summary="Still excellent; outscaled slightly by combat specialists.",
        best_for="New cultivators · steady breakthroughs · daily qi grind",
    ),
    "Violet Lightning Root": RootTierEntry(
        early_tier="A",
        late_tier="B",
        early_summary="Strong adventures and early dungeon damage.",
        late_summary="Stamina tax (×0.92) slows long-term cultivate sessions.",
        best_for="Aggressive explorers · early PvE burst",
    ),
    "Flame Ember Root": RootTierEntry(
        early_tier="B",
        late_tier="S",
        early_summary="Risky breakthroughs before you can afford failures.",
        late_summary="Top-tier PvP and dungeon damage at high realms.",
        best_for="Duelists · dungeon pushers · demonic paths",
    ),
    "Frost Spirit Root": RootTierEntry(
        early_tier="A",
        late_tier="B",
        early_summary="Forgiving adventures and safer breakthroughs.",
        late_summary="−5% drop luck hurts material farming efficiency.",
        best_for="Cautious adventurers · players who hate failing runs",
    ),
    "Earthweight Root": RootTierEntry(
        early_tier="C",
        late_tier="A",
        early_summary="Slow qi gain (×0.95) feels rough while realms are cheap.",
        late_summary="Tankiest root for dungeons and long adventures.",
        best_for="Late-game dungeons · defensive stances · patience",
    ),
    "Moonlit Sword Root": RootTierEntry(
        early_tier="B",
        late_tier="S",
        early_summary="Modest bonuses before Moonwell/Ruins content.",
        late_summary="×1.10 rare events + dungeon dmg — king of Moonwell & affix farming.",
        best_for="Moonwell Ruins · rare events · affix stone hunting",
    ),
    "Mercy Lotus Root": RootTierEntry(
        early_tier="C",
        late_tier="A",
        early_summary="Weakest solo root; lower duel stone winnings.",
        late_summary="×1.15 clan contribution shines in active clans.",
        best_for="Clan players · support dao · group-minded cultivators",
    ),
}

TIER_LEGEND = (
    "**S** — top pick for that phase · **A** — strong · **B** — situational · **C** — weak until geared"
)


def _format_stat_value(key: str, val: float) -> str:
    _label, kind = STAT_DISPLAY.get(key, (key.replace("_", " ").title(), "add"))
    if kind == "mult":
        if val >= 1.0:
            delta = val - 1.0
            if abs(delta) < 0.001:
                return f"×{val:.2f}"
            return f"×{val:.2f} (+{delta * 100:.0f}%)"
        return f"×{val:.2f} ({(val - 1.0) * 100:.0f}%)"
    if kind == "add_pct":
        sign = "+" if val >= 0 else ""
        return f"{sign}{val * 100:.0f}%"
    return str(val)


def format_root_stat_lines(root_name: str) -> list[str]:
    load_all_content()
    mod = get_spirit_root_modifiers(root_name)
    if mod is None:
        return ["No stats configured."]
    lines: list[str] = []
    for key, val in mod.values.items():
        label = STAT_DISPLAY.get(key, (key.replace("_", " ").title(), ""))[0]
        lines.append(f"• **{label}:** {_format_stat_value(key, val)}")
    if mod.description:
        lines.append(f"_{mod.description}_")
    return lines


def _chunk_text(text: str, max_len: int = FIELD_CHUNK_BUDGET) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.split("\n\n"):
        extra = len(paragraph) if not current else 2 + len(paragraph)
        if current and current_len + extra > max_len:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _tier_emoji(tier: str) -> str:
    return {"S": "🟣", "A": "🔵", "B": "🟢", "C": "🟡"}.get(tier, "⚪")


def _format_tier_list() -> str:
    tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
    ordered = sorted(
        ROOT_TIERS.items(),
        key=lambda item: (tier_order.get(item[1].early_tier, 9), item[0]),
    )
    lines = [
        "**Root** · Early → Late · Best for",
    ]
    for root_name, tier in ordered:
        lines.append(
            f"{_tier_emoji(tier.early_tier)}{tier.early_tier} → "
            f"{_tier_emoji(tier.late_tier)}{tier.late_tier} · **{root_name}**\n"
            f"↳ {tier.best_for}"
        )
    return "\n".join(lines)


def _format_early_late_breakdown() -> str:
    blocks: list[str] = []
    for root_name, tier in ROOT_TIERS.items():
        blocks.append(
            f"**{root_name}** — Early **{tier.early_tier}** · Late **{tier.late_tier}**\n"
            f"Early: {tier.early_summary}\n"
            f"Late: {tier.late_summary}"
        )
    return "\n\n".join(blocks)


def _format_all_root_details() -> str:
    blocks: list[str] = []
    for root_name in sorted(ROOT_TIERS.keys()):
        stat_lines = format_root_stat_lines(root_name)
        tier = ROOT_TIERS[root_name]
        blocks.append(
            f"**{root_name}** (Early {tier.early_tier} / Late {tier.late_tier})\n"
            + "\n".join(stat_lines)
        )
    return "\n\n".join(blocks)


def _add_chunked_field(embed: discord.Embed, name: str, text: str) -> None:
    for index, chunk in enumerate(_chunk_text(text)):
        field_name = name if index == 0 else f"{name} (cont.)"
        embed.add_field(name=field_name, value=chunk[:DISCORD_FIELD_CHAR_LIMIT], inline=False)


def build_roots_embed(root_name: str | None = None) -> discord.Embed:
    load_all_content()

    if root_name is not None:
        mod = get_spirit_root_modifiers(root_name)
        if mod is None:
            return discord.Embed(
                title="Unknown Spirit Root",
                description="That root is not in the heavens' records.",
                color=discord.Color.red(),
            )
        tier = ROOT_TIERS.get(root_name)
        tier_text = ""
        if tier:
            tier_text = (
                f"**Early game:** {tier.early_tier} — {tier.early_summary}\n"
                f"**Late game:** {tier.late_tier} — {tier.late_summary}\n"
                f"**Best for:** {tier.best_for}\n\n"
            )
        embed = discord.Embed(
            title=f"Spirit Root — {root_name}",
            description=tier_text + "**Passive stat bonuses:**",
            color=discord.Color.purple(),
        )
        stats = "\n".join(format_root_stat_lines(root_name))
        _add_chunked_field(embed, "Modifiers", stats)
        embed.set_footer(text="Reroll with /reroll_root · Compare all with /roots")
        return embed

    embed = discord.Embed(
        title="Spirit Root Tier List",
        description=(
            "Your **spirit root** is rolled at `/start` and passively modifies stats forever. "
            "Origins stack on top — roots define your long-term specialty.\n\n"
            + TIER_LEGEND
        ),
        color=discord.Color.purple(),
    )
    _add_chunked_field(embed, "Rankings (Early → Late)", _format_tier_list())
    _add_chunked_field(embed, "When each root shines", _format_early_late_breakdown())
    embed.set_footer(text="/roots root:<name> for one root · /reroll_root to change yours")
    return embed


def build_roots_tutorial_pages() -> list[discord.Embed]:
    """Two tutorial embeds for spirit roots."""
    pages: list[discord.Embed] = []

    tier_list = build_roots_embed(root_name=None)
    tier_list.title = "2 · Spirit Roots — Tier List"
    tier_list.set_author(name="Chapter 2 · Spirit Roots")
    pages.append(tier_list)

    detail = discord.Embed(
        title="3 · Spirit Roots — Stat Reference",
        description=(
            "How roots affect gameplay:\n"
            "• **Additive %** — added to success/defense/luck bonuses\n"
            "• **Multipliers** — multiply gains or costs for that system\n"
            "• Roots stack with **origin**, **gear**, **affixes**, and **pills**\n\n"
            f"{quote('Use `/roots` anytime for this chart · `/reroll_root` — 1 free, then 50 stones + 7-day wait.')}"
        ),
        color=discord.Color.purple(),
    )
    detail.set_author(name="Chapter 3 · Spirit Roots")
    _add_chunked_field(detail, "Every root", _format_all_root_details())
    pages.append(detail)

    return pages
