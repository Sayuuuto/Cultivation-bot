from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ...adventure import (
    DEFAULT_STANCE,
    abandon_adventure,
    get_active_adventure,
    resume_adventure_session,
    start_adventure_session,
)
from ...command_choices import (
    adventure_area_for_player,
    resolve_area_choice,
)
from ...combat.discord_ui import build_adventure_combat_embed, build_hunt_combat_embed
from ...combat.loadout import ensure_starter_techniques, get_equipped_active_techniques
from ...combat.session import COMBAT_BUSY_MESSAGE
from ...config import get_config
from ...cooldown_haste import consume_haste_for_activity, get_haste_reduction_seconds
from ...db import get_session
from ...game import utcnow
from ...gather import run_gather
from ...hunt import start_hunt_combat
from ...ui.embeds import build_adventure_embed_from_pending

from ..autocomplete import area_autocomplete
from ..helpers import (
    NOT_STARTED_HINT,
    activity_cooldown_remaining,
    attach_guidance,
    ensure_player,
    format_seconds,
    get_discord_id,
    get_guild_id,
    rng_for,
    schedule_player_reminders,
)
from ..views import AbandonStuckCombatView, AdventureChoiceView, CombatView


class CombatCog(commands.Cog):
    """Combat-related commands: hunt, adventure, gather."""

    @app_commands.command(name="gather", description="Harvest herbs and ore from a region (5 min cooldown).")
    @app_commands.describe(area="Where to gather materials.")
    @app_commands.autocomplete(area=area_autocomplete)
    async def gather_cmd(self, interaction: discord.Interaction, area: str):
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
            remaining = activity_cooldown_remaining(
                session,
                player,
                now,
                "gather",
                player.last_gather_at,
                cfg.gather_cooldown_seconds,
            )
            if remaining > 0:
                haste = get_haste_reduction_seconds(session, player.id, "gather")
                extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
                await interaction.response.send_message(
                    f"The soil needs time to recover. Wait {format_seconds(remaining)}.{extra}",
                    ephemeral=False,
                )
                return

            area_id = resolve_area_choice(area)
            if area_id is None:
                await interaction.response.send_message("That region is unknown.", ephemeral=False)
                return

            rng = rng_for(guild_id, discord_id)
            res = run_gather(session, player, area_id, rng=rng)
            if not res.success:
                await interaction.response.send_message(res.messages[0], ephemeral=False)
                return

            player.last_gather_at = now
            player.last_active_at = now
            consume_haste_for_activity(session, player.id, "gather")
            schedule_player_reminders(session, player, cfg, "gather", now=now)
            session.add(player)
            session.commit()

            from ...inventory import get_item_name

            drop_lines = [f"**{get_item_name(item_id)}** ×{qty}" for item_id, qty in res.drops.items()]
            embed = discord.Embed(
                title=f"Gather — {res.area_name}",
                description="\n".join(res.messages),
                color=discord.Color.green(),
            )
            if drop_lines:
                embed.add_field(name="Collected", value="\n".join(drop_lines), inline=False)
            attach_guidance(embed, "gather", player, session, cfg, now)
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="hunt", description="Track and fight spirit beasts for cores and parts (5 min cooldown).")
    @app_commands.describe(area="Where to hunt beasts.")
    @app_commands.autocomplete(area=area_autocomplete)
    async def hunt_cmd(self, interaction: discord.Interaction, area: str):
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
            remaining = activity_cooldown_remaining(
                session,
                player,
                now,
                "hunt",
                player.last_hunt_at,
                cfg.hunt_cooldown_seconds,
            )
            if remaining > 0:
                haste = get_haste_reduction_seconds(session, player.id, "hunt")
                extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
                await interaction.response.send_message(
                    f"You must recover before hunting again. Wait {format_seconds(remaining)}.{extra}",
                    ephemeral=False,
                )
                return

            area_id = resolve_area_choice(area)
            if area_id is None:
                await interaction.response.send_message("That region is unknown.", ephemeral=False)
                return

            rng = rng_for(guild_id, discord_id)
            start, err = start_hunt_combat(session, player, area_id, rng=rng)
            if err:
                if err == COMBAT_BUSY_MESSAGE:
                    await interaction.response.send_message(
                        err,
                        view=AbandonStuckCombatView(discord_id, guild_id),
                        ephemeral=False,
                    )
                else:
                    await interaction.response.send_message(err, ephemeral=False)
                return

            assert start is not None
            ensure_starter_techniques(session, player.id)
            techniques = get_equipped_active_techniques(session, player.id)
            session.commit()

            embed = build_hunt_combat_embed(start)
            view = CombatView(
                discord_id,
                guild_id,
                start.combat_id,
                "hunt",
                area_id=start.area_id,
                beast_id=start.beast_id,
                area_name=start.area_name,
                techniques=techniques,
                technique_cooldowns={},
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="adventure", description="Start an interactive adventure with choices.")
    async def adventure_cmd(self, interaction: discord.Interaction):
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
            from ...novice_trial import heal_premature_trial_adventure, heal_stuck_novice_adventure, requires_sage_trial
            from ...story_mode import check_elder_trial_command, elder_trial_active

            if heal_stuck_novice_adventure(player):
                session.add(player)
            if heal_premature_trial_adventure(session, player):
                session.add(player)
                session.commit()

            if elder_trial_active(player):
                active = get_active_adventure(session, player.id)
                gate_msg = check_elder_trial_command(player, "adventure")
                if gate_msg and active is None:
                    await interaction.response.send_message(gate_msg, ephemeral=False)
                    return

            remaining = activity_cooldown_remaining(
                session,
                player,
                now,
                "adventure",
                player.last_adventure_at,
                cfg.adventure_cooldown_seconds,
            )
            if remaining > 0:
                haste = get_haste_reduction_seconds(session, player.id, "adventure")
                extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
                await interaction.response.send_message(
                    f"You need to recover before another adventure. Wait {format_seconds(remaining)}.{extra}",
                    ephemeral=False,
                )
                return

            area_id = adventure_area_for_player(player)
            rng = rng_for(guild_id, discord_id)
            pending, err = start_adventure_session(session, player, area_id, DEFAULT_STANCE, rng=rng)
            if err:
                await interaction.response.send_message(err, ephemeral=False)
                return

            assert pending is not None

            if requires_sage_trial(player) and pending.prompt and "Sage of the Bamboo Path" not in pending.prompt:
                abandon_adventure(session, player.id)
                session.commit()
                await interaction.response.send_message(
                    "Your first journey must begin with the **Sage of the Bamboo Path**. "
                    "Run **`/adventure`** again when your path is clear.",
                    ephemeral=False,
                )
                return

            session.commit()

            if pending.encounter_type == "combat" and pending.combat_id:
                from ...combat.session import get_active_combat, load_combat_state

                ensure_starter_techniques(session, player.id)
                techniques = get_equipped_active_techniques(session, player.id)
                active_combat = get_active_combat(session, player.id)
                combat_state = load_combat_state(active_combat) if active_combat else None
                embed = (
                    build_adventure_combat_embed(pending, combat_state)
                    if combat_state
                    else build_adventure_embed_from_pending(pending)
                )
                view = CombatView(
                    discord_id,
                    guild_id,
                    pending.combat_id,
                    "adventure",
                    area_id=area_id,
                    active_id=pending.active_id,
                    area_name=pending.area_name,
                    techniques=techniques,
                    technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                    player_sealed=combat_state.player.sealed if combat_state else False,
                )
            else:
                embed = build_adventure_embed_from_pending(pending)
                view = AdventureChoiceView(discord_id, guild_id, pending.active_id, pending.choices)
            attach_guidance(embed, "adventure", player, session, cfg, now)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="adventure-continue", description="Resume a paused adventure and pick up where you left off.")
    async def adventure_continue_cmd(self, interaction: discord.Interaction):
        cfg = get_config()
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            from ...novice_trial import heal_premature_trial_adventure, heal_stuck_novice_adventure, requires_sage_trial
            from ...story_mode import check_elder_trial_command, elder_trial_active

            if heal_stuck_novice_adventure(player):
                session.add(player)
            if heal_premature_trial_adventure(session, player):
                session.add(player)
                session.commit()

            if elder_trial_active(player):
                gate_msg = check_elder_trial_command(player, "adventure")
                if gate_msg:
                    await interaction.response.send_message(gate_msg, ephemeral=False)
                    return

            pending, err = resume_adventure_session(session, player)
            if err:
                await interaction.response.send_message(err, ephemeral=False)
                return

            assert pending is not None
            if requires_sage_trial(player) and pending.prompt and "Sage of the Bamboo Path" not in pending.prompt:
                abandon_adventure(session, player.id)
                session.commit()
                await interaction.response.send_message(
                    "This journey is not the sage's trial. Use **`/adventure-abandon`**, then "
                    "**`/adventure`** to meet the **Sage of the Bamboo Path**.",
                    ephemeral=False,
                )
                return

            if pending.encounter_type == "combat" and pending.combat_id:
                from ...combat.session import get_active_combat, load_combat_state

                ensure_starter_techniques(session, player.id)
                techniques = get_equipped_active_techniques(session, player.id)
                active_row = get_active_adventure(session, player.id)
                area_id = active_row.area_id if active_row else ""
                active_combat = get_active_combat(session, player.id)
                combat_state = load_combat_state(active_combat) if active_combat else None
                embed = (
                    build_adventure_combat_embed(pending, combat_state)
                    if combat_state
                    else build_adventure_embed_from_pending(pending)
                )
                view = CombatView(
                    discord_id,
                    guild_id,
                    pending.combat_id,
                    "adventure",
                    area_id=area_id,
                    active_id=pending.active_id,
                    area_name=pending.area_name,
                    techniques=techniques,
                    technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                    player_sealed=combat_state.player.sealed if combat_state else False,
                )
            else:
                embed = build_adventure_embed_from_pending(pending)
                view = AdventureChoiceView(discord_id, guild_id, pending.active_id, pending.choices)
            attach_guidance(embed, "adventure", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="adventure-abandon", description="Withdraw from your current adventure without rewards.")
    async def adventure_abandon_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            ok, message = abandon_adventure(session, player.id)
            if not ok:
                await interaction.response.send_message(message, ephemeral=False)
                return

            session.commit()
            await interaction.response.send_message(message, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CombatCog())
