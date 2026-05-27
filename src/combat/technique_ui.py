from __future__ import annotations

import discord
from sqlalchemy.orm import Session

from ..combat.learn import learn_technique_from_manual
from ..combat.loadout import equip_technique
from ..command_choices import can_bind_technique_manual, list_player_manuals, list_technique_equip_options
from ..db import get_session
from ..manuals import craft_manual_from_fragments
from ..models import Player


class ManualStudySelect(discord.ui.Select):
    def __init__(self, session: Session, player: Player):
        manuals = list_player_manuals(session, player.id)
        options = [
            discord.SelectOption(label=label[:100], value=item_id)
            for item_id, label in manuals[:25]
        ]
        super().__init__(
            placeholder="Study a manual from your bag…",
            options=options,
            min_values=1,
            max_values=1,
            disabled=not options,
        )
        self._player_id = player.id

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            manual_id = self.values[0]
            ok, message = learn_technique_from_manual(session, self._player_id, manual_id)
            if not ok:
                await interaction.response.send_message(message, ephemeral=True)
                return
            session.commit()
            await interaction.response.send_message(message, ephemeral=True)
        finally:
            session.close()


class TechniqueEquipSelect(discord.ui.Select):
    def __init__(self, session: Session, player: Player):
        equip_options = list_technique_equip_options(session, player)
        options = [
            discord.SelectOption(label=label[:100], value=value)
            for value, label in equip_options
        ]
        super().__init__(
            placeholder="Equip a learned technique…",
            options=options,
            min_values=1,
            max_values=1,
            disabled=not options,
        )
        self._player_id = player.id

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            raw = self.values[0]
            technique_id, slot = raw.split("|", 1)
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            ok, message = equip_technique(session, player, technique_id, slot)
            if not ok:
                await interaction.response.send_message(message, ephemeral=True)
                return
            session.commit()
            await interaction.response.send_message(message, ephemeral=True)
        finally:
            session.close()


class BindManualButton(discord.ui.Button):
    def __init__(self, player_id: int):
        super().__init__(
            label="Bind Manual",
            style=discord.ButtonStyle.secondary,
        )
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            if not can_bind_technique_manual(session, player.id):
                from ..drop_sources import format_missing_materials_message
                from ..manuals import MANUAL_CRAFT_INPUTS

                await interaction.response.send_message(
                    format_missing_materials_message(
                        session, player.id, MANUAL_CRAFT_INPUTS, action="manual"
                    ),
                    ephemeral=True,
                )
                return
            import random

            res = craft_manual_from_fragments(session, player, rng=random.Random())
            if not res.success:
                await interaction.response.send_message(res.message, ephemeral=True)
                return
            session.commit()
            await interaction.response.send_message(res.message, ephemeral=True)
        finally:
            session.close()


class TechniquesView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        session: Session,
        player: Player,
        *,
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)
        self.owner_discord_id = owner_discord_id
        if list_player_manuals(session, player.id):
            self.add_item(ManualStudySelect(session, player))
        if list_technique_equip_options(session, player):
            self.add_item(TechniqueEquipSelect(session, player))
        bind_btn = BindManualButton(player.id)
        self.add_item(bind_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This martial ledger belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True
