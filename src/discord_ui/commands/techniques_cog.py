from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ...combat.loadout import ensure_starter_techniques
from ...combat.technique_ui import _send_skills_hub_message
from ...db import get_session

from ..autocomplete import technique_autocomplete, technique_equip_autocomplete, technique_slot_autocomplete
from ..helpers import get_guild_id, get_discord_id, ensure_player, NOT_STARTED_HINT, attach_guidance, interaction_ctx, realm_display


class TechniquesCog(commands.Cog):
    """Technique commands: techniques hub."""

    @app_commands.command(
        name="techniques",
        description="Combat skills hub — equipped loadout, library, unlock manuals, equip, upgrade.",
    )
    async def techniques_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            ensure_starter_techniques(session, player.id)
            session.commit()

            await interaction.response.defer(thinking=True)
            await _send_skills_hub_message(
                interaction,
                str(interaction.user.id),
                player,
                edit=False,
                use_followup=True,
            )
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TechniquesCog())
