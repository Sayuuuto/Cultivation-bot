from __future__ import annotations

import json
import logging

import discord

from ...config import get_config
from ...db import get_session
from ...explore import (
    StepResult,
    build_explore_result_embed,
    build_explore_step_embed,
    calculate_affinity,
    finalize_explore,
    get_active_explore,
    load_explore_areas,
    retreat,
)
from ...game import utcnow
from ...models import ExploreSession, Player
from ..helpers import (
    NOT_STARTED_HINT,
    attach_guidance,
    ensure_player,
    get_discord_id,
    interaction_ctx,
)

logger = logging.getLogger("cultivation_bot")


class ExploreView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        explore_session_id: int,
        choices: list,
        area_name: str,
        step: int,
        total_steps: int,
    ):
        super().__init__(timeout=300)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.explore_session_id = explore_session_id
        self.area_name = area_name
        self.step = step
        self.total_steps = total_steps

        for choice in choices[:4]:
            label = choice.label[:80]
            style = discord.ButtonStyle.primary
            if choice.type == "combat":
                label = f"\u2694\ufe0f {label}"
                style = discord.ButtonStyle.danger
            elif choice.type in ("rest",):
                label = f"\U0001f49a {label}"
                style = discord.ButtonStyle.success
            elif choice.type == "dao_event":
                label = f"\u2728 {label}"
                style = discord.ButtonStyle.success
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"explr:{explore_session_id}:{choice.id}",
            )
            button.callback = self._make_choice_callback(choice.id)
            self.add_item(button)

        retreat_btn = discord.ui.Button(
            label="\U0001f3f3\ufe0f Retreat",
            style=discord.ButtonStyle.secondary,
            custom_id=f"explr:{explore_session_id}:__retreat__",
        )
        retreat_btn.callback = self._retreat_callback
        self.add_item(retreat_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This expedition belongs to another daoist.",
                ephemeral=False,
            )
            return False
        return True

    def _make_choice_callback(self, choice_id: str):
        async def callback(interaction: discord.Interaction):
            await self._handle_choice(interaction, choice_id)
        return callback

    async def _handle_choice(
        self,
        interaction: discord.Interaction,
        choice_id: str,
    ) -> None:
        from ...explore import resolve_choice

        session = get_session()
        try:
            cfg = get_config()
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            explore_row = session.get(ExploreSession, self.explore_session_id)
            if explore_row is None:
                await interaction.response.send_message("Expedition session not found.", ephemeral=False)
                return

            result, err = resolve_choice(
                session, player, explore_row, choice_id,
            )
            if err:
                await interaction.response.send_message(err, ephemeral=False)
                return
            assert result is not None

            if result.combat_id:
                from ...combat.loadout import ensure_starter_techniques, get_equipped_active_techniques
                from ...combat.session import get_active_combat, load_combat_state

                ensure_starter_techniques(session, player.id)
                techniques = get_equipped_active_techniques(session, player.id)
                active_combat = get_active_combat(session, player.id)
                combat_state = load_combat_state(active_combat) if active_combat else None
                from ...combat.discord_ui import build_adventure_combat_embed

                from .combat_view import CombatView

                pending = type("Pending", (), {
                    "active_id": result.combat_id,
                    "area_name": self.area_name,
                    "segment": 1, "segments_total": 2,
                    "prompt": "Combat in the expedition.",
                    "encounter_type": "combat",
                })()
                embed = build_adventure_combat_embed(pending, combat_state) if combat_state else discord.Embed(title="Combat starting...")
                view = CombatView(
                    self.owner_discord_id, self.guild_id,
                    result.combat_id, "explore",
                    area_id=explore_row.area_id,
                    active_id=self.explore_session_id,
                    area_name=self.area_name,
                    techniques=techniques,
                    technique_cooldowns=combat_state.technique_cooldowns if combat_state else {},
                    player_sealed=combat_state.player.sealed if combat_state else False,
                )
                session.commit()
                await interaction.response.edit_message(embed=embed, view=view)
                return

            state_raw = json.loads(explore_row.state_json) if isinstance(explore_row.state_json, str) else {}

            if result.finished:
                msgs, has_bonus = finalize_explore(session, player, explore_row)
                final_state = json.loads(explore_row.state_json) if isinstance(explore_row.state_json, str) else {}
                final_rewards = final_state.get("rewards", [])
                embed = build_explore_result_embed(
                    self.area_name, result, final_rewards,
                )
                if msgs:
                    embed.add_field(name="Granted", value="\n".join(msgs[:8]), inline=False)
                attach_guidance(embed, "explore", player, session, cfg, utcnow())
                session.commit()
                await interaction.response.edit_message(embed=embed, view=None)
                return

            areas = load_explore_areas()
            area = areas.get(explore_row.area_id)
            affinity = calculate_affinity(player, area) if area else 1.0
            embed = build_explore_step_embed(
                self.area_name,
                result.new_step + 1,
                result.total_steps,
                result.encounter.title if result.encounter else "",
                result.encounter.text if result.encounter else "",
                state_raw.get("current_hp", 0),
                state_raw.get("max_hp", 1),
                affinity=affinity,
                danger=area.danger if area else 1.0,
            )
            if result.messages:
                embed.add_field(name="Outcome", value="\n".join(result.messages[-3:]), inline=False)

            attach_guidance(embed, "explore", player, session, cfg, utcnow())
            choices = result.encounter.choices if result.encounter else []
            view = ExploreView(
                self.owner_discord_id, self.guild_id,
                self.explore_session_id, choices,
                self.area_name, result.new_step + 1, result.total_steps,
            )
            session.commit()
            await interaction.response.edit_message(embed=embed, view=view)
        finally:
            session.close()

    async def _retreat_callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            cfg = get_config()
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            explore_row = session.get(ExploreSession, self.explore_session_id)
            if explore_row is None:
                await interaction.response.send_message("Expedition not found.", ephemeral=False)
                return

            result = retreat(session, player, explore_row)
            state_raw = json.loads(explore_row.state_json) if isinstance(explore_row.state_json, str) else {}
            rewards = state_raw.get("rewards", [])
            msgs, _ = finalize_explore(session, player, explore_row)

            embed = build_explore_result_embed(self.area_name, result, rewards)
            if msgs:
                embed.add_field(name="Granted", value="\n".join(msgs[:8]), inline=False)
            attach_guidance(embed, "explore", player, session, cfg, utcnow())
            session.commit()
            await interaction.response.edit_message(embed=embed, view=None)
        finally:
            session.close()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        target = getattr(self, "message", None)
        if target is not None:
            try:
                await target.edit(
                    content="_Expedition paused \u2014 use `/explore` to resume._",
                    view=self,
                )
            except discord.HTTPException:
                pass
