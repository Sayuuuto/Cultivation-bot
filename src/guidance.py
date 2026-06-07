from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .config import Config
from .game import qi_cap
from .models import Player


GUIDANCE_FOOTER = "Use `/help` for all commands · `/cooldown` to see timers"


def format_cooldown_status(remaining_seconds: int) -> str:
    if remaining_seconds <= 0:
        return "Ready now"
    return _format_seconds(remaining_seconds)


def _format_seconds(seconds: int) -> str:
    seconds = max(0, seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def get_welcome_intro() -> str:
    return (
        "The path of cultivation is long, but you need not walk it blind.\n\n"
        "**What this is:** a casual xianxia game paced for ~15 minutes a day. "
        "Gather qi, break through realms, explore for materials, craft pills, "
        "and challenge other daoists.\n\n"
        "**Your first session:** Elder Yunjian guides your awakening — follow his counsel "
        "through **`/daily`**, **`/cultivate`**, and the trials ahead."
    )


def get_start_next_steps() -> str:
    return (
        "1. Follow **Elder Yunjian** — your story begins with **`/start`**.\n"
        "2. **`/daily`** — claim spirit stones and qi when he bids you.\n"
        "3. **`/profile`** — your cultivation trophy and martial record.\n"
        "4. **`/cultivate`** once when ready (or use the profile button).\n"
        "5. **`/hunt`** in Mortal Grove — win your first beast fight.\n"
        "6. **`/techniques`** — unlock your manual, equip arts.\n"
        "7. **`/adventure`** — complete the sage's trial.\n"
        "8. **`/breakthrough`** when qi is full · **`/story`** at Qi Refining · **`/help`** anytime.\n\n"
        "Not happy with your spirit root? **`/reroll_root`** once for free."
    )


def get_abode_welcome_intro(dao_name: str) -> str:
    from .story_mode import get_elder_name

    elder = get_elder_name()
    return (
        f"**{dao_name}**, this is your abode — a private chamber where the world cannot intrude.\n\n"
        f"**{elder}** speaks below. Answer their questions there to continue your awakening."
    )


def get_help_sections() -> list[tuple[str, str]]:
    """Flattened sections for the original all-in-one embed."""
    return [(title, body) for _, title, body in HELP_BY_CATEGORY]


HELP_OVERVIEW = (
    "A serious xianxia journey at a casual pace. Most sessions take ~15 minutes: "
    "daily stipend, cultivate, explore, craft.\n\n"
    "Select a category below to learn more.\n\n"
    "**Quick links:** `/cooldown` shows what's ready right now · "
    "`/profile` shows your realm and stats · `/techniques` manages your build"
)

HELP_BY_CATEGORY: list[tuple[str, str, str]] = [
    (
        "getting_started",
        "Getting Started",
        "`/start` — awaken with Elder Yunjian (your story continues in your **abode**)\n"
        "`/story` — reopen the Elder's tale in your abode\n"
        "`/profile` — cultivation dashboard with activity timers and martial summary\n"
        "`/roots` — spirit root tier list & stat bonuses\n"
        "`/help` — this guide\n"
        "`/cooldown` — see what is ready and what is waiting\n"
        "`/remind` — opt-in DM when cultivate, gather, hunt, adventure, dungeon, duel, or daily is ready\n"
        "`/reset` — erase your character (`confirm=true`), then **`/start`** again",
    ),
    (
        "cultivation",
        "Cultivation",
        "`/daily` — daily qi stipend (UTC midnight reset)\n"
        "`/cultivate` — gather qi (15 min cooldown; rare dao events may surge qi or drop manuals)\n"
        "`/breakthrough` — advance to the next realm when qi is full\n"
        "`/reroll_root` — change spirit root (1 free, then spirit stones + 7-day wait)\n\n"
        "Qi is the fuel of cultivation. Fill your dantian, then breakthrough to expand your capacity "
        "and unlock new techniques, areas, and equipment.",
    ),
    (
        "exploration",
        "Exploration",
        "`/gather` — herbs, scroll ink, inscription materials (5 min cooldown)\n"
        "`/hunt` — spirit beast combat with buttons; cooldown starts when the fight ends\n"
        "`/adventure` — choices, combat segments, and karma-shifting moral dilemmas\n"
        "`/adventure-continue` — resume a paused adventure run\n"
        "`/adventure-abandon` — quit an active adventure\n"
        "`/areas` — compare zones, loot tables, and realm requirements\n\n"
        "Exploration routes feed materials, manuals, and karma. "
        "Each lane has distinct rewards — gathering for herbs, hunting for beast parts, "
        "adventures for rare events and moral choices.",
    ),
    (
        "dungeons_gear",
        "Dungeons & Gear",
        "`/dungeon` — realm dungeon solo or tag up to **3** allies (they `/accept` to join)\n"
        "`/forge` — craft equipment into your stash\n"
        "`/equip` — equip forged gear\n"
        "`/recycle` — reclaim materials from old gear\n"
        "`/affix` — apply affix bonuses to equipment\n"
        "`/loadout` • `/stats` — foundation, gear, and combat stat breakdown\n"
        "`/temper` — permanent body stat upgrades from beast cores and herbs\n"
        "`/meridian` — spend meridian points earned from cultivate and gather\n\n"
        "Dungeons require a key (crafted via `/craft key`). Cooperative runs scale rewards "
        "with party size.",
    ),
    (
        "techniques",
        "Martial Techniques",
        "`/techniques` — equipped loadout, skill library, unlock manuals, equip, and upgrade\n"
        "`/item` — inspect any item; manuals show art type and combat effects\n"
        "`/learn` — learn a technique from a manual in your bag\n"
        "`/equip-technique` — assign learned techniques to your loadout slots\n"
        "`/upgrade-technique` — raise a technique's rank with materials + fragments\n"
        "`/craft manual` — bind technique fragments into a manual\n\n"
        "Techniques have active effects (used in combat) and passive triggers (always active). "
        "Load budget limits how many techniques you can equip based on your realm. "
        "Manuals drop from hunt, adventure, cultivate, breakthrough, dungeon, shop, and `/craft manual`.",
    ),
    (
        "economy",
        "Economy & Crafting",
        "`/inventory` — browse items grouped by category\n"
        "`/item` — full item card with effects, uses, and drop sources\n"
        "`/shop` — browse or buy with spirit stones (autocomplete)\n"
        "`/recipes` — browse available recipes by category\n"
        "`/craft pill` — brew consumables (shortages tell you where to farm)\n"
        "`/craft key` — craft dungeon keys\n"
        "`/craft manual` — bind technique fragments into a manual\n"
        "`/use` — consume pills or items (autocomplete from your bag)\n\n"
        "Spirit stones are the currency. Farming routes: gather, hunt, adventure, dungeon. "
        "Crafting paths: pills, keys, manuals, equipment.",
    ),
    (
        "social_pvp",
        "Social & PvP",
        "`/duel` — challenge another cultivator; Accept opens a private arena match\n"
        "`/leaderboard` — top cultivators in this server\n"
        "`/clan-create` • `/clan-join` • `/clan-leave` • `/clan` • `/clan-invite` • `/clan-invites` — player-run clans\n"
        "`/sect-list` • `/sect` • `/sect-join` • `/sect-leave` • `/sect-task` • `/sect-shop` • `/sect-buy` — martial sects\n\n"
        "Clans are player-created, server-scoped. Sects are fixed in-world orders with "
        "karma gates, realm requirements, daily tasks, and merit shops. "
        "Duel loadouts must pass PvP legality checks (caps on legendary, control, shield, healing, survival).",
    ),
]

HELP_CATEGORY_LABELS = {k: v for k, v, _ in HELP_BY_CATEGORY}
HELP_CATEGORY_BODIES = {k: body for k, _, body in HELP_BY_CATEGORY}
HELP_CATEGORY_IDS = [k for k, _, _ in HELP_BY_CATEGORY]


def build_category_embed(category_id: str) -> "discord.Embed":
    import discord

    title = HELP_CATEGORY_LABELS.get(category_id, "Help")
    body = HELP_CATEGORY_BODIES.get(category_id, "")
    embed = discord.Embed(title=title, description=body, color=discord.Color.blurple())
    embed.set_footer(text="Use `/help` for the overview · `/cooldown` to see timers")
    return embed


COOLDOWN_COMMANDS: list[tuple[str, str, str]] = [
    ("cultivate", "/cultivate", "cultivate_cooldown_seconds"),
    ("gather", "/gather", "gather_cooldown_seconds"),
    ("hunt", "/hunt", "hunt_cooldown_seconds"),
    ("daily", "/daily", "daily_cooldown_seconds"),
    ("adventure", "/adventure", "adventure_cooldown_seconds"),
    ("dungeon", "/dungeon", "dungeon_cooldown_seconds"),
    ("duel", "/duel", "pvp_cooldown_seconds"),
]

NO_COOLDOWN_COMMANDS = (
    "/profile · /inventory · /item · /loadout · /stats · /recipes · /roots · /breakthrough · "
    "/techniques · /craft pill · /craft key · /craft manual · "
    "/forge · /equip · /recycle · /affix · /shop · /use · /help · /cooldown · /remind · /leaderboard · /clan · /sect · "
    "/areas · /adventure-continue · /adventure-abandon · /reset"
)


def _daily_claimed_today(player: Player, now: datetime) -> bool:
    if player.last_daily_at is None:
        return False
    last = player.last_daily_at
    if last.tzinfo is None:
        last_day = last.date()
    else:
        last_day = last.astimezone(timezone.utc).date()
    return last_day == now.date()


def build_cooldown_lines(
    player: Player,
    cfg: Config,
    now: datetime,
    remaining_fn,
    session: Session | None = None,
) -> list[str]:
    from .cooldown_haste import get_haste_reduction_seconds

    lines: list[str] = []
    for _key, label, attr in COOLDOWN_COMMANDS:
        seconds = getattr(cfg, attr)
        last_map = {
            "cultivate": player.last_cultivate_at,
            "gather": player.last_gather_at,
            "hunt": player.last_hunt_at,
            "adventure": player.last_adventure_at,
            "dungeon": player.last_dungeon_at,
            "duel": player.last_pvp_at,
            "daily": player.last_daily_at,
        }
        last = last_map.get(_key)
        remaining = remaining_fn(now, last, seconds)
        haste = 0
        if session is not None and _key in {
            "cultivate",
            "adventure",
            "dungeon",
            "duel",
            "gather",
            "hunt",
            "daily",
        }:
            haste = get_haste_reduction_seconds(session, player.id, _key)
            if haste > 0:
                remaining = max(0, remaining - haste)
        interval = _format_seconds(seconds)
        status = format_cooldown_status(remaining)
        haste_note = f" · pill haste −{_format_seconds(haste)}" if haste > 0 else ""
        lines.append(f"**{label}** — every {interval} · **{status}**{haste_note}")
    return lines


def get_reroll_cooldown_line(player: Player, now: datetime) -> str:
    if not player.spirit_root_reroll_free_used:
        return "**/reroll_root** — **1 free reroll** available"
    if player.spirit_root_last_reroll_at is None:
        return "**/reroll_root** — 50 stones · **Ready now** (7-day gate after use)"
    last = player.spirit_root_last_reroll_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    else:
        last = last.astimezone(timezone.utc)
    gate = 7 * 24 * 3600
    elapsed = (now - last).total_seconds()
    remaining = max(0, int(gate - elapsed))
    if remaining <= 0:
        return "**/reroll_root** — 50 stones · **Ready now**"
    return f"**/reroll_root** — 50 stones · wait **{_format_seconds(remaining)}**"


# Timeless command descriptions — what the command does, not what to run next.
_COMMAND_HINTS: dict[str, str] = {
    "help": "Browse categories for command details. **`/cooldown`** shows live activity timers.",
    "profile": (
        "Your cultivation trophy — realm, martial dao, forged gear, and public record. "
        "View others with **`/profile @player`**. **`/cooldown`** shows activity timers."
    ),
    "techniques": (
        "**Equip Skill** fills slots 1–4 (active) and passive slots. "
        "**Skill Library** lists every art you know. **Unlock Skill** studies manuals from your bag."
    ),
    "inventory": "Item names only — **`/item <name>`** shows effects, crafting, and where to farm.",
    "item": "Manuals show art type and combat details. **`/techniques`** unlocks and equips them.",
    "craft_manual": "The manual is bound. Open **`/techniques`** → **Unlock Skill** to study it.",
    "adventure": (
        "Each segment offers **choices** — safer paths succeed more often; "
        "bold moves risk failure but can spike loot. Moral choices shift **karma**."
    ),
    "gather": "Herb and ore runs — materials feed **`/craft pill`** recipes.",
    "hunt": "Turn-based combat against spirit beasts. Wins yield cores, fragments, and manual drops.",
    "roots": "Spirit root tiers and stat bonuses. **`/reroll_root`** once free, then stones + a wait.",
    "recipes": "Recipe browser — missing materials show where to farm.",
    "forge": "Forged gear lands in your stash. **`/equip`** to wear it.",
    "equip": "Worn gear updates **`/stats`** and **`/loadout`** totals.",
    "recycle": "Spirit stones returned from dismantled gear.",
    "unequip": "Gear returns to stash.",
    "gear": "Current worn loadout — swap with **`/equip`**.",
    "stats": "Foundation, gear, and combat stat breakdown.",
    "craft_pill": "Brew consumables — shortages list farm locations.",
    "craft_key": "Dungeon keys for **`/dungeon`** cooperative runs.",
    "dungeon": "Cooperative room combat — tag allies and they **`/accept`** to join.",
    "areas": "Zone comparison — loot tables and realm gates.",
    "shop": "Spirit stone marketplace.",
    "use": "Consumes pills and items. Lingering effects show on **`/loadout`**.",
    "affix": "Affix bonuses apply to the selected gear piece.",
    "loadout": "Equipped gear and technique summary.",
    "duel": "Arena PvP — loadout must pass legality checks.",
    "reroll_root": "Spirit root changed — passive bonuses update on **`/loadout`**.",
    "leaderboard": "Server cultivation rankings.",
    "explore": "Long expedition runs with vitality tracking and rare findings.",
    "achievements": "Milestones earned through cultivation, combat, and discovery.",
}


def get_next_steps(
    command: str,
    player: Player | None,
    session: Session | None,
    cfg: Config,
    now: datetime,
    remaining_fn,
) -> str:
    if player is None:
        return "You have not started yet. Use **`/start`** to begin, then **`/help`** for the full guide."

    if command == "start":
        return (
            "You begin with **neutral karma (0)**. Moral choices on **`/adventure`** shift it. "
            "Elder Yunjian guides your awakening in your abode."
        )

    if command == "cooldown":
        cap = qi_cap(player.realm_index, player.substage, player)
        hints: list[str] = []
        checks = (
            ("cultivate", "/cultivate", player.last_cultivate_at, cfg.cultivate_cooldown_seconds),
            ("daily", "/daily", player.last_daily_at, cfg.daily_cooldown_seconds),
            ("gather", "/gather", player.last_gather_at, cfg.gather_cooldown_seconds),
            ("hunt", "/hunt", player.last_hunt_at, cfg.hunt_cooldown_seconds),
            ("adventure", "/adventure", player.last_adventure_at, cfg.adventure_cooldown_seconds),
        )
        for _key, label, last_at, cooldown in checks:
            if remaining_fn(now, last_at, cooldown) == 0:
                hints.append(f"**`{label}`** — ready")
        if player.qi >= cap:
            hints.append("**Qi pool full** — **`/breakthrough`** available")
        if not hints:
            return "All timed activities are recovering. Timers above show when each returns."
        return "Ready now: " + " · ".join(hints)

    if command == "cultivate":
        cap = qi_cap(player.realm_index, player.substage, player)
        qi_pct = 0 if cap <= 0 else int(min(100, player.qi / cap * 100))
        return f"Qi pool: **{player.qi}/{cap}** ({qi_pct}%)."

    if command == "breakthrough":
        cap = qi_cap(player.realm_index, player.substage, player)
        if player.qi < cap:
            return f"Qi spent or attempt failed — pool at **{player.qi}/{cap}**."
        return "Realm breakthrough succeeded — your cap and arts may have expanded."

    if command == "daily":
        return f"Daily stipend claimed. Streak: **{player.daily_streak}**."

    if command.startswith("clan"):
        return "Clan roster and contribution — **`/clan`** for details."

    if command.startswith("sect"):
        return "**`/sect-task`** shows today's sect goal and progress."

    return _COMMAND_HINTS.get(command, "See **`/help`** for commands · **`/cooldown`** for timers.")


def add_guidance_to_embed(
    embed,
    command: str,
    player: Player | None,
    session: Session | None,
    cfg: Config,
    now: datetime,
    remaining_fn,
) -> None:
    from .story_mode import should_show_command_guidance

    if not should_show_command_guidance(player):
        return
    next_steps = get_next_steps(command, player, session, cfg, now, remaining_fn)
    embed.add_field(name="What happens next", value=next_steps, inline=False)
    embed.set_footer(text=GUIDANCE_FOOTER)


def format_guidance_content(
    command: str,
    player: Player | None,
    session: Session | None,
    cfg: Config,
    now: datetime,
    remaining_fn,
) -> str | None:
    """Plain-text guidance for image-only command replies."""
    if player is None:
        return None
    from .story_mode import should_show_command_guidance

    if not should_show_command_guidance(player):
        return None
    next_steps = get_next_steps(command, player, session, cfg, now, remaining_fn)
    return f"**What happens next**\n{next_steps}\n_{GUIDANCE_FOOTER}_"


def build_help_embed(category_id: str | None = None) -> "discord.Embed":
    import discord

    if category_id:
        title = HELP_CATEGORY_LABELS.get(category_id, "Help")
        body = HELP_CATEGORY_BODIES.get(category_id, "")
        embed = discord.Embed(title=title, description=body, color=discord.Color.blurple())
        embed.set_footer(text="Select another category below · `/cooldown` to see timers")
        return embed

    embed = discord.Embed(
        title="Cultivation Guide",
        description=HELP_OVERVIEW,
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=GUIDANCE_FOOTER)
    return embed


def build_category_embed(category_id: str) -> "discord.Embed":
    """Alias — preferred when the caller knows the category."""
    return build_help_embed(category_id)


def build_cooldown_embed(
    player: Player,
    cfg: Config,
    now: datetime,
    remaining_fn,
    session: Session | None = None,
) -> "discord.Embed":
    import discord

    lines = build_cooldown_lines(player, cfg, now, remaining_fn, session=session)
    lines.append(get_reroll_cooldown_line(player, now))

    embed = discord.Embed(
        title=f"{player.dao_name} — Cooldowns",
        description="Timers use UTC. Commands with no timer are listed below.",
        color=discord.Color.dark_teal(),
    )
    embed.add_field(name="Timed commands", value="\n".join(lines), inline=False)
    embed.add_field(name="No cooldown", value=NO_COOLDOWN_COMMANDS, inline=False)

    from .story_mode import should_show_command_guidance

    if should_show_command_guidance(player):
        next_steps = get_next_steps("cooldown", player, None, cfg, now, remaining_fn)
        embed.add_field(name="Ready now", value=next_steps, inline=False)
    embed.set_footer(text=GUIDANCE_FOOTER)
    return embed
