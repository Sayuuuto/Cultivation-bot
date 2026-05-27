from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import discord

from .guidance import get_abode_welcome_intro
from .realms import get_realm_name, get_realm_names

logger = logging.getLogger(__name__)

ABODE_PREFIX = "abode-"
ABODE_CATEGORY_NAMES = ("abodes", "cultivation abodes")


def abode_channel_slug(dao_name: str, *, max_len: int = 100) -> str:
    """Build a Discord-safe channel name: abode-{sanitized-dao-name}."""
    slug = dao_name.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        slug = "cultivator"
    max_slug = max(0, max_len - len(ABODE_PREFIX))
    return ABODE_PREFIX + slug[:max_slug]


def realm_role_name(realm_index: int) -> str:
    return get_realm_name(realm_index)


@dataclass(frozen=True)
class AbodeProvisionResult:
    channel: discord.TextChannel | None
    channel_error: str | None
    role: discord.Role | None
    role_error: str | None


def _abode_overwrites(
    guild: discord.Guild,
    member: discord.Member,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            use_application_commands=True,
        ),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
        )
    return overwrites


async def resolve_abode_category(
    guild: discord.Guild,
    category_id: str | None,
) -> discord.CategoryChannel | None:
    if category_id:
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
        logger.warning(
            "ABODE_CATEGORY_ID=%s is not a category in guild=%s",
            category_id,
            guild.id,
        )

    for category in guild.categories:
        if category.name.lower() in ABODE_CATEGORY_NAMES:
            return category

    try:
        return await guild.create_category("Abodes", reason="Private cultivation abodes")
    except discord.Forbidden:
        logger.warning("Cannot create Abodes category in guild=%s (missing Manage Channels)", guild.id)
        return None
    except discord.HTTPException:
        logger.exception("Failed to create Abodes category in guild=%s", guild.id)
        return None


def _unique_abode_name(guild: discord.Guild, base_name: str) -> str:
    if discord.utils.get(guild.text_channels, name=base_name) is None:
        return base_name
    stem = base_name
    if len(stem) > 92:
        stem = stem[:92].rstrip("-")
    suffix = 2
    while True:
        candidate = f"{stem}-{suffix}"
        if discord.utils.get(guild.text_channels, name=candidate) is None:
            return candidate[:100]
        suffix += 1


async def _post_abode_welcome(
    channel: discord.TextChannel,
    member: discord.Member,
    dao_name: str,
) -> None:
    embed = discord.Embed(
        title="Welcome to Your Abode",
        description=get_abode_welcome_intro(dao_name),
        color=discord.Color.blurple(),
    )
    try:
        await channel.send(content=member.mention, embed=embed)
    except discord.HTTPException:
        logger.exception(
            "Abode welcome message failed guild=%s channel=%s user=%s",
            channel.guild.id,
            channel.id,
            member.id,
        )


async def create_abode_channel(
    guild: discord.Guild,
    member: discord.Member,
    dao_name: str,
    *,
    category_id: str | None = None,
) -> tuple[discord.TextChannel | None, str | None]:
    channel_name = _unique_abode_name(guild, abode_channel_slug(dao_name))
    category = await resolve_abode_category(guild, category_id)
    overwrites = _abode_overwrites(guild, member)
    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Private abode for {dao_name}",
            topic=f"{dao_name}'s cultivation abode — cultivate and venture here.",
        )
        await _post_abode_welcome(channel, member, dao_name)
        return channel, None
    except discord.Forbidden:
        return None, (
            "Your abode could not be opened — the bot needs **Manage Channels** "
            "and permission to create private channels."
        )
    except discord.HTTPException as exc:
        logger.exception("Abode channel creation failed guild=%s user=%s", guild.id, member.id)
        return None, f"Your abode could not be opened: {exc.text}"


async def ensure_realm_roles(guild: discord.Guild) -> dict[str, discord.Role]:
    """Ensure one Discord role exists per cultivation realm name."""
    by_name = {role.name: role for role in guild.roles}
    result: dict[str, discord.Role] = {}
    for name in get_realm_names():
        role = by_name.get(name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=name,
                    mentionable=False,
                    reason="Cultivation realm rank",
                )
            except discord.Forbidden:
                logger.warning("Cannot create realm role %r in guild=%s", name, guild.id)
                continue
            except discord.HTTPException:
                logger.exception("Failed to create realm role %r in guild=%s", name, guild.id)
                continue
        result[name] = role
    return result


async def sync_member_realm_role(
    guild: discord.Guild,
    member: discord.Member,
    realm_index: int,
) -> tuple[discord.Role | None, str | None]:
    roles_by_name = await ensure_realm_roles(guild)
    target_name = realm_role_name(realm_index)
    target_role = roles_by_name.get(target_name)
    if target_role is None:
        return None, f"The **{target_name}** rank could not be assigned on this server."

    realm_roles = set(roles_by_name.values())
    to_remove = [role for role in member.roles if role in realm_roles and role != target_role]
    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason="Realm advancement")
        if target_role not in member.roles:
            await member.add_roles(target_role, reason="Realm assignment")
        return target_role, None
    except discord.Forbidden:
        return None, (
            "Your realm rank could not be updated — place the bot's role above realm ranks "
            "and grant **Manage Roles**."
        )
    except discord.HTTPException as exc:
        logger.exception(
            "Realm role sync failed guild=%s user=%s realm=%s",
            guild.id,
            member.id,
            target_name,
        )
        return None, f"Your realm rank could not be updated: {exc.text}"


async def provision_new_cultivator(
    guild: discord.Guild,
    member: discord.Member,
    dao_name: str,
    realm_index: int,
    *,
    abode_category_id: str | None = None,
) -> AbodeProvisionResult:
    channel, channel_error = await create_abode_channel(
        guild,
        member,
        dao_name,
        category_id=abode_category_id,
    )
    role, role_error = await sync_member_realm_role(guild, member, realm_index)
    return AbodeProvisionResult(
        channel=channel,
        channel_error=channel_error,
        role=role,
        role_error=role_error,
    )
