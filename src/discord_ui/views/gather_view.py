from __future__ import annotations

import asyncio
import logging
import random

import discord

from ...config import get_config
from ...db import get_session
from ...gather import run_gather
from ...guidance import add_guidance_to_embed as attach_guidance
from ..helpers import (
    consume_haste_for_activity,
    cooldown_remaining,
    ensure_player,
    get_discord_id,
    get_guild_id,
    rng_for,
    schedule_player_reminders,
    utcnow,
)

logger = logging.getLogger(__name__)

ZONE_POSITIONS = ("left", "center", "right")
ZONE_LABELS = {"left": "◀ Left", "center": "◎ Center", "right": "Right ▶"}
TICK_SECONDS = 0.8
MAX_TICKS = 12


class GatherStrikeView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        area_id: str,
        target_zone: str,
        *,
        active_zone: str = "center",
    ):
        super().__init__(timeout=30)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.area_id = area_id
        self.target_zone = target_zone
        self.active_zone = active_zone
        self.resolved = False
        self.message: discord.Message | None = None
        self._anim_task: asyncio.Task | None = None

        for zone in ZONE_POSITIONS:
            btn = discord.ui.Button(
                label=ZONE_LABELS[zone],
                style=discord.ButtonStyle.success if zone == active_zone else discord.ButtonStyle.secondary,
                custom_id=f"gather_{zone}",
            )
            btn.callback = self._make_strike_callback(zone)
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This gathering belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True

    def _timing_multiplier(self, zone: str) -> float:
        if zone == self.target_zone:
            return 1.35
        neighbors = {
            "left": "center",
            "center": None,
            "right": "center",
        }
        if neighbors.get(zone) == self.target_zone or neighbors.get(self.target_zone) == zone:
            return 1.1
        return 1.0

    def _make_strike_callback(self, zone: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.resolved:
                await interaction.response.send_message("The harvest is already done.", ephemeral=True)
                return
            self.resolved = True
            self.stop()
            if self._anim_task is not None:
                self._anim_task.cancel()
            mult = self._timing_multiplier(zone)
            await self._complete_gather(interaction, mult)

        return callback

    async def on_timeout(self) -> None:
        if self.resolved:
            return
        self.resolved = True
        if self._anim_task is not None:
            self._anim_task.cancel()
        await self._complete_gather(None, 1.0, timeout_note="The moment passes — you gather without the bonus strike.")

    def _update_button_styles(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            zone = (child.custom_id or "").removeprefix("gather_")
            child.style = (
                discord.ButtonStyle.success
                if zone == self.active_zone
                else discord.ButtonStyle.secondary
            )

    async def _animate(self) -> None:
        rng = random.Random()
        zones = list(ZONE_POSITIONS)
        try:
            for _ in range(MAX_TICKS):
                if self.resolved:
                    return
                await asyncio.sleep(TICK_SECONDS)
                if self.resolved:
                    return
                self.active_zone = rng.choice(zones)
                self._update_button_styles()
                if self.message is not None:
                    embed = self._build_prompt_embed(highlight=self.active_zone)
                    try:
                        await self.message.edit(embed=embed, view=self)
                    except discord.HTTPException:
                        return
        except asyncio.CancelledError:
            return

    def _build_prompt_embed(self, *, highlight: str) -> discord.Embed:
        lines = []
        for zone in ZONE_POSITIONS:
            pulse = "⚡" if zone == highlight else "　"
            lines.append(f"{pulse} {ZONE_LABELS[zone]}")
        return discord.Embed(
            title="Strike when the pulse gathers",
            description=(
                "A spirit pulse roams the field — strike the zone where it flashes for bonus yield.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.green(),
        )

    def _bonus_note(self, mult: float) -> str:
        if mult >= 1.3:
            return "\n\n**Perfect strike** — the land yields extra bounty."
        if mult >= 1.05:
            return "\n\n**Clean strike** — a modest bonus to your haul."
        return ""

    async def _complete_gather(
        self,
        interaction: discord.Interaction | None,
        mult: float,
        *,
        timeout_note: str | None = None,
    ) -> None:
        cfg = get_config()
        session = get_session()
        try:
            player = ensure_player(session, self.guild_id, self.owner_discord_id)
            if player is None:
                if interaction is not None:
                    await interaction.response.send_message("Character not found.", ephemeral=True)
                return

            now = utcnow()
            rng = rng_for(self.guild_id, self.owner_discord_id)
            res = run_gather(session, player, self.area_id, rng=rng, yield_multiplier=mult)
            if not res.success:
                if interaction is not None:
                    await interaction.response.send_message(res.messages[0], ephemeral=False)
                elif self.message is not None:
                    await self.message.edit(content=res.messages[0], embed=None, view=None)
                return

            player.last_gather_at = now
            player.last_active_at = now
            consume_haste_for_activity(session, player.id, "gather")
            schedule_player_reminders(session, player, cfg, "gather", now=now)
            session.add(player)
            session.commit()

            from ...inventory import get_item_name

            drop_lines = [f"**{get_item_name(item_id)}** ×{qty}" for item_id, qty in res.drops.items()]
            description = "\n".join(res.messages)
            if timeout_note:
                description = f"{timeout_note}\n\n{description}"
            description += self._bonus_note(mult)

            embed = discord.Embed(
                title=f"Gather — {res.area_name}",
                description=description,
                color=discord.Color.green(),
            )
            if drop_lines:
                embed.add_field(name="Collected", value="\n".join(drop_lines), inline=False)
            attach_guidance(embed, "gather", player, session, cfg, now, cooldown_remaining)

            if interaction is not None:
                await interaction.response.edit_message(embed=embed, view=None)
            elif self.message is not None:
                await self.message.edit(embed=embed, view=None)
        except Exception:
            logger.exception("Gather completion failed guild=%s user=%s", self.guild_id, self.owner_discord_id)
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.send_message(
                    "The harvest scattered — try **`/gather`** again.",
                    ephemeral=False,
                )
        finally:
            session.close()


async def start_gather_challenge(
    interaction: discord.Interaction,
    area_id: str,
) -> None:
    guild_id = get_guild_id(interaction)
    discord_id = get_discord_id(interaction.user)
    target_zone = random.choice(ZONE_POSITIONS)
    view = GatherStrikeView(discord_id, guild_id, area_id, target_zone)
    embed = view._build_prompt_embed(highlight=view.active_zone)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    view.message = await interaction.original_response()
    view._anim_task = asyncio.create_task(view._animate())
