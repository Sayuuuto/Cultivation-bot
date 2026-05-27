from __future__ import annotations

from datetime import datetime

import discord
from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .combat.loadout import (
    ACTIVE_SLOTS,
    PASSIVE_SLOT,
    ensure_starter_techniques,
    get_learned_technique_ids,
    get_learned_techniques,
    get_loadout,
)
from .combat.catalog import get_technique
from .combat_stats import PlayerCombatStats, format_combat_stats_block
from .command_choices import can_bind_technique_manual, list_player_manuals
from .config import Config
from .cooldown_haste import get_haste_reduction_seconds
from .game import qi_cap
from .inventory import get_player_inventory
from .item_info import format_manual_bind_progress
from .karma import karma_tier_label
from .ui.formatting import format_qi_bar
from .models import Clan, Player


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


def _lane_status(
    remaining_fn,
    now: datetime,
    last_at: datetime | None,
    cooldown_seconds: int,
    *,
    session: Session | None = None,
    player_id: int | None = None,
    activity: str | None = None,
) -> str:
    remaining = remaining_fn(now, last_at, cooldown_seconds)
    if session is not None and player_id is not None and activity is not None:
        haste = get_haste_reduction_seconds(session, player_id, activity)
        if haste > 0:
            remaining = max(0, remaining - haste)
    if remaining <= 0:
        return "**Ready**"
    return f"**{_format_seconds(remaining)}**"


def format_activity_lanes(
    player: Player,
    cfg: Config,
    now: datetime,
    remaining_fn,
    session: Session,
) -> str:
    lines = [
        f"🌱 **Cultivate** — {_lane_status(remaining_fn, now, player.last_cultivate_at, cfg.cultivate_cooldown_seconds, session=session, player_id=player.id, activity='cultivate')}",
        f"🌿 **Gather** — {_lane_status(remaining_fn, now, player.last_gather_at, cfg.gather_cooldown_seconds, session=session, player_id=player.id, activity='gather')}",
        f"⚔️ **Hunt** — {_lane_status(remaining_fn, now, player.last_hunt_at, cfg.hunt_cooldown_seconds, session=session, player_id=player.id, activity='hunt')}",
        f"📜 **Adventure** — {_lane_status(remaining_fn, now, player.last_adventure_at, cfg.adventure_cooldown_seconds, session=session, player_id=player.id, activity='adventure')}",
        f"🏚️ **Dungeon** — {_lane_status(remaining_fn, now, player.last_dungeon_at, cfg.dungeon_cooldown_seconds, session=session, player_id=player.id, activity='dungeon')}",
    ]
    return "\n".join(lines)


def format_martial_dao_summary(session: Session, player: Player) -> str:
    ensure_starter_techniques(session, player.id)
    learned = get_learned_techniques(session, player.id)
    loadout = get_loadout(session, player.id)
    manuals = list_player_manuals(session, player.id)

    loadout_bits: list[str] = []
    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        name = get_technique(technique_id).name if technique_id else "—"
        loadout_bits.append(f"{slot}: **{name}**")
    passive_id = loadout.get(PASSIVE_SLOT)
    passive_name = get_technique(passive_id).name if passive_id else "—"
    loadout_bits.append(f"Passive: **{passive_name}**")

    lines = [
        "**Loadout** — " + " · ".join(loadout_bits),
        f"**Learned** — {len(learned)} technique(s)",
    ]
    if manuals:
        lines.append(f"**Manuals** — {len(manuals)} unread scroll(s) ready to study")
    elif can_bind_technique_manual(session, player.id):
        lines.append("**Manuals** — all binding materials ready; use **`/craft manual`**")
    else:
        bind_progress = format_manual_bind_progress(session, player.id)
        if bind_progress:
            lines.append(bind_progress)

    return "\n".join(lines)


def build_profile_embed(
    player: Player,
    session: Session,
    cfg: Config,
    now: datetime,
    *,
    offline_qi: int,
    combat: PlayerCombatStats,
    realm_display: str,
    remaining_fn,
) -> discord.Embed:
    cap = qi_cap(player.realm_index, player.substage, player)
    qi_pct = 0 if cap <= 0 else int(min(100, player.qi / cap * 100))
    breakthrough_hint = " · **Breakthrough ready**" if player.qi >= cap else ""

    identity_bits = [player.origin or "Unknown origin", player.spirit_root or "Unrevealed root"]
    identity_bits.append(karma_tier_label(player.karma))
    if player.clan_id is not None:
        clan = session.get(Clan, player.clan_id)
        if clan is not None:
            identity_bits.append(f"Clan: {clan.name}")
    if player.game_sect_id:
        from .game_sects import get_sect_def

        sect_def = get_sect_def(player.game_sect_id)
        if sect_def is not None:
            identity_bits.append(f"Sect: {sect_def.name} · {player.sect_merit} merit")

    embed = discord.Embed(
        title=f"{player.dao_name} — Cultivation Profile",
        description=" · ".join(identity_bits),
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="🌀 Cultivation",
        value=(
            f"**{realm_display}**\n"
            f"{format_qi_bar(player.qi, cap)} **{player.qi}/{cap}** ({qi_pct}%){breakthrough_hint}\n"
            f"🔥 Daily streak **{player.daily_streak}**"
        ),
        inline=False,
    )

    from .novice_trial import format_trial_progress

    trial_line = format_trial_progress(player)
    if trial_line:
        embed.add_field(name="Outer Disciple Trial", value=trial_line, inline=False)

    embed.add_field(
        name="Activity lanes",
        value=format_activity_lanes(player, cfg, now, remaining_fn, session),
        inline=False,
    )

    embed.add_field(
        name="Martial dao",
        value=format_martial_dao_summary(session, player),
        inline=False,
    )

    embed.add_field(
        name="Combat",
        value=format_combat_stats_block(combat),
        inline=False,
    )

    resources = (
        f"💎 Spirit stones **{player.spirit_stones}**\n"
        f"⚡ Stamina **{player.stamina}/100**"
    )
    embed.add_field(name="Resources", value=resources, inline=True)

    mod = get_character_modifiers(session, player)
    from .cultivation_preview import preview_cultivate_qi, format_active_cultivate_line, format_passive_qi_line

    cult_preview = preview_cultivate_qi(player, mod, cfg, now)
    cult_lines = [format_passive_qi_line(cult_preview)]
    cult_lines.append(format_active_cultivate_line(cult_preview, mod).replace("**", ""))
    embed.add_field(name="Next cultivate", value="\n".join(cult_lines), inline=True)

    if offline_qi > 0:
        embed.add_field(
            name="While you were away",
            value=f"**+{offline_qi} passive Qi** was added to your pool.",
            inline=False,
        )

    embed.set_footer(text="Use the buttons below to cultivate · /techniques for your martial build")
    return embed


def build_techniques_embed(session: Session, player: Player) -> discord.Embed:
    ensure_starter_techniques(session, player.id)
    learned = get_learned_techniques(session, player.id)
    loadout = get_loadout(session, player.id)
    manuals = list_player_manuals(session, player.id)

    lines = ["**Combat loadout**"]
    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        name = get_technique(technique_id).name if technique_id else "—"
        lines.append(f"Slot **{slot}**: {name}")
    passive_id = loadout.get(PASSIVE_SLOT)
    passive_name = get_technique(passive_id).name if passive_id else "—"
    lines.append(f"**Passive**: {passive_name}")

    lines.append("")
    lines.append("**Learned techniques**")
    if learned:
        align_icon = {"righteous": "☀️", "demonic": "🌑", "neutral": "⚖️"}
        for tech in learned:
            slot = next((s for s, tid in loadout.items() if tid == tech.technique_id), None)
            equipped = f" _(slot {slot})_" if slot else ""
            realm_gate = ""
            if player.realm_index < tech.min_realm:
                realm_gate = " _(realm locked)_"
            icon = align_icon.get(tech.alignment, "⚖️")
            hint = f" — _{tech.synergy_hint}_" if tech.synergy_hint else ""
            lines.append(
                f"• {icon} **{tech.name}** [{tech.role}/{tech.category}] CD **{tech.cooldown}**{equipped}{realm_gate}{hint}"
            )
    else:
        lines.append("_Basic Strike only — hunt, adventure, and shop manuals expand your art._")

    lines.append("")
    lines.append("**Manuals in your bag**")
    if manuals:
        for item_id, label in manuals:
            lines.append(f"• {label}")
    else:
        lines.append("_No unread manuals — `/hunt`, `/adventure`, `/shop`, or `/craft manual`._")

    if can_bind_technique_manual(session, player.id):
        lines.append("")
        lines.append("_You can bind a manual now with **`/craft manual`** or the menu below._")
    else:
        bind_progress = format_manual_bind_progress(session, player.id)
        if bind_progress:
            lines.append("")
            lines.append(f"_{bind_progress}_")

    embed = discord.Embed(
        title="Martial Techniques",
        description="\n".join(lines),
        color=discord.Color.dark_purple(),
    )
    embed.set_footer(text="Use the menus below, or /learn and /equip-technique with autocomplete")
    return embed
