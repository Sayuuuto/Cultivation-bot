from __future__ import annotations

import json
from pathlib import Path

import discord

from .discord_format import chip, quote, subtext
from .guidance import get_help_sections, get_start_next_steps, get_welcome_intro
from .karma import KARMA_DEMONIC_THRESHOLD, KARMA_RIGHTEOUS_THRESHOLD
from .manuals import (
    BREAKTHROUGH_MANUAL_CHANCE,
    CULTIVATE_FRAGMENT_CHANCE,
    CULTIVATE_MANUAL_CHANCE,
    MANUAL_CRAFT_INPUTS,
)
from .novice_trial import TRIAL_STEPS
from .roots_info import build_roots_tutorial_pages

CONFIG_ROOT = Path(__file__).resolve().parent.parent / "config"
DISCORD_FIELD_CHAR_LIMIT = 1024
DISCORD_DESC_CHAR_LIMIT = 4096
FIELD_CHUNK_BUDGET = 980

LANE_COLORS = {
    "cultivation": discord.Color.from_rgb(46, 204, 113),
    "resource": discord.Color.from_rgb(52, 152, 219),
    "story": discord.Color.from_rgb(155, 89, 182),
}
OFFLINE_QI_CAP_MINUTES = 120
FRAGMENTS_FOR_MANUAL = MANUAL_CRAFT_INPUTS.get("technique_fragment", 3)


def _cultivate_dao_event_chance() -> float:
    with (CONFIG_ROOT / "cultivate_events.json").open(encoding="utf-8") as f:
        return float(json.load(f).get("base_rare_chance", 0.12))


def _format_trial_steps() -> str:
    return "\n".join(f"{idx}. {label}" for idx, (_key, label) in enumerate(TRIAL_STEPS, start=1))


def _no_cooldown_commands() -> str:
    return (
        "/profile · /techniques · /learn · /equip-technique · /inventory · /item · "
        "/loadout · /stats · /recipes · /roots · /breakthrough · /craft · /forge · "
        "/shop · /use · /equip · /help · /cooldown · /remind · /areas · "
        "/adventure-continue · /adventure-abandon · /reset"
    )


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


def _embed(
    title: str,
    description: str,
    color: discord.Color,
    fields: list[tuple[str, str]] | None = None,
    *,
    author: str | None = None,
    footer: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description[:DISCORD_DESC_CHAR_LIMIT], color=color)
    if author:
        embed.set_author(name=author)
    for name, value in fields or []:
        for index, chunk in enumerate(_chunk_text(value)):
            field_name = name if index == 0 else f"{name} (cont.)"
            embed.add_field(name=field_name, value=chunk[:DISCORD_FIELD_CHAR_LIMIT], inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def build_tutorial_intro_markdown() -> str:
    return (
        "# 📜 Cultivation Bot — Complete Tutorial\n\n"
        f"{subtext('Scroll down in order · repost anytime with `/post-tutorial`')}\n\n"
        "**What this channel is:** your server’s step-by-step guide to every major system.\n\n"
        "> 🧘 **Cultivation** — qi, breakthroughs, spirit roots\n"
        "> ⚔️ **Resources** — gather, hunt, techniques, crafting\n"
        "> 🌿 **Story** — adventures, karma, dungeons, clans\n\n"
        "**Technique manuals** live in the **Scripture Pavilion** library channel — use **`/post-library`** "
        "there for the full manual catalog.\n\n"
        "-# New here? Start with **Chapter 1** below, then play **`/start`** in any channel."
    )


def build_tutorial_pages() -> list[discord.Embed]:
    """Return ordered embed pages for the full server tutorial."""
    pages: list[discord.Embed] = []

    pages.append(
        _embed(
            "🌿 Welcome, Cultivator",
            (
                f"{get_welcome_intro()}\n\n"
                f"{quote('Type `/` in chat for slash commands. Timers use UTC.')}\n"
                f"{subtext('~15 minutes a day · serious xianxia · progress saved per server')}\n\n"
                f"Most pick commands use **autocomplete** — lists only show what you can use right now."
            ),
            discord.Color.dark_green(),
            author="Cultivation Bot Tutorial",
        )
    )

    three_paths = discord.Embed(
        title="📍 The Three Paths",
        description=(
            "Most sessions rotate between these lanes while timers recover:\n"
            f"{subtext('Use `/cooldown` anytime to see what is ready')}"
        ),
        color=LANE_COLORS["cultivation"],
    )
    three_paths.set_author(name="Tutorial · Activity Lanes")
    three_paths.add_field(
        name="🧘 Cultivation",
        value=(
            f"{chip('15 min')}\n"
            "**`/cultivate`** · **`/breakthrough`**\n"
            "Grow qi, advance realms, roll dao events."
        ),
        inline=True,
    )
    three_paths.add_field(
        name="⚔️ Resources",
        value=(
            f"{chip('5 min')}\n"
            "**`/gather`** · **`/hunt`**\n"
            "Materials, cores, technique manuals."
        ),
        inline=True,
    )
    three_paths.add_field(
        name="🌿 Story",
        value=(
            f"{chip('20 min')} · {chip('2 hr')}\n"
            "**`/adventure`** · **`/dungeon`**\n"
            "Choices, combat, karma, elite loot."
        ),
        inline=True,
    )
    pages.append(three_paths)

    pages.append(
        _embed(
            "1 · Beginning Your Dao",
            quote("Every cultivator starts the same — your choices shape the dao you become."),
            discord.Color.green(),
            [
                (
                    "Create your character",
                    "**`/start`** — dao name + **origin** (starting gifts, often includes a technique manual).\n\n"
                    "**Karma** begins neutral and shifts through **`/adventure`** moral choices "
                    f"(Righteous **+{KARMA_RIGHTEOUS_THRESHOLD}+** · Demonic **{KARMA_DEMONIC_THRESHOLD}−**).\n\n"
                    "Your **origin** and rolled **spirit root** passively modify stats forever.\n"
                    "**`/reroll_root`** — **1 free reroll**, then 50 spirit stones + 7-day wait.\n"
                    "**`/roots`** — tier list and every stat bonus.",
                ),
                (
                    "Outer Disciple Trial (6 steps)",
                    f"Tracked on **`/profile`** after your first **`/daily`**:\n{_format_trial_steps()}\n\n"
                    f"{quote('First `/adventure` features the Sage of the Bamboo Path — your first karma choice.')}",
                ),
                ("First session checklist", get_start_next_steps()),
            ],
            author="Chapter 1 · Getting Started",
        )
    )

    pages.extend(build_roots_tutorial_pages())

    pages.append(
        _embed(
            "4 · Your Profile Dashboard",
            quote("`/profile` is your home screen — timers, build, and quick actions."),
            discord.Color.blue(),
            [
                (
                    "What you see",
                    "**Realm & qi bar** — daily streak, breakthrough-ready hint\n"
                    "**Outer Disciple Trial** — current onboarding step (until complete)\n"
                    "**Activity lanes** — live timers for cultivate / gather / hunt / adventure / dungeon\n"
                    "**Martial dao** — loadout, learned arts, unread manuals, craft progress\n"
                    "**Combat stats** — HP, strength, agility, defense (for button fights)\n"
                    "**Resources** — spirit stones\n"
                    "**Qi gathering** — passive **Qi/min** while away + **`/cultivate`** preview (pills boost active only)",
                ),
                (
                    "Profile buttons",
                    "**Cultivate** — same as **`/cultivate`** (collects banked passive qi + active roll)\n"
                    "**Breakthrough** — same as **`/breakthrough`** when qi is full\n\n"
                    f"{subtext(f'Passive qi accrues at a fixed rate while away (up to {OFFLINE_QI_CAP_MINUTES} min banked)')}",
                ),
            ],
            author="Chapter 4 · Profile",
        )
    )

    dao_chance = _cultivate_dao_event_chance()
    pages.append(
        _embed(
            "5 · Daily Cultivation Loop",
            "This is what most days look like:",
            discord.Color.gold(),
            [
                (
                    "Core rhythm",
                    "1. **`/daily`** — stipend of qi and spirit stones (24h cooldown; haste pills apply)\n"
                    "2. **`/cultivate`** — active qi roll (**15 min** cooldown; Qi Gathering pills boost this)\n"
                    "3. **`/profile`** — realm, qi cap, timers, **Cultivate** button\n"
                    "4. **`/breakthrough`** — when qi is **full**, attempt to advance\n"
                    "5. **`/cooldown`** — see what is ready right now",
                ),
                (
                    "Dao events & breakthrough",
                    f"{quote(f'~{dao_chance * 100:.0f}% dao event chance per cultivate — Spirit Surge, Heavenly Glimpse, Scripture Whisper, and more.')}\n"
                    "**Passive qi** — **`/profile`** shows your **Qi/min** while inactive; banked on any action.\n"
                    f"**`/cultivate` rolls** — ~{CULTIVATE_FRAGMENT_CHANCE * 100:.0f}% technique fragment · ~{CULTIVATE_MANUAL_CHANCE * 100:.1f}% manual.\n\n"
                    "**Daily streak** on `/profile` boosts stipend stones.\n"
                    "**Clarity pills** boost breakthrough stability for one attempt.\n"
                    f"**`/breakthrough`** — ~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}% manual on success; karma picks the pool.",
                ),
            ],
            author="Chapter 5 · Cultivation",
        )
    )

    pages.append(
        _embed(
            "6 · Gather & Hunt",
            "Farm the martial world in **5-minute** bursts while cultivate cools down.",
            LANE_COLORS["resource"],
            [
                (
                    "Resource commands",
                    "**`/gather`** — herbs, scroll ink, inscription stone (autocomplete areas)\n"
                    "**`/hunt`** — spirit beasts with **button combat**\n"
                    f"{subtext('5 min hunt cooldown starts when the fight ends (Finish or Flee), not when you engage')}",
                ),
                (
                    "Hunt combat",
                    quote(
                        "Engage → technique buttons (cooldowns on buttons) → **Flee** or **Finish**.\n"
                        "HP bars, status badges, emoji combat logs.\n"
                        "Prepare with `/techniques` — 4 active slots + 1 passive."
                    ),
                ),
                (
                    "What you are farming",
                    "• **Beast cores** → Tempering pills\n"
                    f"• **Technique fragments** → `/craft manual` ({FRAGMENTS_FOR_MANUAL}× + scroll + ink)\n"
                    "• **Manual drops** from elite beasts (Mist Fang Wolf, Fire Mantis, Ruin Devourer)\n"
                    f"{subtext('See the Scripture Pavilion library channel for the full manual list')}",
                ),
            ],
            author="Chapter 6 · Resources",
        )
    )

    pages.append(
        _embed(
            "7 · Martial Techniques",
            "Manuals teach combat arts. Your build is techniques + passives — not karma stats.",
            discord.Color.purple(),
            [
                (
                    "Study & equip",
                    "**`/techniques`** — loadout, art types, study/equip **menus**\n"
                    "**`/technique`** — read what an art does before you study it\n"
                    "**`/learn`** — consume a manual from your bag (autocomplete)\n"
                    "**`/equip-technique`** — **active slots 1–4** or **passive slot** (autocomplete labels the type)\n"
                    f"{subtext('Everyone starts with Basic Strike — manuals expand your arsenal')}",
                ),
                (
                    "Where manuals come from",
                    "1. **`/hunt`** elites — targeted drops\n"
                    "2. **`/adventure`** — karma choices, combat wins, rare events\n"
                    f"3. **`/cultivate`** — ~{CULTIVATE_FRAGMENT_CHANCE * 100:.0f}% fragment · ~{CULTIVATE_MANUAL_CHANCE * 100:.1f}% manual · dao events\n"
                    f"4. **`/breakthrough`** — ~{BREAKTHROUGH_MANUAL_CHANCE * 100:.0f}% manual on success (karma pool)\n"
                    "5. **`/dungeon`** — solo or tag up to 3 allies (Accept); 4-room co-op combat\n"
                    "6. **`/shop`** — pamphlets & Unidentified Scroll gamble\n"
                    f"7. **`/craft manual`** — bind {FRAGMENTS_FOR_MANUAL} fragments + scroll + ink\n\n"
                    f"{quote('Duplicate manuals you already know crumble into 2× Technique Fragment.')}",
                ),
                (
                    "Scripture Pavilion",
                    "Admins run **`/post-library`** in your manual channel for the "
                    "**full catalog** — obtain paths, art types, and karma pools.",
                ),
            ],
            author="Chapter 7 · Techniques",
        )
    )

    pages.append(
        _embed(
            "8 · Adventures",
            "Interactive story runs — choices, combat, karma, and loot.",
            LANE_COLORS["story"],
            [
                (
                    "How a run works",
                    "**`/adventure`** — pick **area** + **stance** (cautious / balanced / reckless).\n"
                    "**2 segments** per run — **choice** scenarios or **combat** segments (button fights).\n"
                    "First run: **Sage of the Bamboo Path** teaches karma on a moral choice.\n"
                    "**`/adventure-continue`** — resume timed-out buttons\n"
                    "**`/adventure-abandon`** — quit without rewards\n"
                    f"{chip('20 min')} cooldown after a **completed** run",
                ),
                (
                    "Stances",
                    "**Cautious** — +success, −15% loot\n"
                    "**Balanced** — standard\n"
                    "**Reckless** — −success, +25% loot, more rare events",
                ),
                (
                    "Karma, loot & rare events",
                    f"Moral choices shift karma (−100…+100) and **manual drop pools**.\n"
                    f"{quote('Wandering Elder · Inheritance Fragment · rare-event pity on dry streaks.')}\n"
                    "**`/areas`** — compare zones, materials, realm gates, and rare events.",
                ),
            ],
            author="Chapter 8 · Adventures",
        )
    )

    pages.append(
        _embed(
            "9 · Crafting, Shop & Items",
            "Turn materials into power. Autocomplete shows only what you can make **now**.",
            discord.Color.teal(),
            [
                (
                    "Commands",
                    "**`/recipes`** — pill, key, and forge recipes (filter: all / pill / key / forge)\n"
                    "**`/craft pill`** — pick any recipe; missing mats show farm spots · **`/craft key`** · **`/craft manual`**\n"
                    "**`/inventory`** — item names grouped by category\n"
                    "**`/item`** — full card: effects, crafting uses, farm locations\n"
                    "**`/shop`** — browse catalog (no args) or **`/shop item:<name>`** to buy\n"
                    "**`/use`** — consume pills from your bag (autocomplete)",
                ),
                (
                    "Key pills",
                    "**Qi Gathering** — +30% cultivate qi (3 sessions)\n"
                    "**Tempering** — +adventure defense for one run\n"
                    "**Swiftwind** — +adventure success\n"
                    "**Blood Ember** — +dungeon damage\n"
                    "**Clarity** — +breakthrough stability\n"
                    "**Moonwell Tonic** — +rare event chance",
                ),
                (
                    "Cooldown pills",
                    quote(
                        "Flow Meridian — −10 min next adventure\n"
                        "Meridian Surge — −7 min next 2 cultivations\n"
                        "Gatebreaker Dust — −30 min next dungeon"
                    ),
                ),
            ],
            author="Chapter 9 · Crafting",
        )
    )

    pages.append(
        _embed(
            "10 · Forging & Gear",
            "Gear is crafted, not dropped — then enhanced with affix stones.",
            discord.Color.dark_gold(),
            [
                (
                    "Forge & equip",
                    "**`/forge`** — weapon, armor, accessory, or talisman\n"
                    "Costs adventure materials · random **stats**:\n"
                    "• **Power** — adventure success & PvP\n"
                    "• **Defense** — adventure survivability\n"
                    "• **Fortune** — better material drops\n"
                    "• **Insight** — more rare encounters",
                ),
                (
                    "Affixes",
                    "**`/equip`** — spend **Affix Stone** on **forged** gear only\n"
                    "Stones drop from rare adventure events and dungeons\n"
                    "**`/loadout`** — gear, affixes, active pill effects\n"
                    "**`/stats`** — Power / Defense / Fortune / Insight from forged gear\n\n"
                    f"{quote('Profile **Combat** stats (HP, STR, AGI) power button fights. `/stats` tracks adventure & PvP modifiers from gear.')}",
                ),
            ],
            author="Chapter 10 · Forging",
        )
    )

    pages.append(
        _embed(
            "11 · Dungeons",
            "Key-gated content for better rewards (solo for now).",
            discord.Color.dark_red(),
            [
                (
                    "Blackwind Cavern",
                    "**`/dungeon`** — realm dungeon alone, or tag up to 3 allies who press **Accept**\n"
                    "**`/craft key`** — Foundation Establishment realm+\n"
                    f"{chip('2 hr')} cooldown · **weekly boss manual** on first clear each week\n"
                    f"{quote('Bring Blood Ember pills for extra damage.')}",
                ),
            ],
            author="Chapter 11 · Dungeons",
        )
    )

    pages.append(
        _embed(
            "12 · Duels, Clans & Sects",
            "Social systems — test your dao against players and join groups.",
            discord.Color.blue(),
            [
                (
                    "Duels (technique combat)",
                    "**`/duel @player`** — public challenge with **Accept / Decline** buttons.\n"
                    "On accept, the bot opens a private arena — same **technique buttons** as `/hunt`: "
                    "equipped actives, passives, status effects, **Yield**, and **Finish**.\n"
                    "Opponent has **2 minutes** to respond. Winner gains **spirit stones**.\n"
                    f"{chip('2 hr')} cooldown for both · `/techniques` and `/equip-technique` set your arena loadout",
                ),
                (
                    "Clans & sects",
                    "**Clans** — `/clan-create` · `/clan-join` · `/clan-leave` · `/clan`\n"
                    "Cultivating in a clan contributes qi to your clan total.\n\n"
                    "**Martial sects** — `/sect-list` · `/sect-join` · `/sect-leave` · `/sect`\n"
                    "Join orders (Wudang, Shaolin, Tang, …) based on **karma** and realm.\n"
                    "Sect **merit** shows on **`/profile`** · use **`/sect-task`** and **`/sect-shop`**.\n\n"
                    "**`/leaderboard`** — top cultivators on this server.",
                ),
            ],
            author="Chapter 12 · Social",
        )
    )

    pages.append(
        _embed(
            "13 · Reminders & Help",
            "Optional tools so you never miss a timer.",
            discord.Color.blurple(),
            [
                (
                    "Cooldown reminders",
                    "**`/remind`** — opt-in **DM pings** when timers are ready\n"
                    "**`/remind status`** — see what's enabled\n"
                    "**`/remind on activity:all`** — enable everything\n"
                    "Covers: cultivate, gather, hunt, adventure, dungeon, duel, daily",
                ),
                (
                    "In-game guidance",
                    "**`/help`** — personal guide with contextual next steps\n"
                    "**`/cooldown`** — live timers + pill haste reductions\n"
                    "**`/reset`** — rewrite your character (requires confirmation)\n\n"
                    f"{quote('Most commands attach a What happens next hint after you use them.')}",
                ),
            ],
            author="Chapter 13 · Tools",
        )
    )

    cooldown = discord.Embed(
        title="14 · Cooldowns at a Glance",
        description=f"All timers use **UTC**. {subtext('Use `/cooldown` in-game for live timers + pill haste')}",
        color=discord.Color.dark_teal(),
    )
    cooldown.set_author(name="Chapter 14 · Timers")
    cooldown.add_field(name="🧘 `/cultivate`", value=f"every {chip('15 min')}", inline=True)
    cooldown.add_field(name="⚔️ `/gather` · `/hunt`", value=f"every {chip('5 min')}", inline=True)
    cooldown.add_field(name="🌿 `/adventure`", value=f"every {chip('20 min')}", inline=True)
    cooldown.add_field(name="🏚️ `/dungeon`", value=f"every {chip('2 hr')}", inline=True)
    cooldown.add_field(name="📜 `/daily`", value=f"every {chip('24 hr')} · haste pills apply", inline=True)
    cooldown.add_field(name="⚔️ `/duel`", value=f"every {chip('2 hr')}", inline=True)
    cooldown.add_field(
        name="No cooldown",
        value=quote(_no_cooldown_commands()),
        inline=False,
    )
    pages.append(cooldown)

    help_sections = get_help_sections()
    mid = (len(help_sections) + 1) // 2
    for part_index, chunk in enumerate((help_sections[:mid], help_sections[mid:]), start=1):
        body = "\n\n".join(f"**{title}**\n{quote(body_text)}" for title, body_text in chunk)
        pages.append(
            _embed(
                f"15 · Command Reference ({part_index}/2)",
                "Quick index of slash commands:",
                discord.Color.blurple(),
                [("Commands", body)],
                author="Chapter 15 · Reference",
            )
        )

    pages.append(
        _embed(
            "16 · Walk the Path",
            (
                "You now have the map. **Play in any channel:**\n\n"
                "1. **`/start`** → **`/daily`** → follow the **Outer Disciple Trial** on **`/profile`**\n"
                "2. **`/cultivate`** (or profile button) · **`/breakthrough`** when qi is full\n"
                "3. **`/techniques`** — study origin manual, equip your loadout\n"
                "4. **`/hunt`** & **`/adventure`** — button combat & karma choices\n"
                "5. **`/recipes`** → craft pills or bind manuals · **`/shop`** for supplies\n"
                "6. **`/forge`** & **`/equip`** when affix stones drop\n"
                "7. **`/remind on activity:all`** if you want DM timer pings\n\n"
                f"{quote('Questions? `/help` and `/cooldown` are always available in-game.')}\n\n"
                "*May your meridians stay clear and your breakthroughs succeed.*"
            ),
            discord.Color.dark_purple(),
            author="Chapter 16 · Begin",
            footer="Tutorial complete · Scripture Pavilion has the manual catalog",
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
