from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..helpers import (
    NOT_STARTED_HINT,
    attach_guidance,
    ensure_player,
    get_discord_id,
    interaction_ctx,
    rng_for,
)
from ..views.explore_view import ExploreView
from ...config import get_config
from ...db import get_session
from ...explore import (
    _deserialize_state,
    _load_encounter_sequence,
    build_explore_result_embed,
    build_explore_step_embed,
    calculate_affinity,
    check_explore_cooldown,
    finalize_explore,
    get_active_explore,
    load_explore_areas,
    start_explore,
)
from ...game import utcnow
from ...models import ExploreSession

logger = logging.getLogger("cultivation_bot")


async def explore_area_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    areas = load_explore_areas()
    choices: list[app_commands.Choice[str]] = []
    lower = current.lower()
    for aid, area in areas.items():
        if lower in aid.lower() or lower in area.name.lower():
            choices.append(
                app_commands.Choice(name=f"{area.name} ({aid})", value=aid)
            )
            if len(choices) >= 25:
                break
    if not choices:
        for aid, area in areas.items():
            choices.append(
                app_commands.Choice(name=f"{area.name} ({aid})", value=aid)
            )
            if len(choices) >= 25:
                break
    return choices


class ExploreCog(commands.Cog):

    @app_commands.command(
        name="explore",
        description="Embark on an expedition into dangerous lands for rare rewards.",
    )
    @app_commands.describe(
        area="Which area to explore (or leave blank to resume an active expedition).",
    )
    @app_commands.autocomplete(area=explore_area_autocomplete)
    async def explore_cmd(
        self,
        interaction: discord.Interaction,
        area: str | None = None,
    ) -> None:
        cfg = get_config()
        session = get_session()
        try:
            guild_id = interaction_ctx(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()

            # Check for existing active expedition
            active = get_active_explore(session, guild_id, discord_id)
            if active is not None:
                areas = load_explore_areas()
                area_obj = areas.get(active.area_id)
                area_name = area_obj.name if area_obj else active.area_id
                state_raw = json.loads(active.state_json) if isinstance(active.state_json, str) else {}
                current_hp = state_raw.get("current_hp", 0)
                max_hp = state_raw.get("max_hp", 1)
                current_step = active.current_step

                seq_state = _deserialize_state(active)
                sequence = _load_encounter_sequence(active, seq_state)
                if current_step < len(sequence):
                    enc = sequence[current_step]
                else:
                    enc = None

                affinity = calculate_affinity(player, area_obj) if area_obj else 1.0
                if enc:
                    embed = build_explore_step_embed(
                        area_name,
                        current_step + 1,
                        active.total_steps,
                        enc.title,
                        enc.text,
                        current_hp,
                        max_hp,
                        affinity=affinity,
                        danger=area_obj.danger if area_obj else 1.0,
                    )
                else:
                    embed = discord.Embed(
                        title=f"Expedition: {area_name}",
                        description="Your expedition continues.",
                        color=discord.Color.dark_green(),
                    )
                view = ExploreView(
                    discord_id, guild_id, active.id,
                    enc.choices if enc else [],
                    area_name, current_step + 1, active.total_steps,
                )
                attach_guidance(embed, "explore", player, session, cfg, now)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
                return

            # If no area provided, show cooldown/area picker
            if area is None:
                on_cooldown, msg = check_explore_cooldown(player)
                if on_cooldown:
                    areas = load_explore_areas()
                    area_options = "\n".join(
                        f"• `{aid}` — {a.name}"
                        for aid, a in list(areas.items())[:10]
                    )
                    embed = discord.Embed(
                        title="⏳ Expedition Cooldown",
                        description=msg,
                        color=discord.Color.dark_grey(),
                    )
                    embed.add_field(
                        name="Available areas",
                        value=area_options,
                        inline=False,
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=False)
                    return

                embed = discord.Embed(
                    title="🌲 Explore",
                    description="Choose an area to explore using the autocomplete:\n"
                                "`/explore area:<name>`\n\n"
                                "Each area offers different dangers and rewards. "
                                "Your spirit root and path affinity affect your success.",
                    color=discord.Color.dark_green(),
                )
                areas = load_explore_areas()
                area_lines = "\n".join(
                    f"• `{aid}` — {a.name} (danger: {a.danger:.2f})"
                    for aid, a in list(areas.items())[:10]
                )
                embed.add_field(name="Expedition areas", value=area_lines, inline=False)
                await interaction.response.send_message(embed=embed, ephemeral=False)
                return

            # Start a new expedition
            row, err = start_explore(session, player, guild_id, area)
            if err:
                await interaction.response.send_message(err, ephemeral=False)
                return
            if row is None:
                await interaction.response.send_message("Failed to start expedition.", ephemeral=False)
                return

            areas = load_explore_areas()
            area_obj = areas.get(area)
            state_raw = json.loads(row.state_json) if isinstance(row.state_json, str) else {}
            current_hp = state_raw.get("current_hp", 0)
            max_hp = state_raw.get("max_hp", 1)
            affinity = calculate_affinity(player, area_obj) if area_obj else 1.0

            seq_state = _deserialize_state(row)
            sequence = _load_encounter_sequence(row, seq_state)
            first_enc = sequence[0] if sequence else None

            name = area_obj.name if area_obj else area
            embed = build_explore_step_embed(
                name,
                1,
                row.total_steps,
                first_enc.title if first_enc else "Expedition Begins",
                first_enc.text if first_enc else "You step into the unknown.",
                current_hp,
                max_hp,
                affinity=affinity,
                danger=area_obj.danger if area_obj else 1.0,
            )
            attach_guidance(embed, "explore", player, session, cfg, now)
            view = ExploreView(
                discord_id, guild_id, row.id,
                first_enc.choices if first_enc else [],
                name, 1, row.total_steps,
            )
            session.commit()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExploreCog())
