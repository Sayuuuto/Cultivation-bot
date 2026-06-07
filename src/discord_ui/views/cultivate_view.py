from __future__ import annotations

import logging
import random

import discord

from ...config import get_config
from ...db import get_session
from ...models import Clan, Player
from ...character import get_character_modifiers
from ...cooldown_haste import consume_haste_for_activity
from ...game import (
    CultivateResult,
    cultivate,
    utcnow,
)
from ...cultivate_events import apply_cultivate_bonus_drops
from ...cultivation_preview import build_breakthrough_preview_embed
from ...effects import consume_effect_charge
from ...ui.embeds import build_cultivate_embed
from ..helpers import (
    get_guild_id,
    get_discord_id,
    ensure_player,
    NOT_STARTED_HINT,
    format_seconds,
    activity_cooldown_remaining,
    schedule_player_reminders,
    attach_guidance,
    interaction_ctx,
    realm_display,
    _prepare_player_for_breakthrough,
    _finish_breakthrough_attempt,
)
from ..helpers import _story_continue_after_creation as _story_continue_after_creation

logger = logging.getLogger("cultivation_bot")


class CultivationButtons(discord.ui.View):
    def __init__(self, owner_discord_id: str, cfg, rng: random.Random):
        super().__init__(timeout=None)
        self.owner_discord_id = owner_discord_id
        self.cfg = cfg
        self.rng = rng

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if get_discord_id(interaction.user) != self.owner_discord_id:
            logger.warning(
                "Button press denied %s owner=%s",
                interaction_ctx(interaction),
                self.owner_discord_id,
            )
            await interaction.response.send_message(
                "That cultivation command is not yours to press.",
                ephemeral=False,
            )
            return False
        return True


class CultivateButton(discord.ui.Button):
    def __init__(self, cfg, rng: random.Random):
        super().__init__(label="🧘 Cultivate", style=discord.ButtonStyle.primary)
        self.cfg = cfg
        self.rng = rng

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            logger.info("BUTTON Cultivate begin %s", interaction_ctx(interaction))
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            remaining = activity_cooldown_remaining(
                session, player, now, "cultivate",
                player.last_cultivate_at, self.cfg.cultivate_cooldown_seconds,
            )
            if remaining > 0:
                logger.debug(
                    "Button /cultivate cooldown block guild=%s user=%s remaining=%ss",
                    guild_id, discord_id, remaining,
                )
                await interaction.response.send_message(
                    f"The qi pool is not ready yet. Wait {format_seconds(remaining)}.",
                    ephemeral=False,
                )
                return

            mod = get_character_modifiers(session, player)

            clan = None
            if player.clan_id is not None:
                clan = session.get(Clan, player.clan_id)

            res: CultivateResult = cultivate(
                player, clan, self.cfg, rng=self.rng, mod=mod,
                session=session, player_id=player.id,
            )
            applied_drops = apply_cultivate_bonus_drops(session, player.id, res.bonus_drops or {})
            if "qi_gathering" in mod.active_effects:
                consume_effect_charge(session, player.id, "qi_gathering")
            consume_haste_for_activity(session, player.id, "cultivate")
            from ...story_mode import should_apply_trial_activity_cooldown

            if should_apply_trial_activity_cooldown(player, "cultivate"):
                player.last_cultivate_at = now
            schedule_player_reminders(session, player, self.cfg, "cultivate", now=now)

            session.add(player)
            if clan is not None:
                session.add(clan)
            session.commit()
            logger.info(
                "BUTTON Cultivate complete guild=%s user=%s qi_gain=%s stones_gain=%s new_qi=%s realm=%s/%s",
                guild_id, discord_id, res.qi_gain, res.stones_gain,
                player.qi, player.realm_index, player.substage,
            )

            embed = build_cultivate_embed(
                res, player,
                realm_display=realm_display(player.realm_index, player.substage),
                passive_qi=res.passive_qi_collected,
                applied_drops=applied_drops,
            )
            attach_guidance(embed, "cultivate", player, session, self.cfg, now)
            await interaction.response.send_message(embed=embed, ephemeral=False)
            await _story_continue_after_creation(
                interaction, session, player,
                on_player_update=_story_continue_after_creation,
            )
            session.commit()
        finally:
            session.close()


class BreakthroughCommitView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        cfg,
        rng: random.Random,
        bt_preview,
    ):
        super().__init__(timeout=120)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.cfg = cfg
        self.rng = rng
        self.bt_preview = bt_preview

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This breakthrough window belongs to another daoist.",
                ephemeral=False,
            )
            return False
        return True

    @discord.ui.button(label="Breakthrough", style=discord.ButtonStyle.danger)
    async def commit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=False)
        for item in self.children:
            item.disabled = True
        if interaction.message is not None:
            await interaction.message.edit(view=self)
        await _finish_breakthrough_attempt(
            interaction,
            guild_id=self.guild_id,
            discord_id=self.owner_discord_id,
            cfg=self.cfg,
            rng=self.rng,
            bt_preview=self.bt_preview,
        )

    @discord.ui.button(label="Hold Back", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        embed = discord.Embed(
            title="Breakthrough Held",
            description="You steady your breathing and keep cultivating.",
            color=discord.Color.light_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if interaction_msg := getattr(self, "message", None):
            try:
                await interaction_msg.edit(
                    embed=discord.Embed(
                        title="Breakthrough Held",
                        description="The preview window closed — your qi remains banked.",
                        color=discord.Color.light_grey(),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


class BreakthroughButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, cfg, rng: random.Random):
        super().__init__(label="Breakthrough", style=discord.ButtonStyle.secondary)
        self.owner_discord_id = owner_discord_id
        self.cfg = cfg
        self.rng = rng

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            player = ensure_player(session, guild_id, self.owner_discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            now = utcnow()
            await _prepare_player_for_breakthrough(session, player, self.cfg, now)
            from ...game import compute_breakthrough_preview
            mod = get_character_modifiers(session, player)
            bt_preview = compute_breakthrough_preview(
                player, mod, session=session, player_id=player.id,
            )
            session.add(player)
            session.commit()

            embed = build_breakthrough_preview_embed(player, bt_preview)
            view = (
                BreakthroughCommitView(
                    self.owner_discord_id, guild_id,
                    self.cfg, self.rng, bt_preview,
                )
                if bt_preview.can_attempt
                else None
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        finally:
            session.close()


class CultivateView(CultivationButtons):
    def __init__(self, owner_discord_id: str, cfg, rng: random.Random):
        super().__init__(owner_discord_id, cfg, rng)
        self.add_item(CultivateButton(cfg, rng))
        self.add_item(BreakthroughButton(owner_discord_id, cfg, rng))
