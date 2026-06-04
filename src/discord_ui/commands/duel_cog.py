from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from ...config import get_config
from ...db import get_session
from ...duel_challenges import attach_challenge_message, create_duel_challenge
from ..helpers import (
    build_duel_challenge_embed,
    ensure_player,
    get_discord_id,
    get_guild_id,
    interaction_ctx,
    rng_for,
    schedule_player_reminders,
)
from ..views import DuelChallengeView

logger = logging.getLogger("cultivation_bot")


class DuelCog(commands.Cog):
    @app_commands.command(name="duel", description="Challenge another daoist to a turn-based arena duel.")
    @app_commands.describe(opponent="Another player who has used /start (not a bot).")
    async def duel_cmd(self, interaction: discord.Interaction, opponent: discord.User):
        cfg = get_config()
        if interaction.guild is None:
            await interaction.response.send_message("This bot works inside a server.", ephemeral=False)
            return

        if opponent.bot:
            await interaction.response.send_message("You cannot duel a bot.", ephemeral=False)
            return

        guild_id = get_guild_id(interaction)
        challenger_id = get_discord_id(interaction.user)
        opponent_id = get_discord_id(opponent)

        if challenger_id == opponent_id:
            await interaction.response.send_message("You cannot duel yourself.", ephemeral=False)
            return

        await interaction.response.defer(ephemeral=False)

        session = get_session()
        try:
            logger.info("CMD /duel challenge %s opponent=%s", interaction_ctx(interaction), opponent.id)

            challenger = ensure_player(session, guild_id, challenger_id)
            opponent_player = ensure_player(session, guild_id, opponent_id)
            if challenger is None or opponent_player is None:
                missing = []
                if challenger is None:
                    missing.append("you")
                if opponent_player is None:
                    missing.append(f"**{opponent.display_name}**")
                await interaction.followup.send(
                    f"Both players need a character. Missing: {', '.join(missing)}. Use **`/start`** first.",
                    ephemeral=False,
                )
                return

            challenge, err = create_duel_challenge(session, guild_id, challenger, opponent_player, cfg)
            if err:
                await interaction.followup.send(err, ephemeral=False)
                return

            assert challenge is not None
            session.commit()

            embed = build_duel_challenge_embed(challenger, opponent_player)
            view = DuelChallengeView(challenge.id, guild_id, challenger_id, opponent_id)
            public_msg = await interaction.channel.send(
                content=f"{opponent.mention} — **{challenger.dao_name}** challenges you to a duel!",
                embed=embed,
                view=view,
            )
            view.message = public_msg
            attach_challenge_message(session, challenge, str(public_msg.channel.id), str(public_msg.id))
            session.commit()

            await interaction.followup.send(
                f"Challenge sent to **{opponent_player.dao_name}** in this channel. "
                f"They have **2 minutes** to Accept or Decline.",
                ephemeral=False,
            )
        except Exception:
            logger.exception("CMD /duel failed %s", interaction_ctx(interaction))
            await interaction.followup.send(
                "The duel challenge could not be sent. Check bot logs or try again in a moment.",
                ephemeral=False,
            )
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DuelCog())
