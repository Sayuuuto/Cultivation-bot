from __future__ import annotations

import logging

import discord

logger = logging.getLogger("cultivation_bot")


class ConfirmActionView(discord.ui.View):
    """Reusable two-button confirmation view for destructive actions."""

    def __init__(
        self,
        owner_discord_id: str,
        *,
        title: str = "Confirm Action",
        description: str = "Are you sure you want to proceed?",
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        confirm_style: discord.ButtonStyle = discord.ButtonStyle.danger,
        cancel_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        timeout: int = 30,
        on_confirm=None,
        on_cancel=None,
    ):
        super().__init__(timeout=timeout)
        self.owner_discord_id = owner_discord_id
        self.title = title
        self.description = description
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label
        self.confirm_style = confirm_style
        self.cancel_style = cancel_style

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This action belongs to another daoist.",
                ephemeral=False,
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        if self.on_confirm:
            await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        embed = discord.Embed(
            title="Action Cancelled",
            description="You decide to hold your ground.",
            color=discord.Color.light_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        if self.on_cancel:
            await self.on_cancel(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if interaction_msg := getattr(self, "message", None):
            try:
                await interaction_msg.edit(
                    embed=discord.Embed(
                        title="Action Cancelled",
                        description="The window for this action has closed.",
                        color=discord.Color.light_grey(),
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass
