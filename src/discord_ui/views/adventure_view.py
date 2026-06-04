from __future__ import annotations

import logging

import discord

from ...config import get_config
from ...cooldown_haste import consume_haste_for_activity
from ...db import get_session
from ...game import utcnow
from ...models import ActiveAdventure, Player
from ...adventure import (
    AdventureResult,
    PendingAdventure,
    abandon_adventure,
    apply_adventure_choice,
)
from ...combat.loadout import ensure_starter_techniques, get_equipped_active_techniques
from ...ui.embeds import (
    build_adventure_embed_from_pending,
    build_adventure_embed_from_result,
)
from ..helpers import (
    get_discord_id,
    ensure_player,
    NOT_STARTED_HINT,
    rng_for,
    _apply_adventure_completion,
    schedule_player_reminders,
    attach_guidance,
    interaction_ctx,
)
from ..helpers import _story_continue_after_creation as _story_continue_after_creation

logger = logging.getLogger("cultivation_bot")


class AdventureChoiceView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        active_id: int,
        choices: tuple,
    ):
        super().__init__(timeout=180)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.active_id = active_id
        for choice in choices[:5]:
            button = discord.ui.Button(
                label=choice.label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"adv:{active_id}:{choice.id}",
            )
            button.callback = self._make_callback(choice.id)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This choice belongs to another daoist.",
                ephemeral=False,
            )
            return False
        return True

    def _make_callback(self, choice_id: str):
        async def callback(interaction: discord.Interaction):
            session = get_session()
            try:
                cfg = get_config()
                discord_id = get_discord_id(interaction.user)
                player = ensure_player(session, self.guild_id, discord_id)
                if player is None:
                    await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                    return

                rng = rng_for(self.guild_id, discord_id)
                result, err = apply_adventure_choice(
                    session, player, self.active_id, choice_id, rng=rng
                )
                if err:
                    await interaction.response.send_message(err, ephemeral=False)
                    return

                now = utcnow()
                if isinstance(result, PendingAdventure):
                    if result.encounter_type == "combat" and result.combat_id:
                        from ...combat.session import get_active_combat, load_combat_state
                        ensure_starter_techniques(session, player.id)
                        techniques = get_equipped_active_techniques(session, player.id)
                        active_row = session.get(ActiveAdventure, result.active_id)
                        area_id = active_row.area_id if active_row else ""
                        active_combat = get_active_combat(session, player.id)
                        combat_state = load_combat_state(active_combat) if active_combat else None
                        from ...combat.discord_ui import build_adventure_combat_embed
                        embed = (
                            build_adventure_combat_embed(result, combat_state)
                            if combat_state
                            else build_adventure_embed_from_pending(result)
                        )
                        from .combat_view import CombatView
                        view = CombatView(
                            self.owner_discord_id, self.guild_id,
                            result.combat_id, "adventure",
                            area_id=area_id,
                            active_id=result.active_id,
                            area_name=result.area_name,
                            techniques=techniques,
                            technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                            player_sealed=combat_state.player.sealed if combat_state else False,
                        )
                    else:
                        embed = build_adventure_embed_from_pending(result)
                        attach_guidance(embed, "adventure", player, session, cfg, now)
                        view = AdventureChoiceView(
                            self.owner_discord_id, self.guild_id,
                            result.active_id, result.choices,
                        )
                    session.commit()
                    await interaction.response.edit_message(embed=embed, view=view)
                    return

                assert isinstance(result, AdventureResult)
                trial_msgs = _apply_adventure_completion(session, player, result, now)
                consume_haste_for_activity(session, player.id, "adventure")
                schedule_player_reminders(session, player, cfg, "adventure", now=now)
                session.add(player)
                session.commit()

                embed = build_adventure_embed_from_result(result, player.qi)
                if trial_msgs:
                    embed.add_field(name="Trial progress", value="\n".join(trial_msgs), inline=False)
                attach_guidance(embed, "adventure", player, session, cfg, now)
                await interaction.response.edit_message(embed=embed, view=None)
                await _story_continue_after_creation(
                    interaction, session, player,
                    on_player_update=_story_continue_after_creation,
                )
                session.commit()
            finally:
                session.close()
        return callback

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        session = get_session()
        try:
            row = session.get(ActiveAdventure, self.active_id)
            if row is not None:
                player = session.get(Player, row.player_id)
                if player is not None:
                    abandon_adventure(session, player.id)
                    session.commit()
        except Exception:
            logger.exception("Adventure choice timeout failed active_id=%s", self.active_id)
        finally:
            session.close()
        if getattr(self, "message", None) is not None:
            try:
                await self.message.edit(
                    embed=discord.Embed(
                        title="Adventure Withdrawn",
                        description="The choice window closed — your run ends without reward.",
                        color=discord.Color.dark_grey(),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass
