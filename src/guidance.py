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
        "**Your first session:** claim your daily stipend, cultivate qi, and "
        "venture into the **Whispering Bamboo Grove** when you are ready."
    )


def get_start_next_steps() -> str:
    return (
        "1. **`/daily`** — claim spirit stones and qi (starts the **Outer Disciple Trial**).\n"
        "2. **`/profile`** — dashboard, trial step, timers, **Cultivate** button.\n"
        "3. **`/cultivate`** once when ready (or use the profile button).\n"
        "4. **`/hunt`** in Whispering Bamboo Grove — win your first beast fight.\n"
        "5. **`/learn`** — study the origin manual in your bag, then **`/equip-technique`**.\n"
        "6. **`/adventure`** — complete the sage's trial (karma choice).\n"
        "7. **`/breakthrough`** when qi is full · **`/help`** anytime.\n\n"
        "Not happy with your spirit root? **`/reroll_root`** once for free."
    )


def get_abode_welcome_intro(dao_name: str) -> str:
    return (
        f"**{dao_name}**, this is your abode — a private chamber where the world cannot intrude.\n\n"
        "Cultivate qi here, claim your daily stipend, hunt spirit beasts, and walk the path of adventure. "
        "Only you and the heavens witness what unfolds within these walls.\n\n"
        "**Begin here**\n"
        "1. **`/daily`** — claim spirit stones and qi\n"
        "2. **`/profile`** — your dashboard, trial step, and timers\n"
        "3. **`/cultivate`** when ready · **`/help`** for the full guide"
    )


def get_help_sections() -> list[tuple[str, str]]:
    return [
        (
            "Getting started",
            "`/start` — choose your dao name and origin (starting gifts and manuals)\n"
            "`/profile` — cultivation dashboard with activity timers and martial summary\n"
            "`/roots` — spirit root tier list & stat bonuses\n"
            "`/help` — this guide\n"
            "`/cooldown` — see what is ready and what is waiting\n"
            "`/remind` — opt-in DM when cultivate, gather, hunt, adventure, dungeon, duel, or daily is ready",
        ),
        (
            "Core loop (~15 min/day)",
            "`/daily` — daily stipend (UTC reset)\n"
            "`/cultivate` — gather qi (15 min cooldown; rare dao events may surge qi or drop manuals)\n"
            "`/breakthrough` — advance when qi is full\n"
            "`/reroll_root` — change spirit root (1 free, then stones + 7-day wait)",
        ),
        (
            "Three activity lanes",
            "**Cultivation** — `/cultivate` · `/breakthrough`\n"
            "**Resource** — `/gather` · `/hunt` (5 min, button combat on hunt)\n"
            "**Story** — `/adventure` · `/dungeon` (20 min / 2 hr cooldowns)",
        ),
        (
            "Martial techniques",
            "`/techniques` — loadout, study & equip menus\n"
            "`/technique` — read what an art does (manual in bag or already learned)\n"
            "`/item` — full item card; manuals show art type and combat effect\n"
            "`/learn` — study a manual from inventory (autocomplete)\n"
            "`/equip-technique` — **active slots 1–4** for manual arts · **passive slot** for always-on arts\n"
            "Manuals from **hunt**, **adventure**, **cultivate**, **breakthrough**, **dungeon**, **shop**, **`/craft manual`**",
        ),
        (
            "Exploration & crafting",
            "`/gather` — herbs, scroll ink, inscription materials (5 min)\n"
            "`/hunt` — button combat; cooldown applies when the fight ends\n"
            "`/adventure` — choices, combat segments, karma shifts\n"
            "`/adventure-continue` · `/adventure-abandon` — resume or quit a run\n"
            "`/areas` — compare zones, loot tables, and realm requirements\n"
            "`/recipes` — pill, key, and forge recipes\n"
            "`/inventory` — item names by category · `/item` — full item card\n"
            "`/shop` — browse or buy with spirit stones (autocomplete)\n"
            "`/craft pill` · `/craft key` · `/craft manual` — autocomplete craftables\n"
            "`/use` — consume pills (autocomplete from your bag)",
        ),
        (
            "Dungeons & gear",
            "`/dungeon` — key-gated runs (autocomplete when you hold a key)\n"
            "`/forge` · `/equip` — craft gear and apply affix stones (autocomplete)\n"
            "`/loadout` · `/stats` — gear affixes and combat stat breakdown",
        ),
        (
            "Social",
            "`/duel` — challenge a player; Accept opens a private arena with hunt-style technique combat\n"
            "`/leaderboard` — top cultivators in this server\n"
            "`/clan-create` · `/clan-join` · `/clan-leave` · `/clan` · `/clan-invite` · `/clan-invites` — player clans\n"
            "`/sect-list` · `/sect` · `/sect-join` · `/sect-leave` · `/sect-task` · `/sect-shop` · `/sect-buy` — martial sects",
        ),
        (
            "Other",
            "`/reset` — rewrite your character (requires confirm)\n\n"
            "**Tips:** Stamina affects cultivate gains. **Karma** (earned in adventures) shifts breakthrough risk and manual drops. "
            "Origin and spirit root passively shape your dao.",
        ),
    ]


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
    "/profile · /inventory · /item · /technique · /loadout · /stats · /recipes · /roots · /breakthrough · "
    "/techniques · /learn · /equip-technique · /craft pill · /craft key · /craft manual · "
    "/forge · /shop · /use · /equip · /help · /cooldown · /remind · /leaderboard · /clan · /sect · "
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
        if _key == "daily":
            if _daily_claimed_today(player, now):
                lines.append(f"**{label}** — claimed today · next reset UTC midnight")
            else:
                lines.append(f"**{label}** — **Ready now** (once per UTC day)")
            continue
        last_map = {
            "cultivate": player.last_cultivate_at,
            "gather": player.last_gather_at,
            "hunt": player.last_hunt_at,
            "adventure": player.last_adventure_at,
            "dungeon": player.last_dungeon_at,
            "duel": player.last_pvp_at,
        }
        last = last_map.get(_key)
        remaining = remaining_fn(now, last, seconds)
        haste = 0
        if session is not None and _key in {"cultivate", "adventure", "dungeon", "duel", "gather", "hunt"}:
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

    cap = qi_cap(player.realm_index, player.substage)
    qi_pct = 0 if cap <= 0 else int(min(100, player.qi / cap * 100))
    cult_ready = remaining_fn(now, player.last_cultivate_at, cfg.cultivate_cooldown_seconds) == 0
    gather_ready = remaining_fn(now, player.last_gather_at, cfg.gather_cooldown_seconds) == 0
    hunt_ready = remaining_fn(now, player.last_hunt_at, cfg.hunt_cooldown_seconds) == 0
    adv_ready = remaining_fn(now, player.last_adventure_at, cfg.adventure_cooldown_seconds) == 0
    daily_ready = not _daily_claimed_today(player, now)

    hints: list[str] = []

    if command == "start":
        return (
            "You begin with **neutral karma (0)**. Help or harm others on **`/adventure`** to shift it. "
            "Next: **`/daily`**, then **`/profile`**."
        )

    if command == "help":
        return "Run **`/cooldown`** to see what you can do right now, then **`/profile`** to check your realm."

    if command == "cooldown":
        if cult_ready:
            hints.append("**`/cultivate`** is ready — gather qi now.")
        if daily_ready:
            hints.append("**`/daily`** stipend is waiting.")
        if gather_ready:
            hints.append("**`/gather`** — quick herb and ore farming.")
        if hunt_ready:
            hints.append("**`/hunt`** — spirit beasts for cores and parts.")
        if adv_ready:
            hints.append("**`/adventure`** — try Whispering Bamboo Grove for materials.")
        if player.qi >= cap:
            hints.append("Your qi is full — consider **`/breakthrough`**.")
        if not hints:
            hints.append("While timers recover, review **`/inventory`** or plan **`/craft pill`** recipes.")
        return " ".join(hints)

    if command == "profile":
        if daily_ready:
            hints.append("Claim **`/daily`** first if you have not today.")
        if cult_ready:
            hints.append("Press **Cultivate** below or use **`/cultivate`**.")
        elif gather_ready:
            hints.append("Cultivate is on cooldown — **`/gather`** or **`/hunt`** for materials.")
        elif hunt_ready:
            hints.append("Try **`/hunt`** for beast cores and manual drops.")
        elif adv_ready:
            hints.append("Cultivate is on cooldown — try **`/adventure`**.")
        if player.qi >= cap:
            hints.append(f"Qi is at {qi_pct}% — **`/breakthrough`** when ready.")
        hints.append("Open **`/techniques`** to study manuals and equip your loadout.")
        return " ".join(hints) if hints else "Check **`/cooldown`** for your next action."

    if command == "techniques":
        return (
            "Use the **menus** to study manuals or equip techniques. "
            "Use **`/technique <name>`** to see whether an art is **active** (slots 1–4) or **passive** (always on). "
            "Farm manuals via **`/hunt`**, **`/adventure`**, **`/dungeon`**, or **`/shop`**. "
            "Bind fragments with **`/craft manual`**."
        )

    if command == "technique":
        return (
            "When you **`/learn`** a manual, equip it with **`/equip-technique`**. "
            "**Active** arts go in slots **1–4**; **passive** arts go in the **passive slot**. "
            "Test the art in **`/hunt`** or **`/adventure`** combat."
        )

    if command == "learn":
        return (
            "Read the manual first with **`/technique`** if you are unsure. "
            "After studying, **`/equip-technique`**: actives → slots **1–4**, passives → **passive slot**. "
            "**`/hunt`** to test it in combat."
        )

    if command == "inventory":
        return "Names only here — use **`/item <name>`** for effects, crafting, and farm locations."

    if command == "item":
        return (
            "Manuals show **art type** (active vs passive) and combat details here. "
            "Study with **`/learn`**, then **`/equip-technique`**. "
            "Or use **`/technique <name>`** for the same scripture card."
        )

    if command == "craft_manual":
        return "Study the bound manual with **`/learn`**, then **`/equip-technique`**. **`/techniques`** shows your full build."

    if command == "cultivate":
        if player.qi >= cap:
            return f"Your qi nears its limit ({player.qi}/{cap}). Attempt **`/breakthrough`** when the moment feels right."
        if adv_ready:
            return f"Qi: {player.qi}/{cap} ({qi_pct}%). While cultivate cools down, **`/gather`**, **`/hunt`**, or **`/adventure`** gather materials."
        if gather_ready or hunt_ready:
            return f"Qi: {player.qi}/{cap} ({qi_pct}%). **`/gather`** and **`/hunt`** are quick 5 min farms."
        return f"Qi: {player.qi}/{cap} ({qi_pct}%). See **`/cooldown`** for when you can cultivate again."

    if command == "breakthrough":
        if player.qi < cap:
            return f"Breakthrough failed or qi was spent. **`/cultivate`** to rebuild ({player.qi}/{cap} qi)."
        return "The realm shifts. **`/profile`** to see your new stage, then **`/cultivate`** anew."

    if command == "daily":
        if cult_ready:
            return "Stipend accepted. **`/cultivate`** while your daily luck holds."
        return "Stipend stored. **`/cooldown`** shows when cultivate returns."

    if command == "adventure":
        return (
            "Each segment offers **choices** — safer paths succeed more often; "
            "bold moves can fail the run or spike loot. Moral choices shift **karma** and manual pools. "
            "**`/recipes`** for cooldown pills."
        )

    if command == "gather":
        return "Quick herb runs — Green Dew Herbs craft **Qi Gathering** pills (`/recipes`). **`/hunt`** for beast cores → Tempering."

    if command == "hunt":
        return (
            "Button combat against spirit beasts. Win for cores, fragments, and manual drops. "
            "**`/techniques`** to equip arts before you hunt."
        )

    if command == "roots":
        return "Compare with **`/profile`** and **`/stats`**. Reroll once free via **`/reroll_root`**."

    if command == "recipes":
        return "Farm materials with **`/adventure`**, then **`/craft pill`**. Cooldown pills stack before a busy session."

    if command == "forge":
        return "Apply **`/equip`** with an Affix Stone on forged gear. Check **`/stats`** for totals."

    if command == "stats":
        return "Higher **Fortune** and **Insight** improve adventure drops and rare events. **`/loadout`** for details."

    if command in ("craft_pill", "craft_key"):
        return "Autocomplete lists only recipes you can craft now. **`/use`** pills before a busy session."

    if command == "dungeon":
        return "Rest and recover. **`/cooldown`** tracks dungeon timer · craft another key with **`/craft key`**."

    if command == "inventory":
        return "Compare farming spots with **`/areas`**. Craft via **`/craft pill`** or **`/craft key`**."

    if command == "areas":
        return "Ready? **`/adventure`** with the area that matches your realm. Check **`/inventory`** after."

    if command == "shop":
        return "Use **`/use`** on haste pills before your next run. **`/loadout`** to see purchased gear."

    if command == "use":
        return "Active effects show on **`/loadout`**. **`/cultivate`** or **`/adventure`** to use them."

    if command == "equip":
        return "Affix applied. **`/stats`** and **`/loadout`** show your full bonuses."

    if command == "loadout":
        return "Forge missing slots with **`/forge`**. Venture out with **`/adventure`** or **`/duel`**."

    if command == "duel":
        return "Honor satisfied. **`/cooldown`** before another duel · **`/cultivate`** to recover."

    if command == "reroll_root":
        return "Your root changed your passive bonuses. **`/loadout`** to see the difference."

    if command == "leaderboard":
        return "Climb higher with **`/cultivate`** and **`/breakthrough`**. **`/adventure`** for an edge."

    if command.startswith("clan"):
        return "Clan qi grows when you **`/cultivate`**. **`/profile`** shows your clan and sect."

    if command.startswith("sect"):
        return "Earn merit from activities and **`/sect-task`** dailies. Spend merit at **`/sect-shop`**."

    return "See **`/help`** for commands or **`/cooldown`** for what is ready."


def add_guidance_to_embed(
    embed,
    command: str,
    player: Player | None,
    session: Session | None,
    cfg: Config,
    now: datetime,
    remaining_fn,
) -> None:
    next_steps = get_next_steps(command, player, session, cfg, now, remaining_fn)
    embed.add_field(name="What happens next", value=next_steps, inline=False)
    embed.set_footer(text=GUIDANCE_FOOTER)


def build_help_embed() -> "discord.Embed":
    import discord

    embed = discord.Embed(
        title="Cultivation Guide",
        description=(
            "A serious xianxia journey at a casual pace. "
            "Most sessions take ~15 minutes: daily stipend, cultivate, explore, craft."
        ),
        color=discord.Color.blurple(),
    )
    for title, body in get_help_sections():
        embed.add_field(name=title, value=body, inline=False)
    embed.set_footer(text=GUIDANCE_FOOTER)
    return embed


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

    next_steps = get_next_steps("cooldown", player, None, cfg, now, remaining_fn)
    embed.add_field(name="Suggested next step", value=next_steps, inline=False)
    embed.set_footer(text=GUIDANCE_FOOTER)
    return embed
