from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..helpers import (
    interaction_ctx,
    get_guild_id,
    get_discord_id,
    ensure_player,
    NOT_STARTED_HINT,
    schedule_player_reminders,
    attach_guidance,
    realm_display,
    rng_for,
    format_seconds,
    activity_cooldown_remaining,
    _prepare_player_for_breakthrough,
    _story_continue_after_creation,
)
from ..views import BreakthroughCommitView
from ...character import get_character_modifiers
from ...cooldown_haste import consume_haste_for_activity, get_haste_reduction_seconds
from ...config import get_config
from ...cultivate_events import apply_cultivate_bonus_drops
from ...cultivation_preview import build_breakthrough_preview_embed
from ...db import get_session
from ...effects import consume_effect_charge
from ...game import (
    CultivateResult,
    cultivate,
    compute_daily_rewards,
    collect_passive_qi,
    update_daily_streak,
    compute_breakthrough_preview,
    utcnow,
)
from ...models import Clan, Player
from ...novice_trial import on_daily_claimed
from ...story_delivery import maybe_sync_elder_story, send_elder_followup
from ...ui.embeds import build_cultivate_embed

logger = logging.getLogger("cultivation_bot")


class CultivationCog(commands.Cog):
    """Cultivation commands: cultivate, breakthrough, daily."""

    @app_commands.command(name="cultivate", description="Cultivate qi to strengthen yourself.")
    async def cultivate_cmd(self, interaction: discord.Interaction):
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /cultivate begin %s", interaction_ctx(interaction))
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
                "cultivate",
                player.last_cultivate_at,
                cfg.cultivate_cooldown_seconds,
            )
            if remaining > 0:
                logger.debug(
                    "Cooldown block /cultivate guild=%s user=%s remaining=%ss last_cultivate_at=%s",
                    guild_id,
                    discord_id,
                    remaining,
                    player.last_cultivate_at,
                )
                haste = get_haste_reduction_seconds(session, player.id, "cultivate")
                extra = f" (pill haste: −{format_seconds(haste)})" if haste > 0 else ""
                await interaction.response.send_message(
                    f"Your qi pool still settles. Wait {format_seconds(remaining)}.{extra}",
                    ephemeral=False,
                )
                return

            logger.debug(
                "Pre-cultivate state qi=%s last_active_at=%s last_cultivate_at=%s realm=%s/%s",
                player.qi,
                player.last_active_at,
                player.last_cultivate_at,
                player.realm_index,
                player.substage,
            )
            mod = get_character_modifiers(session, player)

            clan = None
            if player.clan_id is not None:
                clan = session.get(Clan, player.clan_id)

            rng = rng_for(guild_id, discord_id)
            res: CultivateResult = cultivate(
                player, clan, cfg, rng=rng, mod=mod, session=session, player_id=player.id
            )
            applied_drops = apply_cultivate_bonus_drops(session, player.id, res.bonus_drops or {})
            if "qi_gathering" in mod.active_effects:
                consume_effect_charge(session, player.id, "qi_gathering")
            consume_haste_for_activity(session, player.id, "cultivate")
            from ...story_mode import should_apply_trial_activity_cooldown

            if should_apply_trial_activity_cooldown(player, "cultivate"):
                player.last_cultivate_at = now
            schedule_player_reminders(session, player, cfg, "cultivate", now=now)

            session.add(player)
            if clan is not None:
                session.add(clan)
            session.commit()

            logger.info(
                "Cultivate complete guild=%s user=%s qi_gain=%s stones_gain=%s new_qi=%s realm=%s/%s",
                guild_id,
                discord_id,
                res.qi_gain,
                res.stones_gain,
                player.qi,
                player.realm_index,
                player.substage,
            )

            embed = build_cultivate_embed(
                res,
                player,
                realm_display=realm_display(player.realm_index, player.substage),
                passive_qi=res.passive_qi_collected,
                applied_drops=applied_drops,
            )
            attach_guidance(embed, "cultivate", player, session, cfg, now)
            await interaction.response.send_message(embed=embed, ephemeral=False)
            from ...story_mode import elder_trial_active
            if elder_trial_active(player):
                await send_elder_followup(
                    interaction,
                    session,
                    player,
                    on_player_update=_story_continue_after_creation,
                )
            else:
                await maybe_sync_elder_story(
                    interaction,
                    session,
                    player,
                    on_player_update=_story_continue_after_creation,
                )
            session.commit()
        finally:
            session.close()

    @app_commands.command(name="breakthrough", description="Review breakthrough odds, then commit or hold back.")
    async def breakthrough_cmd(self, interaction: discord.Interaction):
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /breakthrough begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            await _prepare_player_for_breakthrough(session, player, cfg, now)
            mod = get_character_modifiers(session, player)
            bt_preview = compute_breakthrough_preview(
                player, mod, session=session, player_id=player.id
            )
            session.add(player)
            session.commit()

            embed = build_breakthrough_preview_embed(player, bt_preview)
            view = (
                BreakthroughCommitView(
                    discord_id,
                    guild_id,
                    cfg,
                    rng_for(guild_id, discord_id),
                    bt_preview,
                )
                if bt_preview.can_attempt
                else None
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        finally:
            session.close()

    @app_commands.command(name="daily", description="Claim today's cultivation stipend (UTC).")
    async def daily_cmd(self, interaction: discord.Interaction):
        cfg = get_config()
        session = get_session()
        try:
            logger.info("CMD /daily begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            mod = get_character_modifiers(session, player)
            collect_passive_qi(player, now, cap_mult=mod.offline_efficiency)
            player.last_active_at = now

            daily_remaining = activity_cooldown_remaining(
                session,
                player,
                now,
                "daily",
                player.last_daily_at,
                cfg.daily_cooldown_seconds,
            )
            if daily_remaining > 0:
                haste = get_haste_reduction_seconds(session, player.id, "daily")
                extra = f" (pill haste: \u2212{format_seconds(haste)})" if haste > 0 else ""
                await interaction.response.send_message(
                    f"Your stipend is still cooling down \u2014 **{format_seconds(daily_remaining)}** remaining{extra}.",
                    ephemeral=False,
                )
                return

            update_daily_streak(player, now)

            consume_haste_for_activity(session, player.id, "daily")
            stones, qi = compute_daily_rewards(player)
            logger.info(
                "Daily claimed guild=%s user=%s stones=%s qi=%s streak=%s realm=%s/%s",
                guild_id,
                discord_id,
                stones,
                qi,
                player.daily_streak,
                player.realm_index,
                player.substage,
            )
            player.spirit_stones += stones
            player.qi += qi
            trial_msgs, story_next = on_daily_claimed(player)
            schedule_player_reminders(session, player, cfg, "daily", now=now)

            from ...foundation import apply_lesser_body_temper

            temper_res = apply_lesser_body_temper(player, rng=rng_for(guild_id, discord_id))
            temper_line = f"\n\n{temper_res.message}" if temper_res.success else ""

            from ...story_mode import should_apply_trial_activity_cooldown

            if should_apply_trial_activity_cooldown(player, "daily"):
                player.last_daily_at = now
            player.last_active_at = now

            session.add(player)
            session.commit()

            from ...story_mode import elder_trial_active
            in_trial = elder_trial_active(player)
            embed = discord.Embed(
                title="Daily Stipend",
                description=(
                    f"You accept the day's offerings.\n"
                    f"+{stones} spirit stones, +{qi} qi."
                    + temper_line
                    + (f"\n\n" + "\n".join(trial_msgs) if trial_msgs and not in_trial else "")
                ),
                color=discord.Color.purple(),
            )
            embed.add_field(name="Daily Streak", value=str(player.daily_streak), inline=True)
            attach_guidance(embed, "daily", player, session, cfg, now)
            await interaction.response.send_message(embed=embed, ephemeral=False)
            if in_trial:
                await send_elder_followup(
                    interaction,
                    session,
                    player,
                    on_player_update=_story_continue_after_creation,
                )
            else:
                await maybe_sync_elder_story(
                    interaction,
                    session,
                    player,
                    on_player_update=_story_continue_after_creation,
                )
            session.commit()
        finally:
            session.close()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CultivationCog())
