from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..autocomplete import craft_key_autocomplete, craft_pill_autocomplete
from ..helpers import (
    attach_guidance,
    ensure_player,
    format_seconds,
    get_discord_id,
    get_guild_id,
    interaction_ctx,
    NOT_STARTED_HINT,
    realm_display,
    rng_for,
)
from ...command_choices import can_bind_technique_manual
from ...config import get_config
from ...content import get_recipes
from ...crafting import craft_recipe
from ...db import get_session
from ...manuals import craft_manual_from_fragments
from ...recipes_info import build_recipes_embed
from ...game import utcnow

logger = logging.getLogger("cultivation_bot")


class CraftCog(commands.Cog):
    """Crafting commands: recipes, craft pill/key/manual."""

    @app_commands.command(name="recipes", description="View craft recipes, pill effects, and forge costs.")
    @app_commands.describe(category="Filter by pills, keys, or forging.")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Pills", value="pill"),
            app_commands.Choice(name="Keys", value="key"),
            app_commands.Choice(name="Forging", value="forge"),
        ]
    )
    async def recipes_cmd(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str] | None = None,
    ):
        cfg = get_config()
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)

            cat = category.value if category is not None else None
            recipe_type = None if cat in (None, "all") else cat
            embed = build_recipes_embed(recipe_type=recipe_type, realm_index=player.realm_index)
            if player is not None:
                attach_guidance(embed, "recipes", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    craft_group = app_commands.Group(name="craft", description="Craft pills, keys, and technique manuals.")

    @craft_group.command(name="pill", description="Craft a pill from materials.")
    @app_commands.describe(recipe="Pill recipe to attempt.", amount="How many to attempt (1-10).")
    @app_commands.autocomplete(recipe=craft_pill_autocomplete)
    async def craft_pill_cmd(
        self,
        interaction: discord.Interaction,
        recipe: str,
        amount: app_commands.Range[int, 1, 10] = 1,
    ):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if get_recipes().get(recipe) is None:
                await interaction.response.send_message(
                    "Pick a pill recipe from the **`/craft pill`** list.",
                    ephemeral=False,
                )
                return

            rng = rng_for(guild_id, discord_id)
            res = craft_recipe(session, player, recipe, amount=amount, rng=rng)
            session.add(player)
            session.commit()

            if res.success:
                color = discord.Color.green()
                title = "Alchemy"
            elif "don't have enough materials" in res.message.lower():
                color = discord.Color.red()
                title = "Materials Short"
            else:
                color = discord.Color.orange()
                title = "Alchemy"
            embed = discord.Embed(title=title, description=res.message, color=color)
            if res.crafted:
                from ...inventory import get_item_name

                lines = [f"{get_item_name(k)} ×{v}" for k, v in res.crafted.items()]
                embed.add_field(name="Crafted", value="\n".join(lines), inline=False)
            attach_guidance(embed, "craft_pill", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @craft_group.command(name="key", description="Craft a dungeon key.")
    @app_commands.describe(recipe="Pick a key recipe you can forge now.")
    @app_commands.autocomplete(recipe=craft_key_autocomplete)
    async def craft_key_cmd(self, interaction: discord.Interaction, recipe: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if get_recipes().get(recipe) is None:
                await interaction.response.send_message(
                    "Pick a key recipe from the **`/craft key`** list.",
                    ephemeral=False,
                )
                return

            rng = rng_for(guild_id, discord_id)
            res = craft_recipe(session, player, recipe, amount=1, rng=rng)
            session.add(player)
            session.commit()

            color = discord.Color.green() if res.success else discord.Color.red()
            embed = discord.Embed(title="Key Forging", description=res.message, color=color)
            attach_guidance(embed, "craft_key", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @craft_group.command(name="manual", description="Bind technique fragments into a manual.")
    async def craft_manual_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if not can_bind_technique_manual(session, player.id):
                from ...drop_sources import format_missing_materials_message
                from ...manuals import MANUAL_CRAFT_INPUTS

                await interaction.response.send_message(
                    format_missing_materials_message(session, player.id, MANUAL_CRAFT_INPUTS, action="manual"),
                    ephemeral=False,
                )
                return

            rng = rng_for(guild_id, discord_id)
            res = craft_manual_from_fragments(session, player, rng=rng)
            session.add(player)
            session.commit()

            color = discord.Color.green() if res.success else discord.Color.orange()
            embed = discord.Embed(title="Manual Binding", description=res.message, color=color)
            if res.crafted:
                from ...inventory import get_item_name

                lines = [f"{get_item_name(k)} ×{v}" for k, v in res.crafted.items()]
                embed.add_field(name="Bound", value="\n".join(lines), inline=False)
            attach_guidance(embed, "craft_manual", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(CraftCog())
