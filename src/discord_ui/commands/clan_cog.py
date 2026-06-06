from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ...config import get_config
from ...db import get_session
from ...game import utcnow
from ...models import Clan
from ...clans import (
    can_join_clan,
    consume_clan_invitation,
    create_clan_invitation,
    get_clan_top_contributors,
    list_clan_invitations_for_player,
    set_clan_invite_only,
)
from ..helpers import (
    NOT_STARTED_HINT,
    attach_guidance,
    ensure_player,
    get_clan_by_name_lookup,
    get_discord_id,
    get_guild_id,
    interaction_ctx,
    realm_display,
    rng_for,
)
from ..views.confirm_view import ConfirmActionView

logger = logging.getLogger("cultivation_bot")


class ClanCog(commands.Cog):
    """Player clan commands."""

    @app_commands.command(name="clan-create", description="Create a player clan in this server.")
    async def clan_create_cmd(self, interaction: discord.Interaction, name: str):
        session = get_session()
        try:
            logger.info("CMD /clan-create begin %s name=%r", interaction_ctx(interaction), name)
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.clan_id is not None:
                await interaction.response.send_message(
                    "You are already in a clan. Use `/clan-leave` first.", ephemeral=False
                )
                return

            name = name.strip()
            if not name:
                await interaction.response.send_message("Clan name cannot be empty.", ephemeral=False)
                return

            existing = get_clan_by_name_lookup(session, guild_id, name)
            if existing is not None:
                await interaction.response.send_message("A clan with that name already exists.", ephemeral=False)
                return

            clan = Clan(
                guild_id=guild_id,
                name=name,
                created_by_discord_id=discord_id,
                clan_qi_contributed=0,
                member_count=1,
            )
            session.add(clan)
            session.flush()

            player.clan_id = clan.id
            player.clan_role = "founder"
            player.clan_contribution_qi_total = 0
            session.add(player)
            session.commit()
            logger.info(
                "Clan created guild=%s founder=%s clan_id=%s clan_name=%r member_count=%s",
                guild_id,
                discord_id,
                clan.id,
                name,
                clan.member_count,
            )

            embed = discord.Embed(
                title="New Clan Formed",
                description=f"The clan `{name}` opens its banner. You become its founder.",
                color=discord.Color.dark_gold(),
            )
            attach_guidance(embed, "clan-create", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="clan-join", description="Join an existing player clan by name.")
    async def clan_join_cmd(self, interaction: discord.Interaction, name: str):
        session = get_session()
        try:
            logger.info("CMD /clan-join begin %s name=%r", interaction_ctx(interaction), name)
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.clan_id is not None:
                await interaction.response.send_message(
                    "You are already in a clan. Use `/clan-leave` first.", ephemeral=False
                )
                return

            name = name.strip()
            if not name:
                await interaction.response.send_message("Clan name cannot be empty.", ephemeral=False)
                return

            clan = get_clan_by_name_lookup(session, guild_id, name)
            if clan is None:
                await interaction.response.send_message("That clan does not exist.", ephemeral=False)
                return

            ok, reason = can_join_clan(session, player, clan)
            if not ok:
                await interaction.response.send_message(reason, ephemeral=False)
                return

            player.clan_id = clan.id
            player.clan_role = "member"
            player.clan_contribution_qi_total = 0
            clan.member_count += 1
            consume_clan_invitation(session, guild_id, discord_id, clan.id)

            session.add(player)
            session.add(clan)
            session.commit()
            logger.info(
                "Clan joined guild=%s user=%s clan_id=%s clan_name=%r new_member_count=%s",
                guild_id,
                discord_id,
                clan.id,
                clan.name,
                clan.member_count,
            )

            embed = discord.Embed(
                title="You Join a Clan",
                description=(
                    f"You rally under `{clan.name}`. "
                    "Your cultivation will contribute qi to the clan total."
                ),
                color=discord.Color.green(),
            )
            attach_guidance(embed, "clan-join", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="clan-leave", description="Leave your current player clan.")
    async def clan_leave_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            logger.info("CMD /clan-leave begin %s", interaction_ctx(interaction))
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.clan_id is None:
                await interaction.response.send_message("You are not in a clan.", ephemeral=False)
                return

            clan = session.get(Clan, player.clan_id)
            clan_name = clan.name if clan else "your clan"

            async def confirm_leave(interaction: discord.Interaction):
                session2 = get_session()
                try:
                    player2 = ensure_player(session2, guild_id, discord_id)
                    if player2 is None:
                        await interaction.followup.send(NOT_STARTED_HINT, ephemeral=False)
                        return

                    clan2 = session2.get(Clan, player2.clan_id)
                    if clan2 is not None:
                        clan2.member_count = max(0, clan2.member_count - 1)
                        session2.add(clan2)

                    player2.clan_id = None
                    player2.clan_role = "member"
                    player2.clan_contribution_qi_total = 0

                    session2.add(player2)
                    session2.commit()
                    logger.info("Clan left guild=%s user=%s", guild_id, discord_id)

                    embed = discord.Embed(
                        title="You Leave the Clan",
                        description="You lower the banner. The path ahead is yours alone.",
                        color=discord.Color.orange(),
                    )
                    attach_guidance(embed, "clan-leave", player2, session2, cfg, utcnow())
                    await interaction.followup.send(embed=embed, ephemeral=False)
                finally:
                    session2.close()

            view = ConfirmActionView(
                discord_id,
                title="Leave Clan",
                description=f"Are you sure you want to leave **{clan_name}**?",
                confirm_label="Leave Clan",
                on_confirm=confirm_leave,
            )
            embed = discord.Embed(
                title="Leave Clan?",
                description=f"Are you sure you want to leave **{clan_name}**? This action cannot be undone.",
                color=discord.Color.orange(),
            )
            msg = await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            view.message = msg
        finally:
            session.close()

    @app_commands.command(name="clan", description="View your player clan details.")
    async def clan_cmd(self, interaction: discord.Interaction):
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /clan begin %s", interaction_ctx(interaction))
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.clan_id is None:
                await interaction.response.send_message(
                    "You are not in a clan. Use `/clan-create` or `/clan-join`.", ephemeral=False
                )
                return

            clan = session.get(Clan, player.clan_id)
            if clan is None:
                await interaction.response.send_message("Your clan record was not found.", ephemeral=False)
                return

            members = get_clan_top_contributors(session, guild_id, clan.id)
            lines = [f"{p.dao_name}: {p.clan_contribution_qi_total} qi" for p in members]

            embed = discord.Embed(
                title=f"Clan: {clan.name}",
                description=(
                    f"Members: {clan.member_count}\n"
                    f"Total contributed qi: {clan.clan_qi_contributed}\n"
                    f"Join policy: **{'invite only' if clan.invite_only else 'open'}**"
                ),
                color=discord.Color.blue(),
            )
            embed.add_field(name="Top Contributors", value="\n".join(lines) if lines else "None yet.", inline=False)
            attach_guidance(embed, "clan", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="clan-invite", description="Invite a cultivator to your clan (founder only).")
    @app_commands.describe(member="The player to invite to your clan.")
    async def clan_invite_cmd(self, interaction: discord.Interaction, member: discord.Member):
        session = get_session()
        try:
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.clan_id is None or player.clan_role != "founder":
                await interaction.response.send_message(
                    "Only a **clan founder** can send invitations.", ephemeral=False
                )
                return

            clan = session.get(Clan, player.clan_id)
            if clan is None:
                await interaction.response.send_message("Your clan record was not found.", ephemeral=False)
                return

            if member.bot:
                await interaction.response.send_message("You cannot invite bots.", ephemeral=False)
                return

            invitee_id = str(member.id)
            ok, msg = create_clan_invitation(
                session,
                clan=clan,
                invitee_discord_id=invitee_id,
                invited_by_discord_id=discord_id,
            )
            if not ok:
                await interaction.response.send_message(msg, ephemeral=False)
                return

            session.commit()
            embed = discord.Embed(
                title="Clan Invitation Sent",
                description=f"{msg}\nThey may join with **`/clan-join {clan.name}`**.",
                color=discord.Color.green(),
            )
            attach_guidance(embed, "clan-invite", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="clan-invites", description="View pending clan invitations for you.")
    async def clan_invites_cmd(self, interaction: discord.Interaction):
        session = get_session()
        try:
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            rows = list_clan_invitations_for_player(session, guild_id, discord_id)
            if not rows:
                await interaction.response.send_message(
                    "You have no pending clan invitations.", ephemeral=False
                )
                return

            lines = [f"• **{clan.name}** — `/clan-join {clan.name}`" for _, clan in rows]
            embed = discord.Embed(
                title="Pending Clan Invitations",
                description="\n".join(lines),
                color=discord.Color.blue(),
            )
            attach_guidance(embed, "clan-invites", player, session, cfg, utcnow())
            await interaction.response.send_message(embed=embed, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="clan-invite-only", description="Toggle whether your clan requires invitations (founder).")
    @app_commands.describe(enabled="True = invite only; False = open join.")
    async def clan_invite_only_cmd(self, interaction: discord.Interaction, enabled: bool):
        session = get_session()
        try:
            if interaction.guild is None:
                await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
                return

            cfg = get_config()
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            if player.clan_id is None or player.clan_role != "founder":
                await interaction.response.send_message(
                    "Only a **clan founder** can change join policy.", ephemeral=False
                )
                return

            clan = session.get(Clan, player.clan_id)
            if clan is None:
                await interaction.response.send_message("Your clan record was not found.", ephemeral=False)
                return

            msg = set_clan_invite_only(session, clan, enabled)
            session.commit()
            await interaction.response.send_message(msg, ephemeral=False)
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClanCog())
