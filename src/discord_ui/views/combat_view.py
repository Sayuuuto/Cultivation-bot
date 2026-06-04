from __future__ import annotations

import logging

import discord

from ...combat.discord_ui import build_adventure_combat_embed, build_combat_embed
from ...combat.loadout import get_equipped_active_techniques
from ...combat.session import abandon_active_combat, process_combat_action
from ...config import get_config
from ...cooldown_haste import consume_haste_for_activity
from ...db import get_session
from ...game import utcnow
from ...models import Player
from ...adventure import (
    AdventureResult,
    PendingAdventure,
    apply_adventure_combat_outcome,
)
from ...character import get_character_modifiers
from ...combat_stats import compute_combat_stats
from ...hunt import finalize_hunt_combat
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

from ...ui.formatting import technique_button_emoji


class AbandonStuckCombatView(discord.ui.View):
    """Shown when a player hits COMBAT_BUSY_MESSAGE — clears orphaned active_combats rows."""

    def __init__(self, owner_discord_id: str, guild_id: str):
        super().__init__(timeout=120)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        btn = discord.ui.Button(
            label="Clear combat",
            style=discord.ButtonStyle.danger,
            custom_id=f"combat:abandon:{owner_discord_id}",
        )
        btn.callback = self._on_abandon
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "Only the daoist who owns this fight can clear it.",
                ephemeral=False,
            )
            return False
        return True

    async def _on_abandon(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return
            cleared, message = abandon_active_combat(session, player.id)
            if cleared:
                session.commit()
            await interaction.response.edit_message(content=message, embed=None, view=None)
        finally:
            session.close()


class CombatView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        combat_id: int,
        context: str,
        *,
        area_id: str = "",
        beast_id: str = "",
        active_id: int | None = None,
        area_name: str = "",
        techniques: list | None = None,
        technique_cooldowns: dict[str, int] | None = None,
        player_sealed: bool = False,
    ):
        super().__init__(timeout=300)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.combat_id = combat_id
        self.context = context
        self.area_id = area_id
        self.beast_id = beast_id
        self.active_id = active_id
        self.area_name = area_name
        cds = technique_cooldowns or {}

        for slot_idx, tech in enumerate((techniques or [])[:4]):
            emoji = technique_button_emoji(tech.category)
            cd = cds.get(tech.technique_id, 0)
            sealed_blocked = player_sealed and tech.technique_id != "basic_strike"
            label = f"{emoji} {tech.name}"
            if cd > 0:
                label = f"⏳{cd} {label}"
            elif sealed_blocked:
                label = f"🔒 {label}"
            label = label[:80]
            if tech.technique_id == "basic_strike":
                style = discord.ButtonStyle.primary
            elif cd > 0 or sealed_blocked:
                style = discord.ButtonStyle.secondary
            else:
                style = discord.ButtonStyle.danger
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"cbt:{combat_id}:s{slot_idx}:{tech.technique_id}",
                disabled=cd > 0 or sealed_blocked,
            )
            button.callback = self._make_technique_callback(tech.technique_id)
            self.add_item(button)

        pass_btn = discord.ui.Button(
            label="⏭ Pass Turn",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cbt:{combat_id}:pass",
        )
        pass_btn.callback = self._make_action_callback("pass")
        self.add_item(pass_btn)

        flee_btn = discord.ui.Button(
            label="🏃 Flee",
            style=discord.ButtonStyle.secondary,
            custom_id=f"cbt:{combat_id}:flee",
        )
        flee_btn.callback = self._make_action_callback("flee")
        self.add_item(flee_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This fight belongs to another daoist.",
                ephemeral=False,
            )
            return False
        return True

    def _make_technique_callback(self, technique_id: str):
        async def callback(interaction: discord.Interaction):
            await self._handle_action(interaction, "technique", technique_id=technique_id)
        return callback

    def _make_action_callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            await self._handle_action(interaction, action)
        return callback

    async def _handle_action(
        self,
        interaction: discord.Interaction,
        action: str,
        *,
        technique_id: str | None = None,
    ) -> None:
        session = get_session()
        try:
            cfg = get_config()
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            mod = get_character_modifiers(session, player)
            stats = compute_combat_stats(player, session, mod)
            rng = rng_for(self.guild_id, discord_id)
            result, err = process_combat_action(
                session, player, self.combat_id, action,
                technique_id=technique_id, stats=stats, mod=mod, rng=rng,
            )
            if err:
                await interaction.response.send_message(err, ephemeral=False)
                return
            assert result is not None
            state = result.state
            now = utcnow()

            if not state.finished:
                techniques = get_equipped_active_techniques(session, player.id)
                if self.context == "hunt":
                    embed = build_combat_embed(f"Hunt — {self.area_name}", state)
                else:
                    pending = PendingAdventure(
                        active_id=self.active_id or 0,
                        area_name=self.area_name,
                        segment=1, segments_total=2,
                        prompt="Combat in progress.",
                        encounter_type="combat",
                    )
                    embed = build_adventure_combat_embed(pending, state)
                view = CombatView(
                    self.owner_discord_id, self.guild_id, self.combat_id, self.context,
                    area_id=self.area_id, beast_id=self.beast_id,
                    active_id=self.active_id, area_name=self.area_name,
                    techniques=techniques,
                    technique_cooldowns=state.technique_cooldowns,
                    player_sealed=state.player.sealed,
                )
                session.commit()
                await interaction.response.edit_message(embed=embed, view=view)
                return

            if self.context == "hunt":
                hunt_res = finalize_hunt_combat(
                    session, player, self.area_id, self.beast_id, state.victory, rng=rng,
                )
                from ...story_mode import should_apply_trial_activity_cooldown
                if should_apply_trial_activity_cooldown(player, "hunt"):
                    player.last_hunt_at = now
                player.last_active_at = now
                consume_haste_for_activity(session, player.id, "hunt")
                schedule_player_reminders(session, player, cfg, "hunt", now=now)
                session.add(player)
                session.commit()

                color = discord.Color.dark_green() if hunt_res.success else discord.Color.dark_red()
                from ...ui.embeds import build_hunt_result_embed
                embed = build_hunt_result_embed(self.area_name or hunt_res.area_name, state, hunt_res)
                embed.color = color
                attach_guidance(embed, "hunt", player, session, cfg, now)
                await interaction.response.edit_message(embed=embed, view=None)
                await _story_continue_after_creation(
                    interaction, session, player,
                    on_player_update=_story_continue_after_creation,
                )
                session.commit()
                return

            assert self.active_id is not None
            adventure_result, adv_err = apply_adventure_combat_outcome(
                session, player, self.active_id, victory=state.victory, fled=state.fled, rng=rng,
            )
            if adv_err:
                await interaction.response.send_message(adv_err, ephemeral=False)
                return

            if isinstance(adventure_result, PendingAdventure):
                if adventure_result.encounter_type == "combat" and adventure_result.combat_id:
                    techniques = get_equipped_active_techniques(session, player.id)
                    from ...combat.session import get_active_combat, load_combat_state
                    active_combat = get_active_combat(session, player.id)
                    combat_state = load_combat_state(active_combat) if active_combat else state
                    embed = build_adventure_combat_embed(adventure_result, combat_state)
                    view = CombatView(
                        self.owner_discord_id, self.guild_id,
                        adventure_result.combat_id, "adventure",
                        area_id=self.area_id,
                        active_id=adventure_result.active_id,
                        area_name=adventure_result.area_name,
                        techniques=techniques,
                        technique_cooldowns=combat_state.technique_cooldowns,
                        player_sealed=combat_state.player.sealed,
                    )
                else:
                    from ...ui.embeds import build_adventure_embed_from_pending
                    embed = build_adventure_embed_from_pending(adventure_result)
                    attach_guidance(embed, "adventure", player, session, cfg, now)
                    from .adventure_view import AdventureChoiceView
                    view = AdventureChoiceView(
                        self.owner_discord_id, self.guild_id,
                        adventure_result.active_id, adventure_result.choices,
                    )
                session.commit()
                await interaction.response.edit_message(embed=embed, view=view)
                return

            assert isinstance(adventure_result, AdventureResult)
            trial_msgs = _apply_adventure_completion(session, player, adventure_result, now)
            consume_haste_for_activity(session, player.id, "adventure")
            schedule_player_reminders(session, player, cfg, "adventure", now=now)
            session.add(player)
            session.commit()
            from ...ui.embeds import build_adventure_embed_from_result
            embed = build_adventure_embed_from_result(adventure_result, player.qi)
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

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        target = getattr(self, "message", None)
        if target is not None:
            try:
                await target.edit(
                    content="_Combat paused — use **`/hunt`** again to continue._",
                    view=self,
                )
            except discord.HTTPException:
                pass
