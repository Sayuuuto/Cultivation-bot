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
            label = self._format_choice_label(choice)
            style = discord.ButtonStyle.primary
            if choice.type == "combat":
                style = discord.ButtonStyle.danger
            elif choice.type in ("rest",):
                style = discord.ButtonStyle.success
            elif choice.type == "dao_event":
                style = discord.ButtonStyle.success
            button = discord.ui.Button(
                label=label[:80],
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

    @staticmethod
    def _format_choice_label(choice) -> str:
        base = choice.label
        t = getattr(choice, "type", "safe_skip")
        if t == "combat":
            return f"\u2694\ufe0f {base}"
        if t == "rest":
            heal = getattr(choice, "heal_pct", 0)
            pct = int(heal * 100) if heal > 0 else 30
            return f"\U0001f49a {base} (Heal {pct}%)"
        if t == "dao_event":
            return f"\u2728 {base}"
        if t == "pay_stones":
            amt = getattr(choice, "amount", 0)
            return f"\U0001f48e {base} ({amt} stones)"
        if t in ("stat_check", "trap"):
            icon = "\U0001f6e1\ufe0f" if t == "stat_check" else "\u26a0\ufe0f"
            stats = getattr(choice, "stats", [])
            stat_labels = {
                "might": "ATK",
                "qi_power": "DEF",
                "perception": "Spirit",
                "resolve": "Int",
                "speed": "Speed",
                "armor": "Armor",
                "karma": "Karma",
                "luck": "Luck",
            }
            hints = "+".join(stat_labels.get(s, s) for s in stats[:3])
            if hints:
                return f"{icon} {base} ({hints})"
            return f"{icon} {base}"
        if t == "risk_reward":
            fail = getattr(choice, "fail_damage_pct", 0)
            fail_pct = int(fail * 100) if fail > 0 else 15
            return f"\U0001f3b2 {base} ({fail_pct}% fail)"
        return base

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

        await interaction.response.defer()
        session = get_session()
        try:
            cfg = get_config()
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.followup.send(NOT_STARTED_HINT, ephemeral=False)
                return

            explore_row = session.get(ExploreSession, self.explore_session_id)
            if explore_row is None:
                await interaction.followup.send("Expedition session not found.", ephemeral=False)
                return

            result, err = resolve_choice(
                session, player, explore_row, choice_id,
            )
            if err:
                await interaction.followup.send(err, ephemeral=False)
                return
            assert result is not None

            if result.combat_id:
                from ...combat.loadout import get_equipped_active_techniques
                from ...combat.session import get_active_combat, load_combat_state

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
                await interaction.edit_original_response(embed=embed, view=view)
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
                await interaction.edit_original_response(embed=embed, view=None)
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
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            logger.exception("Error in explore choice callback (choice_id=%s)", choice_id)
            try:
                if not interaction.response.is_done():
                    await interaction.followup.send("Something went wrong processing your choice.", ephemeral=True)
            except Exception:
                pass
        finally:
            session.close()

    async def _retreat_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        session = get_session()
        try:
            cfg = get_config()
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, self.guild_id, discord_id)
            if player is None:
                await interaction.followup.send(NOT_STARTED_HINT, ephemeral=False)
                return

            explore_row = session.get(ExploreSession, self.explore_session_id)
            if explore_row is None:
                await interaction.followup.send("Expedition not found.", ephemeral=False)
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
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            logger.exception("Error in explore retreat callback")
            try:
                if not interaction.response.is_done():
                    await interaction.followup.send("Something went wrong during retreat.", ephemeral=True)
            except Exception:
                pass
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
