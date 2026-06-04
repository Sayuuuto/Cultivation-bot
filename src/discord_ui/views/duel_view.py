from __future__ import annotations

import logging

import discord

from ...config import get_config
from ...db import get_session
from ...models import ActivePvpMatch
from ...pvp_arena import build_pvp_combat_embed, create_arena_channel, post_pvp_results, schedule_arena_cleanup
from ...pvp_combat import PvpCombatState, combat_slice_for_actor, process_pvp_action
from ...duel_challenges import (
    DUEL_CHALLENGE_TIMEOUT_SECONDS,
    accept_duel_challenge,
    decline_duel_challenge,
    expire_duel_challenge,
)
from ...pvp_match import (
    finalize_pvp_match,
    load_pvp_match_state,
    save_pvp_match_state,
)
from ...combat.loadout import ensure_starter_techniques, get_equipped_active_techniques
from ...ui.formatting import technique_button_emoji
from ..helpers import (
    get_discord_id,
    ensure_player,
    rng_for,
    interaction_ctx,
    build_duel_challenge_embed,
    build_duel_started_embed,
    build_duel_expired_embed,
    build_duel_declined_embed,
    _finalize_pvp_match_discord,
)

logger = logging.getLogger("cultivation_bot")

class DuelCombatView(discord.ui.View):
    def __init__(
        self,
        match_id: int,
        guild_id: str,
        participant_ids: set[str],
        actor_discord_id: str,
        *,
        techniques: list | None = None,
        technique_cooldowns: dict[str, int] | None = None,
        player_sealed: bool = False,
    ):
        super().__init__(timeout=900)
        self.match_id = match_id
        self.guild_id = guild_id
        self.participant_ids = participant_ids
        self.actor_discord_id = actor_discord_id
        self.message: discord.Message | None = None
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
                custom_id=f"duel:{match_id}:s{slot_idx}:{tech.technique_id}",
                disabled=cd > 0 or sealed_blocked,
            )
            button.callback = self._make_technique_callback(tech.technique_id)
            self.add_item(button)

        pass_btn = discord.ui.Button(
            label="⏭ Pass Turn",
            style=discord.ButtonStyle.secondary,
            custom_id=f"duel:{match_id}:pass",
        )
        pass_btn.callback = self._make_action_callback("pass")
        self.add_item(pass_btn)

        flee_btn = discord.ui.Button(
            label="🏃 Yield",
            style=discord.ButtonStyle.secondary,
            custom_id=f"duel:{match_id}:flee",
        )
        flee_btn.callback = self._make_action_callback("flee")
        self.add_item(flee_btn)

    def _disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        actor_id = get_discord_id(interaction.user)
        if actor_id not in self.participant_ids:
            await interaction.response.send_message("This arena is not yours.", ephemeral=False)
            return False
        if actor_id != self.actor_discord_id:
            await interaction.response.send_message("Wait for your turn.", ephemeral=False)
            return False

        session = get_session()
        try:
            match = session.get(ActivePvpMatch, self.match_id)
            if match is None or match.status != "active":
                await interaction.response.send_message("This duel is no longer active.", ephemeral=False)
                return False
            state = load_pvp_match_state(match)
            if state.finished:
                await interaction.response.send_message("The duel is already over.", ephemeral=False)
                return False
            if actor_id != state.current_actor_id:
                await interaction.response.send_message("Wait for your turn.", ephemeral=False)
                return False
            return True
        finally:
            session.close()

    def _make_technique_callback(self, technique_id: str):
        async def callback(interaction: discord.Interaction):
            await self._resolve_action(interaction, "technique", technique_id=technique_id)
        return callback

    def _make_action_callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            await self._resolve_action(interaction, action)
        return callback

    async def _resolve_action(
        self,
        interaction: discord.Interaction,
        action: str,
        *,
        technique_id: str | None = None,
    ) -> None:
        await interaction.response.defer()
        session = get_session()
        try:
            cfg = get_config()
            match = session.get(ActivePvpMatch, self.match_id)
            if match is None or match.status != "active":
                await interaction.followup.send("This duel is no longer active.", ephemeral=False)
                return

            actor_id = get_discord_id(interaction.user)
            state = load_pvp_match_state(match)
            rng = rng_for(self.guild_id, f"pvp:{match.id}:{state.turn}:{actor_id}:{technique_id or action}")
            result = process_pvp_action(
                session, state, actor_id, action, rng, technique_id=technique_id,
            )
            if not result.ok:
                await interaction.followup.send(result.message, ephemeral=False)
                return

            save_pvp_match_state(session, match, result.state)
            embed = build_pvp_combat_embed(result.state)

            if result.state.finished:
                self._disable_buttons()
                session.commit()
                target = interaction.message if interaction.message is not None else self.message
                if target is not None:
                    await target.edit(embed=embed, view=self)
                assert interaction.guild is not None
                try:
                    await _finalize_pvp_match_discord(
                        interaction.client, interaction.guild, match, cfg,
                        combat_message=target, combat_state=result.state,
                    )
                except Exception:
                    logger.exception("PvP finalize failed match_id=%s", self.match_id)
                return

            actor = result.state.actor()
            actor_player = ensure_player(session, self.guild_id, actor.discord_id)
            if actor_player is None:
                await interaction.followup.send("Duelist record missing.", ephemeral=False)
                return
            ensure_starter_techniques(session, actor_player.id)
            techniques = get_equipped_active_techniques(session, actor_player.id)
            cs = combat_slice_for_actor(result.state)
            view = DuelCombatView(
                self.match_id, self.guild_id, self.participant_ids,
                result.state.current_actor_id,
                techniques=techniques,
                technique_cooldowns=cs.technique_cooldowns,
                player_sealed=cs.player.sealed,
            )
            view.message = interaction.message if interaction.message is not None else self.message
            session.commit()
            target = interaction.message if interaction.message is not None else self.message
            if target is not None:
                await target.edit(embed=embed, view=view)
        except Exception:
            logger.exception("PvP action failed match_id=%s action=%s", self.match_id, action)
            await interaction.followup.send("That action could not be resolved.", ephemeral=False)
        finally:
            session.close()

    async def on_timeout(self) -> None:
        self._disable_buttons()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class PvpCombatView(DuelCombatView):
    """Backward-compatible alias for tests and imports."""


class DuelChallengeView(discord.ui.View):
    def __init__(
        self,
        challenge_id: int,
        guild_id: str,
        challenger_discord_id: str,
        opponent_discord_id: str,
    ):
        super().__init__(timeout=DUEL_CHALLENGE_TIMEOUT_SECONDS)
        self.challenge_id = challenge_id
        self.guild_id = guild_id
        self.challenger_discord_id = challenger_discord_id
        self.opponent_discord_id = opponent_discord_id
        self.message: discord.Message | None = None

    def _disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _edit_message(self, embed: discord.Embed, interaction: discord.Interaction | None = None) -> None:
        self._disable_buttons()
        target = interaction.message if interaction is not None else self.message
        if target is not None:
            await target.edit(embed=embed, view=self)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor_id = get_discord_id(interaction.user)
        if actor_id != self.opponent_discord_id:
            await interaction.response.send_message(
                "Only the challenged daoist may accept this duel.",
                ephemeral=False,
            )
            return

        await interaction.response.defer()
        session = get_session()
        try:
            cfg = get_config()
            challenger = ensure_player(session, self.guild_id, self.challenger_discord_id)
            opponent = ensure_player(session, self.guild_id, self.opponent_discord_id)
            if challenger is None or opponent is None:
                await interaction.followup.send(
                    "One of the duelists no longer has a character.",
                    ephemeral=False,
                )
                return

            rng = rng_for(self.guild_id, f"{challenger.discord_id}:{opponent.discord_id}")
            started, err = accept_duel_challenge(
                session, self.challenge_id, actor_id, challenger, opponent, cfg, rng,
            )
            if err:
                await interaction.followup.send(err, ephemeral=False)
                return
            assert started is not None

            if interaction.guild is None:
                await interaction.followup.send("This duel must be accepted inside a server.", ephemeral=False)
                return

            challenger_member = interaction.guild.get_member(int(challenger.discord_id))
            opponent_member = interaction.guild.get_member(int(opponent.discord_id))
            if challenger_member is None:
                challenger_member = await interaction.guild.fetch_member(int(challenger.discord_id))
            if opponent_member is None:
                opponent_member = await interaction.guild.fetch_member(int(opponent.discord_id))

            arena, arena_err = await create_arena_channel(
                interaction.guild, challenger_member, opponent_member,
                challenger.dao_name, opponent.dao_name,
                category_id=cfg.arena_category_id,
            )
            if arena is None:
                started.match.status = "cancelled"
                session.add(started.match)
                session.commit()
                await interaction.followup.send(arena_err or "The arena could not be opened.", ephemeral=False)
                return

            started.match.arena_channel_id = str(arena.id)
            session.add(started.match)
            session.commit()

            actor_player = ensure_player(session, self.guild_id, started.state.current_actor_id)
            techniques: list = []
            technique_cooldowns: dict[str, int] = {}
            player_sealed = False
            if actor_player is not None:
                ensure_starter_techniques(session, actor_player.id)
                techniques = get_equipped_active_techniques(session, actor_player.id)
                cs = combat_slice_for_actor(started.state)
                technique_cooldowns = cs.technique_cooldowns
                player_sealed = cs.player.sealed

            combat_view = DuelCombatView(
                started.match.id, self.guild_id,
                {challenger.discord_id, opponent.discord_id},
                started.state.current_actor_id,
                techniques=techniques,
                technique_cooldowns=technique_cooldowns,
                player_sealed=player_sealed,
            )
            combat_embed = build_pvp_combat_embed(started.state)
            combat_msg = await arena.send(
                content=(
                    f"{challenger_member.mention} {opponent_member.mention} — "
                    "the arena is sealed. The bot will call each turn."
                ),
                embed=combat_embed, view=combat_view,
            )
            combat_view.message = combat_msg
            started.match.combat_message_id = str(combat_msg.id)
            session.add(started.match)
            session.commit()

            logger.info(
                "Duel arena opened challenge_id=%s match_id=%s guild=%s arena=%s",
                self.challenge_id, started.match.id, self.guild_id, arena.id,
            )
            await self._edit_message(build_duel_started_embed(challenger, opponent, arena), interaction)
        except Exception:
            logger.exception("Duel accept failed challenge_id=%s", self.challenge_id)
            await interaction.followup.send(
                "The duel could not be completed. Try again later.",
                ephemeral=False,
            )
        finally:
            session.close()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        actor_id = get_discord_id(interaction.user)
        if actor_id != self.opponent_discord_id:
            await interaction.response.send_message(
                "Only the challenged daoist may decline this duel.",
                ephemeral=False,
            )
            return

        await interaction.response.defer()
        session = get_session()
        try:
            challenge, err = decline_duel_challenge(session, self.challenge_id, actor_id)
            if err:
                await interaction.followup.send(err, ephemeral=False)
                return
            session.commit()
            assert challenge is not None
            await self._edit_message(build_duel_declined_embed(challenge), interaction)
        finally:
            session.close()

    async def on_timeout(self) -> None:
        session = get_session()
        try:
            challenge = expire_duel_challenge(session, self.challenge_id)
            if challenge is not None:
                session.commit()
                await self._edit_message(build_duel_expired_embed(challenge))
        finally:
            session.close()
