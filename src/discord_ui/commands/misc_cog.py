from __future__ import annotations

import logging
import random
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ...areas_info import build_areas_embed
from ...character import get_character_modifiers
from ...combat_stats import compute_combat_stats
from ...config import get_config
from ...db import get_session
from ...discord_guild import delete_abode_channel, provision_new_cultivator, strip_realm_roles
from ...game import SPIRIT_ROOTS, collect_passive_qi, utcnow
from ...guidance import build_cooldown_embed, build_help_embed
from ...models import Clan, Player
from ...player_wipe import wipe_player_character
from ...player_dashboard import build_profile_embed
from ...reminders import (
    ACTIVITY_LABELS,
    REMINDER_ACTIVITIES,
    build_reminder_status_text,
    schedule_after_activity,
    set_all_reminders_enabled,
    set_reminder_enabled,
)
from ...roots_info import build_roots_embed
from ...story_delivery import (
    deliver_pending_story_to_abode,
    maybe_sync_elder_story,
    resolve_abode_channel,
    sync_player_story_to_abode,
)
from ...story_mode import (
    apply_path_bonuses,
    clear_pending,
    current_awaited_command,
    elder_trial_active,
    finalize_creation_state,
    get_node,
    get_path_def,
    get_pending,
    player_story_chapter,
    render_node_text,
    start_pending,
    story_command_available,
    unlock_chapter_two,
)
from ...story_views import (
    StoryView,
    build_story_embed,
    send_story_node_for_pending,
    send_story_node_for_player,
)
from ...ui.fonts import card_fonts_available, card_images_enabled
from ...ui.profile_card import build_profile_card_data, render_profile_card
from ..autocomplete import all_areas_autocomplete
from ..helpers import (
    get_guild_id,
    get_discord_id,
    ensure_player,
    NOT_STARTED_HINT,
    schedule_player_reminders,
    attach_guidance,
    interaction_ctx,
    realm_display,
    rng_for,
    format_seconds,
    activity_cooldown_remaining,
    cooldown_remaining,
    time_left,
    build_profile_view,
    _story_pending_update,
    _story_player_node_update,
    _story_continue_after_creation,
    _story_finalize_creation,
)

logger = logging.getLogger("cultivation_bot")

ROOT_CHOICES = [app_commands.Choice(name=r, value=r) for r in SPIRIT_ROOTS]

REMIND_ACTION_CHOICES = [
    app_commands.Choice(name="Status", value="status"),
    app_commands.Choice(name="Turn on", value="on"),
    app_commands.Choice(name="Turn off", value="off"),
]
REMIND_ACTIVITY_CHOICES = [
    app_commands.Choice(name="All timers", value="all"),
    *[app_commands.Choice(name=label, value=activity) for activity, label in ACTIVITY_LABELS.items()],
]


class MiscCog(commands.Cog):
    """Miscellaneous commands: start, story, profile, cooldown, etc."""

    @app_commands.command(name="start", description="Awaken on the cultivation path — Elder Yunjian awaits.")
    async def start_cmd(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            logger.info("CMD /start begin %s", interaction_ctx(interaction))
            await interaction.response.defer(thinking=True)

            if interaction.guild is None:
                await interaction.followup.send("This bot works inside a server.", ephemeral=False)
                return

            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)

            existing = ensure_player(session, guild_id, discord_id)
            if existing is not None:
                if elder_trial_active(existing):
                    await interaction.followup.send(
                        "You have already begun. Return to your **abode** and speak with **Elder Yunjian** — **`/story`**.",
                        ephemeral=False,
                    )
                else:
                    await interaction.followup.send(
                        "You have already begun. Check your **abode**, **`/profile`**, or **`/cooldown`**.",
                        ephemeral=False,
                    )
                return

            pending = get_pending(guild_id, discord_id)
            if pending is not None:
                if pending.dao_name and pending.abode_channel_id:
                    delivered = await deliver_pending_story_to_abode(
                        interaction,
                        pending,
                        session,
                        on_pending_update=_story_pending_update,
                        on_finalize=_story_finalize_creation,
                        redirect_start=str(interaction.channel_id) != pending.abode_channel_id,
                    )
                    if delivered:
                        channel = await resolve_abode_channel(interaction.guild, pending.abode_channel_id)
                        mention = channel.mention if channel else "your **abode**"
                        await interaction.followup.send(
                            f"Your awakening continues with **Elder Yunjian** in {mention}.",
                            ephemeral=False,
                        )
                        return
                await send_story_node_for_pending(
                    interaction,
                    pending,
                    edit=False,
                    on_pending_update=_story_pending_update,
                    on_finalize=_story_finalize_creation,
                )
                return

            pending = start_pending(guild_id, discord_id)
            await send_story_node_for_pending(
                interaction,
                pending,
                edit=False,
                on_pending_update=_story_pending_update,
                on_finalize=_story_finalize_creation,
            )
        finally:
            session.close()

    @app_commands.command(name="story", description="Continue your cultivation story with Elder Yunjian.")
    async def story_cmd(self, interaction: discord.Interaction) -> None:
        cfg = get_config()
        session = get_session()
        try:
            await interaction.response.defer(thinking=True)
            if interaction.guild is None:
                await interaction.followup.send("This bot works inside a server.", ephemeral=False)
                return

            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                pending = get_pending(guild_id, discord_id)
                if pending is not None:
                    if pending.dao_name and pending.abode_channel_id:
                        delivered = await deliver_pending_story_to_abode(
                            interaction,
                            pending,
                            session,
                            on_pending_update=_story_pending_update,
                            on_finalize=_story_finalize_creation,
                            redirect_start=False,
                        )
                        if delivered:
                            in_abode = str(interaction.channel_id) == pending.abode_channel_id
                            if in_abode:
                                await interaction.followup.send(
                                    "The Elder speaks above — answer them to continue your awakening.",
                                    ephemeral=False,
                                )
                            else:
                                channel = await resolve_abode_channel(
                                    interaction.guild, pending.abode_channel_id
                                )
                                mention = channel.mention if channel else "your **abode**"
                                await interaction.followup.send(
                                    f"**Elder Yunjian** continues in {mention}.",
                                    ephemeral=False,
                                )
                            return
                    await send_story_node_for_pending(
                        interaction,
                        pending,
                        edit=False,
                        on_pending_update=_story_pending_update,
                        on_finalize=_story_finalize_creation,
                    )
                    return
                await interaction.followup.send(
                    "Your story has not begun. Use **`/start`** to awaken.",
                    ephemeral=False,
                )
                return

            available, msg = story_command_available(player)
            if not available:
                await interaction.followup.send(msg, ephemeral=False)
                return

            if player.realm_index >= 1 and player_story_chapter(player) < 2:
                unlock_chapter_two(player)
                session.add(player)
                session.commit()

            if interaction.guild is not None and player.abode_channel_id:
                channel = await sync_player_story_to_abode(
                    interaction.client,
                    interaction.guild,
                    session,
                    player,
                    on_player_update=_story_player_node_update,
                )
                session.commit()
                if channel is not None:
                    await interaction.followup.send(
                        f"**Elder Yunjian** continues in {channel.mention}.",
                        ephemeral=False,
                    )
                    return

            await send_story_node_for_player(
                interaction,
                session,
                player,
                edit=False,
                on_player_update=_story_player_node_update,
            )
        finally:
            session.close()

    @app_commands.command(name="reroll_root", description="Reroll your spirit root (limited).")
    async def reroll_root_cmd(self, interaction: discord.Interaction) -> None:
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /reroll_root begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            free_used = bool(player.spirit_root_reroll_free_used)
            cost = 50
            gate_days = 7

            if not free_used:
                player.spirit_root = random.choice(SPIRIT_ROOTS)
                player.spirit_root_reroll_free_used = True
                player.spirit_root_last_reroll_at = now
                session.add(player)
                session.commit()
            else:
                if player.spirit_root_last_reroll_at is not None:
                    elapsed = now - player.spirit_root_last_reroll_at
                    if elapsed < timedelta(days=gate_days):
                        remaining = int((timedelta(days=gate_days) - elapsed).total_seconds())
                        await interaction.response.send_message(
                            f"Reroll is not yet ready. Wait {format_seconds(remaining)}.",
                            ephemeral=False,
                        )
                        return
                if player.spirit_stones < cost:
                    await interaction.response.send_message(
                        f"You need {cost} spirit stones for a reroll.",
                        ephemeral=False,
                    )
                    return

                player.spirit_stones -= cost
                player.spirit_root = random.choice(SPIRIT_ROOTS)
                player.spirit_root_last_reroll_at = now
                session.add(player)
                session.commit()

            embed = discord.Embed(
                title="Spirit Root Reforged",
                description=f"Your spirit root is now **{player.spirit_root}**.",
                color=discord.Color.green(),
            )
            attach_guidance(embed, "reroll_root", player, session, cfg, now)
            logger.info(
                "Reroll complete guild=%s user=%s free_used=%s new_root=%r stones=%s",
                guild_id,
                discord_id,
                free_used,
                player.spirit_root,
                player.spirit_stones,
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="reset", description="Erase your character so you can begin again with /start.")
    @app_commands.describe(confirm="Set to true to erase your cultivator record.")
    async def reset_cmd(self, interaction: discord.Interaction, confirm: bool) -> None:
        if not confirm:
            await interaction.response.send_message(
                "Set **`confirm=true`** to erase your character, then run **`/start`** for a new path.",
                ephemeral=False,
            )
            return

        session = get_session()
        try:
            logger.info("CMD /reset begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            await interaction.response.defer(thinking=True)

            abode_channel_id = wipe_player_character(session, player)
            session.commit()

            if interaction.guild is not None and isinstance(interaction.user, discord.Member):
                await delete_abode_channel(interaction.guild, abode_channel_id)
                await strip_realm_roles(interaction.user)

            logger.info("Character wiped guild=%s user=%s", guild_id, discord_id)

            embed = discord.Embed(
                title="Your Record Is Cleared",
                description=(
                    "Your cultivator data, inventory, techniques, and active sessions are gone.\n\n"
                    "Run **`/start`** to choose your dao name and origin and walk the path from the beginning."
                ),
                color=discord.Color.gold(),
            )
            await interaction.followup.send(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="profile", description="View a cultivation trophy — yours or another daoist's.")
    @app_commands.describe(player="Another cultivator to view (optional).")
    async def profile_cmd(
        self,
        interaction: discord.Interaction,
        player: discord.Member | None = None,
    ) -> None:
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /profile begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            viewer_discord_id = get_discord_id(interaction.user)
            target_member = player or interaction.user
            target_discord_id = get_discord_id(target_member)
            is_public_view = target_discord_id != viewer_discord_id

            target_player = ensure_player(session, guild_id, target_discord_id)
            if target_player is None:
                if is_public_view:
                    await interaction.response.send_message(
                        f"**{target_member.display_name}** has not begun cultivation yet.",
                        ephemeral=False,
                    )
                else:
                    await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            offline_qi = 0
            if not is_public_view:
                mod = get_character_modifiers(session, target_player)
                offline_qi = collect_passive_qi(target_player, now, cap_mult=mod.offline_efficiency)
                target_player.last_active_at = now
                session.add(target_player)
                session.commit()

            mod = get_character_modifiers(session, target_player)
            combat = compute_combat_stats(target_player, session, mod)

            view = None
            if not is_public_view:
                rng = rng_for(guild_id, viewer_discord_id)
                view = build_profile_view(
                    owner_discord_id=viewer_discord_id, cfg=cfg, rng=rng, player=target_player
                )

            guild_label = interaction.guild.name if interaction.guild else "Wandering Realm"
            display_name = target_member.display_name or target_member.name
            card_data = build_profile_card_data(
                session,
                target_player,
                combat,
                cfg,
                now,
                guild_label=guild_label,
                display_name=display_name,
                is_public_view=is_public_view,
            )

            avatar_image = None
            try:
                from io import BytesIO

                from PIL import Image

                avatar_bytes = await target_member.display_avatar.read()
                avatar_image = Image.open(BytesIO(avatar_bytes))
            except Exception:
                logger.debug("Profile avatar fetch skipped for %s", target_discord_id, exc_info=True)

            content_parts: list[str] = []
            if offline_qi > 0:
                content_parts.append(
                    f"Your formation bank released **+{offline_qi} Qi** into your cultivation pool."
                )

            from ...guidance import format_guidance_content

            file = None
            if card_images_enabled() and card_fonts_available():
                try:
                    png_bytes = render_profile_card(card_data, avatar_image)
                    from io import BytesIO

                    file = discord.File(BytesIO(png_bytes), filename="profile.png")
                except Exception:
                    logger.exception("Profile card render failed for %s", target_discord_id)

            if file is not None:
                if not is_public_view:
                    hint = format_guidance_content(
                        "profile", target_player, session, cfg, now, cooldown_remaining
                    )
                    if hint:
                        content_parts.append(hint)
                reply: dict = {"file": file, "ephemeral": False}
                if view is not None:
                    reply["view"] = view
                if content_parts:
                    reply["content"] = "\n\n".join(content_parts)
                await interaction.response.send_message(**reply)
                return

            embed = build_profile_embed(
                target_player,
                session,
                cfg,
                now,
                offline_qi=offline_qi,
                combat=combat,
                realm_display=realm_display(target_player.realm_index, target_player.substage),
                remaining_fn=cooldown_remaining,
                is_public_view=is_public_view,
            )
            if not is_public_view:
                attach_guidance(embed, "profile", target_player, session, cfg, now)
            reply: dict = {"embed": embed, "ephemeral": False}
            if view is not None:
                reply["view"] = view
            if content_parts:
                reply["content"] = "\n".join(content_parts)
            await interaction.response.send_message(**reply)
        finally:
            session.close()

    @app_commands.command(name="achievements", description="View cultivation milestones earned and locked.")
    async def achievements_cmd(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return
            from ...achievements import build_achievements_embed

            embed = build_achievements_embed(session, player)
            attach_guidance(embed, "achievements", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="cooldown", description="See which commands are ready and which are on timer.")
    async def cooldown_cmd(self, interaction: discord.Interaction) -> None:
        cfg = get_config()
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            embed = build_cooldown_embed(player, cfg, now, cooldown_remaining, session=session)
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="remind", description="Get DM pings when cooldowns are ready (opt-in).")
    @app_commands.describe(action="Show status, turn reminders on, or turn them off.", activity="Which timer to manage (required for on/off).")
    @app_commands.choices(action=REMIND_ACTION_CHOICES, activity=REMIND_ACTIVITY_CHOICES)
    async def remind_cmd(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        activity: app_commands.Choice[str] | None = None,
    ) -> None:
        cfg = get_config()
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
            return

        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            if action.value == "status":
                description = build_reminder_status_text(session, player, cfg, now)
                embed = discord.Embed(
                    title="Cooldown reminders",
                    description=description,
                    color=discord.Color.blurple(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=False)
                return

            if activity is None:
                await interaction.response.send_message(
                    "Pick an **activity** when turning reminders on or off (or choose **All timers**).",
                    ephemeral=False,
                )
                return

            if action.value == "on":
                if activity.value == "all":
                    set_all_reminders_enabled(session, player, cfg, True, now)
                    msg = "Reminders **on** for all timers. You'll get a DM when each is ready."
                else:
                    set_reminder_enabled(session, player, cfg, activity.value, True, now)
                    msg = f"Reminders **on** for **{ACTIVITY_LABELS[activity.value]}**."
            else:
                if activity.value == "all":
                    set_all_reminders_enabled(session, player, cfg, False, now)
                    msg = "Reminders **off** for all timers."
                else:
                    set_reminder_enabled(session, player, cfg, activity.value, False, now)
                    msg = f"Reminders **off** for **{ACTIVITY_LABELS[activity.value]}**."

            session.commit()
            embed = discord.Embed(
                title="Cooldown reminders",
                description=msg + "\n\n" + build_reminder_status_text(session, player, cfg, now),
                color=discord.Color.blurple(),
            )
            if player.remind_dms_blocked:
                embed.set_footer(
                    text="Enable DMs from server members in Discord settings to receive pings."
                )
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="help", description="Learn how to play and see all commands.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        cfg = get_config()
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            embed = build_help_embed()
            if player is not None:
                attach_guidance(embed, "help", player, session, cfg, utcnow())
            from ...discord_ui.views.help_view import HelpView

            await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="leaderboard", description="View the realm leaderboard (server only).")
    async def leaderboard_cmd(self, interaction: discord.Interaction) -> None:
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /leaderboard begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            from sqlalchemy import select

            stmt = (
                select(Player)
                .where(Player.guild_id == guild_id)
                .order_by(Player.realm_index.desc(), Player.substage.desc(), Player.qi.desc())
                .limit(10)
            )
            players = list(session.execute(stmt).scalars().all())
            if not players:
                logger.debug("Leaderboard empty guild=%s", guild_id)
                await interaction.response.send_message("No cultivators yet. Someone run `/start`.", ephemeral=False)
                return

            lines = []
            for i, p in enumerate(players, start=1):
                lines.append(f"{i}. {p.dao_name} — {realm_display(p.realm_index, p.substage)} | Qi {p.qi}")

            embed = discord.Embed(title="Realm Leaderboard", description="\n".join(lines), color=discord.Color.teal())
            logger.info("Leaderboard guild=%s top_count=%s", guild_id, len(players))
            player = ensure_player(session, guild_id, get_discord_id(interaction.user))
            attach_guidance(embed, "leaderboard", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="roots", description="Spirit root tier list, early vs late power, and stat bonuses.")
    @app_commands.describe(root="Optional: details for one spirit root.")
    @app_commands.choices(root=ROOT_CHOICES)
    async def roots_cmd(
        self,
        interaction: discord.Interaction,
        root: app_commands.Choice[str] | None = None,
    ) -> None:
        cfg = get_config()
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)

            root_name = root.value if root is not None else None
            embed = build_roots_embed(root_name=root_name)
            if player is not None and root_name is None:
                embed.add_field(
                    name="Your root",
                    value=f"**{player.spirit_root}** — use `/roots root:` for your match-up details.",
                    inline=False,
                )
            if player is not None:
                attach_guidance(embed, "roots", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="areas", description="Compare adventure zones, loot, and realm requirements.")
    @app_commands.describe(area="Optional: details for one area including rare events.")
    @app_commands.autocomplete(area=all_areas_autocomplete)
    async def areas_cmd(
        self,
        interaction: discord.Interaction,
        area: str | None = None,
    ) -> None:
        cfg = get_config()
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)

            from ...command_choices import resolve_area_choice

            area_id = resolve_area_choice(area) if area else None
            embed = build_areas_embed(player, area_id=area_id)
            if player is not None:
                attach_guidance(embed, "areas", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MiscCog())
