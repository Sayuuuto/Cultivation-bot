from __future__ import annotations

from discord import app_commands
from sqlalchemy import select

from ..config import get_config
from ..db import get_session
from ..models import Player
from ..shop import list_shop_listings
from ..command_choices import (
    TECHNIQUE_SLOT_OPTIONS,
    list_affixable_gear,
    list_affordable_shop_items,
    list_craftable_recipes,
    list_enterable_dungeons,
    list_equippable_gear,
    list_equippable_techniques,
    list_forgeable_slots,
    list_player_manuals,
    list_recipe_options,
    list_recyclable_gear,
    list_technique_equip_options,
    list_valid_slots_for_technique,
    resolve_technique_id,
)
from ..item_info import list_inventory_item_options
from ..technique_info import list_technique_inspect_options
from ..content import get_areas
from .helpers import get_guild_id, get_discord_id, ensure_player

try:
    from ..autocomplete_cache import get_area_autocomplete_options
except ImportError:
    get_area_autocomplete_options = None


def filter_options(options: list[tuple[str, str]], current: str) -> list[tuple[str, str]]:
    if not current:
        return options[:25]
    lower = current.lower()
    return [
        (v, l) for v, l in options
        if lower in l.lower() or lower in v.lower()
    ][:25]


def _choices_from_options(options: list[tuple[str, str]], current: str) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=label[:100], value=value)
        for value, label in filter_options(options, current)
    ]


async def shop_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is not None:
            options = list_affordable_shop_items(player)
        else:
            options = [
                (listing.shop_id, f"{listing.name} ({listing.price} stones)")
                for listing in list_shop_listings()
            ]
        return _choices_from_options(options, current)
    finally:
        session.close()


async def learn_manual_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_player_manuals(session, player.id), current)
    finally:
        session.close()


async def technique_inspect_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_technique_inspect_options(session, player.id), current)
    finally:
        session.close()


async def inventory_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_inventory_item_options(session, player.id), current)
    finally:
        session.close()


async def craft_pill_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_recipe_options(session, player.id, "pill"), current)
    finally:
        session.close()


async def craft_key_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_craftable_recipes(session, player.id, "key"), current)
    finally:
        session.close()


async def area_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    guild_id = get_guild_id(interaction)
    discord_id = get_discord_id(interaction.user)
    options = await get_area_autocomplete_options(guild_id, discord_id)
    if options is None:
        return []
    return _choices_from_options(options, current)


async def all_areas_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    options = [
        (area_id, f"{area.name} ({area.recommended_text})")
        for area_id, area in get_areas().items()
    ]
    return _choices_from_options(options, current)


async def dungeon_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_enterable_dungeons(session, player), current)
    finally:
        session.close()


async def forge_slot_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_forgeable_slots(session, player.id), current)
    finally:
        session.close()


async def affix_gear_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_affixable_gear(session, player.id), current)
    finally:
        session.close()


async def equip_gear_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_equippable_gear(session, player.id), current)
    finally:
        session.close()


async def recycle_gear_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_recyclable_gear(session, player.id), current)
    finally:
        session.close()


async def technique_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_equippable_techniques(session, player), current)
    finally:
        session.close()


async def technique_equip_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        return _choices_from_options(list_technique_equip_options(session, player), current)
    finally:
        session.close()


async def technique_slot_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []

    technique_raw = getattr(interaction.namespace, "technique", None)
    if not technique_raw:
        options = [(slot, f"Slot {slot}" if slot != "passive" else "Passive") for slot in TECHNIQUE_SLOT_OPTIONS]
        return _choices_from_options(options, current)

    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        player = ensure_player(session, guild_id, discord_id)
        if player is None:
            return []
        technique_id = resolve_technique_id(str(technique_raw))
        if technique_id is None:
            return []
        return _choices_from_options(list_valid_slots_for_technique(session, player, technique_id), current)
    finally:
        session.close()
