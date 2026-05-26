from __future__ import annotations

import discord

from .guidance import get_help_sections, get_start_next_steps, get_welcome_intro
from .roots_info import build_roots_tutorial_pages

DISCORD_FIELD_CHAR_LIMIT = 1024
DISCORD_DESC_CHAR_LIMIT = 4096
FIELD_CHUNK_BUDGET = 980


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


def _embed(title: str, description: str, color: discord.Color, fields: list[tuple[str, str]] | None = None) -> discord.Embed:
    desc = description[:DISCORD_DESC_CHAR_LIMIT]
    embed = discord.Embed(title=title, description=desc, color=color)
    for name, value in fields or []:
        for index, chunk in enumerate(_chunk_text(value)):
            field_name = name if index == 0 else f"{name} (cont.)"
            embed.add_field(name=field_name, value=chunk[:DISCORD_FIELD_CHAR_LIMIT], inline=False)
    return embed


def build_tutorial_pages() -> list[discord.Embed]:
    """Return ordered embed pages for the full server tutorial."""
    pages: list[discord.Embed] = []

    pages.append(
        _embed(
            "🌿 The Path of Cultivation",
            get_welcome_intro()
            + "\n\n"
            "**Pace:** ~15 minutes a day · **Theme:** serious xianxia · **Progress:** saved per server\n\n"
            "Use the slash commands below (type `/` in chat). This channel is your reference — "
            "bookmark it and return whenever you forget a mechanic.",
            discord.Color.dark_green(),
        )
    )

    pages.append(
        _embed(
            "1 · Beginning Your Dao",
            "Every cultivator starts the same way:",
            discord.Color.green(),
            [
                (
                    "Create your character",
                    "`/start` — choose your **dao name**, **origin**, and **moral path**.\n"
                    "Your origin and randomly rolled **spirit root** passively change stats forever.\n"
                    "See **`/roots`** for the tier list (early vs late game) and every stat bonus.\n"
                    "Don't like your root? `/reroll_root` — **one free reroll**, then 50 spirit stones + 7-day wait.",
                ),
                ("First session checklist", get_start_next_steps()),
            ],
        )
    )

    pages.extend(build_roots_tutorial_pages())

    pages.append(
        _embed(
            "4 · The Daily Core Loop",
            "This is what most days look like:",
            discord.Color.gold(),
            [
                (
                    "Step by step",
                    "1. **`/daily`** — stipend of qi and spirit stones (once per UTC day).\n"
                    "2. **`/cultivate`** — gather qi (**15 min** cooldown). Uses stamina; regens over time.\n"
                    "3. **`/profile`** — check realm, qi cap, and hit **Cultivate** from buttons.\n"
                    "4. **`/breakthrough`** — when qi is **full**, attempt to advance substage/realm.\n"
                    "5. **`/cooldown`** — see what's ready right now.",
                ),
                (
                    "Breakthrough tips",
                    "**Moral path** shifts breakthrough risk (righteous = safer, demonic = riskier rewards).\n"
                    "**Clarity pills** boost stability for one attempt.\n"
                    "Failed breakthroughs cost qi — cultivate back up and try again.",
                ),
            ],
        )
    )

    pages.append(
        _embed(
            "5 · Adventures & Materials",
            "Adventures are **interactive** — you make choices that affect loot and failure.",
            discord.Color.dark_green(),
            [
                (
                    "How adventures work",
                    "`/adventure` — pick an **area** and **stance** (cautious / balanced / reckless).\n"
                    "Each run has **2 segments**. Every segment shows a scenario with **button choices**.\n"
                    "• Safer choices succeed more often · risky choices can **fail the whole run** or spike loot\n"
                    "`/adventure-continue` — resume if buttons timed out · `/adventure-abandon` — quit without rewards\n"
                    "**Cooldown:** 20 minutes between completed runs.",
                ),
                (
                    "Stances",
                    "**Cautious** — +success, −15% loot\n"
                    "**Balanced** — standard\n"
                    "**Reckless** — −success, +25% loot, slightly more rare events",
                ),
                (
                    "Areas",
                    "`/areas` — compare zones, materials, realm gates, and rare events.\n"
                    "**Whispering Bamboo Grove** — starter herbs & cores\n"
                    "**Ashen Cliff** — bandit tokens, ember moss, iron shards\n"
                    "**Moonwell Ruins** — lotus, ancient dust, late-game alchemy",
                ),
            ],
        )
    )

    pages.append(
        _embed(
            "6 · Alchemy & Recipes",
            "Turn adventure materials into power.",
            discord.Color.teal(),
            [
                (
                    "Crafting commands",
                    "`/recipes` — all pill & key recipes with success rates and effects\n"
                    "`/craft pill` — brew pills (may leave **pill ash** on failure)\n"
                    "`/craft key` — forge **Blackwind Key** for dungeons\n"
                    "`/inventory` — see what you carry\n"
                    "`/use` — consume a pill by item id (e.g. `qi_gathering_pill`)",
                ),
                (
                    "Key pills",
                    "**Qi Gathering** — +30% cultivate qi (3 sessions)\n"
                    "**Tempering** — +defense for one adventure/dungeon\n"
                    "**Swiftwind** — +adventure success for one run\n"
                    "**Blood Ember** — +dungeon damage for one run\n"
                    "**Clarity** — +breakthrough stability for one attempt\n"
                    "**Moonwell Tonic** — +rare event chance for one adventure",
                ),
                (
                    "Cooldown pills (fun)",
                    "**Flow Meridian Pill** — shaves **10 min** off your next adventure wait\n"
                    "**Meridian Surge Pill** — shaves **7 min** off your next **2** cultivations\n"
                    "**Gatebreaker Dust** — shaves **30 min** off your next dungeon wait\n"
                    "Use before a session when several timers are almost ready. Check `/cooldown` for active haste.",
                ),
            ],
        )
    )

    pages.append(
        _embed(
            "7 · Forging & Equipment",
            "Gear is crafted, not dropped — then enhanced with affix stones.",
            discord.Color.dark_gold(),
            [
                (
                    "Forge & equip",
                    "`/forge` — craft a piece for **weapon**, **armor**, **accessory**, or **talisman** slot.\n"
                    "Costs adventure materials · rolls random **stats**:\n"
                    "• **Power** — adventure success & PvP\n"
                    "• **Defense** — adventure survivability\n"
                    "• **Fortune** — better material drops\n"
                    "• **Insight** — more rare encounters",
                ),
                (
                    "Affixes",
                    "`/equip` — spend an **Affix Stone** on **forged** gear (not empty slots).\n"
                    "Affix stones drop from rare adventure events and dungeons.\n"
                    "`/loadout` — see gear + affixes · `/stats` — total stat bonuses",
                ),
            ],
        )
    )

    pages.append(
        _embed(
            "8 · Dungeons",
            "Key-gated group content for better rewards (solo for now).",
            discord.Color.dark_red(),
            [
                (
                    "Blackwind Cavern",
                    "`/dungeon` — enter **Blackwind Cavern** (consumes **Blackwind Key** on entry).\n"
                    "Craft keys with `/craft key` · requires **Foundation Establishment** realm or higher.\n"
                    "**Cooldown:** 2 hours between runs.\n"
                    "Bring **Blood Ember pills** for extra damage. Affix stones can drop on success.",
                ),
            ],
        )
    )

    pages.append(
        _embed(
            "9 · PvP, Sects & Leaderboard",
            "Test your dao against others in the server.",
            discord.Color.blue(),
            [
                (
                    "Social commands",
                    "`/duel @player` — challenge them; they **Accept/Decline** before the spar (stones only, 2 hr cooldown)\n"
                    "`/leaderboard` — top cultivators by progression\n"
                    "`/sect-create` · `/sect-join` · `/sect-leave` · `/sect` — minimal sect system\n"
                    "Cultivating while in a sect contributes qi to the sect total.",
                ),
            ],
        )
    )

    pages.append(
        _embed(
            "10 · Cooldowns at a Glance",
            "All timers use **UTC**.",
            discord.Color.dark_teal(),
            [
                (
                    "Timed commands",
                    "**/cultivate** — every **15 min**\n"
                    "**/adventure** — every **20 min** (after a completed run)\n"
                    "**/dungeon** — every **2 hours**\n"
                    "**/duel** — every **2 hours**\n"
                    "**/daily** — once per UTC calendar day",
                ),
                (
                    "No cooldown",
                    "/profile · /inventory · /loadout · /stats · /recipes · /areas · "
                    "/breakthrough · /craft pill · /craft key · /forge · /use · /equip · "
                    "/help · /cooldown · /leaderboard · /sect · /adventure-continue · /adventure-abandon",
                ),
            ],
        )
    )

    help_body = "\n\n".join(f"**{title}**\n{body}" for title, body in get_help_sections())
    pages.append(
        _embed(
            "11 · Full Command Reference",
            "Quick index of every slash command:",
            discord.Color.blurple(),
            [("Commands", help_body)],
        )
    )

    pages.append(
        _embed(
            "12 · Walk the Path",
            "You now have everything you need.\n\n"
            "1. `/start` if you haven't yet\n"
            "2. `/daily` then `/cultivate`\n"
            "3. `/adventure` in **Whispering Bamboo Grove** when ready\n"
            "4. `/recipes` → `/craft pill` when you have materials\n"
            "5. `/forge` and `/equip` when you earn affix stones\n\n"
            "Questions? Ask in chat — or use `/help` anytime.\n"
            "*May your meridians stay clear and your breakthroughs succeed.*",
            discord.Color.dark_purple(),
        )
    )

    return pages


def validate_tutorial_pages(pages: list[discord.Embed] | None = None) -> None:
    """Raise ValueError if any embed violates Discord limits."""
    pages = pages or build_tutorial_pages()
    for index, embed in enumerate(pages):
        if embed.description and len(embed.description) > DISCORD_DESC_CHAR_LIMIT:
            raise ValueError(f"Page {index + 1} description exceeds {DISCORD_DESC_CHAR_LIMIT} chars")
        for field in embed.fields:
            if len(field.value) > DISCORD_FIELD_CHAR_LIMIT:
                raise ValueError(
                    f"Page {index + 1} field {field.name!r} exceeds {DISCORD_FIELD_CHAR_LIMIT} chars"
                )
