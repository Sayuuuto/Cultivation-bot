from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ...config import get_config
from ...db import get_session
from ...game import utcnow
from ...inventory import build_inventory_embed, get_player_inventory, load_item_catalog
from ...item_info import build_item_detail_embed, resolve_inventory_item_id
from ...shop import build_shop_embed, buy_from_shop, resolve_shop_id
from ..autocomplete import inventory_item_autocomplete, shop_item_autocomplete
from ..helpers import (
    NOT_STARTED_HINT,
    attach_guidance,
    ensure_player,
    get_discord_id,
    get_guild_id,
    interaction_ctx,
    realm_display,
    rng_for,
)

logger = logging.getLogger("cultivation_bot")


class ShopCog(commands.Cog):
    """Shop, inventory, and item inspection commands."""

    @app_commands.command(name="shop", description="Buy pills, gear, and supplies with spirit stones.")
    @app_commands.describe(
        item="Item to buy (leave empty to browse the catalog).",
        quantity="How many to buy (equipment is always 1).",
    )
    @app_commands.autocomplete(item=shop_item_autocomplete)
    async def shop_cmd(
        self,
        interaction: discord.Interaction,
        item: str | None = None,
        quantity: app_commands.Range[int, 1, 99] = 1,
    ):
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

            if item is None:
                embed = build_shop_embed(player)
                attach_guidance(embed, "shop", player, session, cfg, utcnow())
                await interaction.response.send_message(embed=embed, ephemeral=False)
                return

            shop_id = resolve_shop_id(item) or item
            rng = rng_for(guild_id, discord_id)
            ok, message = buy_from_shop(session, player, shop_id, int(quantity), rng=rng)
            if not ok:
                await interaction.response.send_message(message, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(
                title="Purchase complete",
                description=message,
                color=discord.Color.green(),
            )
            attach_guidance(embed, "shop", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="inventory", description="View your storage ring — item names grouped by type.")
    async def inventory_cmd(self, interaction: discord.Interaction):
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /inventory begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            load_item_catalog()
            stacks = get_player_inventory(session, player.id)
            embed = build_inventory_embed(player, stacks, session=session)
            attach_guidance(embed, "inventory", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="item", description="Inspect an item — effects, crafting uses, and how to obtain more.")
    @app_commands.describe(name="Item from your inventory (autocomplete).")
    @app_commands.autocomplete(name=inventory_item_autocomplete)
    async def item_cmd(self, interaction: discord.Interaction, name: str):
        session = get_session()
        try:
            load_item_catalog()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            item_id = resolve_inventory_item_id(session, player.id, name)
            if item_id is None:
                await interaction.response.send_message(
                    "That item is not in your bag. Check **`/inventory`** or pick from autocomplete.",
                    ephemeral=False,
                )
                return

            embed = build_item_detail_embed(item_id, session=session, player_id=player.id)
            if embed is None:
                await interaction.response.send_message("Unknown item.", ephemeral=False)
                return

            cfg = get_config()
            attach_guidance(embed, "item", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog())
