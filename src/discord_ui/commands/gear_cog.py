from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..autocomplete import (
    forge_slot_autocomplete,
    affix_gear_autocomplete,
    equip_gear_autocomplete,
    recycle_gear_autocomplete,
)
from ..helpers import (
    get_guild_id,
    get_discord_id,
    ensure_player,
    NOT_STARTED_HINT,
    attach_guidance,
    interaction_ctx,
    realm_display,
    rng_for,
    format_seconds,
    schedule_player_reminders,
)
from ...character import get_character_modifiers
from ...command_choices import resolve_forge_choice, resolve_gear_item_id
from ...config import get_config
from ...consumables import use_item
from ...db import get_session
from ...effects import format_active_effects_block
from ...equipment import apply_affix_stone, format_loadout
from ...forge import forge_equipment_for_player
from ...foundation import (
    body_stat_choices,
    body_stat_label,
    meridian_stat_choices,
    meridian_stat_label,
    spend_meridian_point,
    temper_body,
)
from ...game import utcnow
from ...gear_stash import equip_gear_item, recycle_gear_item, unequip_slot
from ...stats import format_gear_summary, format_stats_summary


def _foundation_stat_choices(stat_keys: list[str], label_fn) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=label_fn(k), value=k) for k in stat_keys]


class GearCog(commands.Cog):
    """Equipment and gear commands: forge, temper, meridian, stats, gear, affix, equip, unequip, recycle, loadout, use."""

    @app_commands.command(name="forge", description="Forge equipment for a slot using adventure materials.")
    @app_commands.describe(slot="Pick a slot you can forge right now.")
    @app_commands.autocomplete(slot=forge_slot_autocomplete)
    async def forge_cmd(self, interaction: discord.Interaction, slot: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            resolved_slot = resolve_forge_choice(slot)
            if resolved_slot is None:
                await interaction.response.send_message(
                    "Pick a forge option from the **`/forge`** list — you need the listed materials.",
                    ephemeral=False,
                )
                return

            slot_name, grade = resolved_slot
            rng = rng_for(guild_id, discord_id)
            res = forge_equipment_for_player(session, player, slot_name, grade=grade, rng=rng)
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(title="Forging Complete", description=res.message, color=discord.Color.gold())
            attach_guidance(embed, "forge", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(
        name="temper",
        description="Temper your body — spend hunt materials or an essence charge for permanent stats.",
    )
    @app_commands.describe(
        stat="Which aspect of your flesh to harden.",
        use_charge="Spend a free essence charge instead of materials (from daily or breakthrough).",
    )
    @app_commands.choices(stat=_foundation_stat_choices(body_stat_choices(), body_stat_label))
    async def temper_cmd(
        self,
        interaction: discord.Interaction,
        stat: str,
        use_charge: bool = False,
    ):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            res = temper_body(
                player,
                stat,
                session=session,
                player_id=player.id,
                use_charge=use_charge,
            )
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=False)
                return

            session.add(player)
            session.commit()
            embed = discord.Embed(title="Body Tempering", description=res.message, color=discord.Color.dark_red())
            attach_guidance(embed, "temper", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(
        name="meridian",
        description="Open meridian channels — spend meridian points for permanent internal growth.",
    )
    @app_commands.describe(stat="Which channel to reinforce.")
    @app_commands.choices(stat=_foundation_stat_choices(meridian_stat_choices(), meridian_stat_label))
    async def meridian_cmd(self, interaction: discord.Interaction, stat: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            res = spend_meridian_point(player, stat)
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=False)
                return

            session.add(player)
            session.commit()
            embed = discord.Embed(title="Meridian Cultivation", description=res.message, color=discord.Color.teal())
            attach_guidance(embed, "meridian", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="stats", description="View combat stats, gear contribution, and realm baseline.")
    async def stats_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            mod = get_character_modifiers(session, player)
            summary = format_stats_summary(session, player.id, player=player, mod=mod)
            embed = discord.Embed(
                title=f"{player.dao_name} — Stats",
                description=summary,
                color=discord.Color.dark_purple(),
            )
            embed.add_field(name="Adventure Success", value=f"+{mod.adventure_success:.2f}", inline=True)
            embed.add_field(name="Adventure Defense", value=f"+{mod.adventure_defense:.2f}", inline=True)
            embed.add_field(name="Drop Luck", value=f"+{mod.drop_luck:.2f}", inline=True)
            embed.add_field(name="Rare Events", value=f"×{mod.rare_event_mult:.2f}", inline=True)
            embed.add_field(name="PvP Power", value=f"+{mod.pvp_power:.2f}", inline=True)
            embed.add_field(name="Dungeon Damage", value=f"+{mod.dungeon_damage:.2f}", inline=True)
            attach_guidance(embed, "stats", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="gear", description="View forged gear and equipment stat totals.")
    async def gear_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            embed = discord.Embed(
                title=f"{player.dao_name} — Gear",
                description=format_gear_summary(session, player.id),
                color=discord.Color.dark_teal(),
            )
            attach_guidance(embed, "stats", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="affix", description="Spend an Affix Stone on forged gear for a random bonus.")
    @app_commands.describe(gear="Pick gear from your stash or worn slots.")
    @app_commands.autocomplete(gear=affix_gear_autocomplete)
    async def affix_cmd(self, interaction: discord.Interaction, gear: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            gear_item_id = resolve_gear_item_id(gear)
            if gear_item_id is None:
                await interaction.response.send_message(
                    "Pick gear from the **`/affix`** list.",
                    ephemeral=False,
                )
                return

            rng = rng_for(guild_id, discord_id)
            ok, message, _affix = apply_affix_stone(session, player.id, gear_item_id, rng=rng)
            if not ok:
                await interaction.response.send_message(message, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(title="Affix Imprinted", description=message, color=discord.Color.blue())
            attach_guidance(embed, "affix", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="equip", description="Wear forged gear from your stash.")
    @app_commands.describe(gear="Pick a piece from your gear stash.")
    @app_commands.autocomplete(gear=equip_gear_autocomplete)
    async def equip_gear_cmd(self, interaction: discord.Interaction, gear: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            gear_item_id = resolve_gear_item_id(gear)
            if gear_item_id is None:
                await interaction.response.send_message(
                    "Pick gear from the **`/equip`** list.",
                    ephemeral=False,
                )
                return

            res = equip_gear_item(session, player.id, gear_item_id)
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(title="Gear Equipped", description=res.message, color=discord.Color.green())
            attach_guidance(embed, "equip", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="recycle", description="Break down stash gear into spirit stones.")
    @app_commands.describe(gear="Pick a stash piece to recycle.")
    @app_commands.autocomplete(gear=recycle_gear_autocomplete)
    async def recycle_cmd(self, interaction: discord.Interaction, gear: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            gear_item_id = resolve_gear_item_id(gear)
            if gear_item_id is None:
                await interaction.response.send_message(
                    "Pick gear from the **`/recycle`** list.",
                    ephemeral=False,
                )
                return

            res = recycle_gear_item(session, player, gear_item_id)
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(title="Gear Recycled", description=res.message, color=discord.Color.dark_gold())
            attach_guidance(embed, "recycle", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="unequip", description="Stow worn gear back into your stash.")
    @app_commands.describe(slot="Which slot to clear.")
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Weapon", value="weapon"),
            app_commands.Choice(name="Armor", value="armor"),
            app_commands.Choice(name="Accessory", value="accessory"),
            app_commands.Choice(name="Talisman", value="talisman"),
        ]
    )
    async def unequip_cmd(self, interaction: discord.Interaction, slot: app_commands.Choice[str]):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            res = unequip_slot(session, player.id, slot.value)
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(title="Gear Stowed", description=res.message, color=discord.Color.light_grey())
            attach_guidance(embed, "unequip", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="loadout", description="View equipment affixes and derived modifiers.")
    async def loadout_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            mod = get_character_modifiers(session, player)
            equipment_text = format_loadout(session, player.id)

            embed = discord.Embed(title=f"{player.dao_name} — Loadout", description=equipment_text, color=discord.Color.dark_blue())
            embed.add_field(name="Cultivate Qi", value=f"×{mod.cultivate_qi_mult:.2f}", inline=True)
            embed.add_field(name="Breakthrough Stability", value=f"+{mod.breakthrough_stability:.2f}", inline=True)
            embed.add_field(name="Adventure Success", value=f"+{mod.adventure_success:.2f}", inline=True)
            embed.add_field(name="Drop Luck", value=f"+{mod.drop_luck:.2f}", inline=True)
            embed.add_field(name="Dungeon Damage", value=f"+{mod.dungeon_damage:.2f}", inline=True)
            embed.add_field(name="PvP Power", value=f"+{mod.pvp_power:.2f}", inline=True)
            effects_block = format_active_effects_block(session, player.id)
            if effects_block:
                embed.add_field(name="Lingering effects", value=effects_block, inline=False)
            attach_guidance(embed, "loadout", player, session, get_config(), utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="use", description="Consume a pill or special item from your inventory.")
    @app_commands.describe(item="Pick from the list, or type a pill name (e.g. Qi Gathering Pill).")
    async def use_cmd(self, interaction: discord.Interaction, item: str):
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            await interaction.response.defer(ephemeral=False)

            rng = rng_for(guild_id, discord_id)
            ok, message = use_item(session, player, item, rng=rng)
            if not ok:
                await interaction.followup.send(message, ephemeral=False)
                return

            session.add(player)
            session.commit()
            embed = discord.Embed(title="Item Used", description=message, color=discord.Color.green())
            effects_block = format_active_effects_block(session, player.id)
            if effects_block:
                embed.add_field(name="Still active on you", value=effects_block, inline=False)
            attach_guidance(embed, "use", player, session, get_config(), utcnow())
            await interaction.followup.send(embed=embed, ephemeral=False)
        finally:
            session.close()


@GearCog.use_cmd.autocomplete("item")
async def use_item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    from ...consumables import list_usable_inventory
    from ...inventory import get_item_name

    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []

        current_lower = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for item_id, qty in list_usable_inventory(session, player.id):
            name = get_item_name(item_id)
            if current_lower and current_lower not in name.lower() and current_lower not in item_id:
                continue
            label = f"{name} (×{qty})"
            choices.append(app_commands.Choice(name=label[:100], value=item_id))
            if len(choices) >= 25:
                break

        return choices
    finally:
        session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GearCog())
