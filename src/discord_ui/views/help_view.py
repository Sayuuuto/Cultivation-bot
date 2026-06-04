from __future__ import annotations

import discord

from ...guidance import HELP_CATEGORY_BODIES, HELP_CATEGORY_LABELS, build_help_embed

HELP_OVERVIEW_ID = "__overview__"
_OVERVIEW_LABEL = "Overview"

CATEGORY_EMOJI: dict[str, str | None] = {
    "getting_started": "\U0001F4DA",
    "cultivation": "\u2728",
    "exploration": "\U0001F3D4\uFE0F",
    "dungeons_gear": "\U0001F5E1\uFE0F",
    "techniques": "\U0001F4DC",
    "economy": "\U0001FA99",
    "social_pvp": "\U0001F91D",
}

BUTTON_LABELS: dict[str, str] = {
    "getting_started": "Getting Started",
    "cultivation": "Cultivation",
    "exploration": "Exploration",
    "dungeons_gear": "Dungeons & Gear",
    "techniques": "Techniques",
    "economy": "Economy",
    "social_pvp": "Social & PvP",
}

BUTTON_STYLES: dict[str, discord.ButtonStyle] = {
    "getting_started": discord.ButtonStyle.blurple,
    "cultivation": discord.ButtonStyle.green,
    "exploration": discord.ButtonStyle.blurple,
    "dungeons_gear": discord.ButtonStyle.grey,
    "techniques": discord.ButtonStyle.blurple,
    "economy": discord.ButtonStyle.grey,
    "social_pvp": discord.ButtonStyle.green,
}


class HelpView(discord.ui.View):
    """Interactive help with category buttons and an Overview button.

    Calling code must send the embed with *view=self* on the initial response.
    """

    def __init__(self) -> None:
        super().__init__(timeout=300)
        self._current_category: str | None = None
        self._build_buttons()

    def _build_buttons(self) -> None:
        for cid in BUTTON_LABELS:
            label = BUTTON_LABELS[cid]
            style = BUTTON_STYLES.get(cid, discord.ButtonStyle.secondary)
            emoji = CATEGORY_EMOJI.get(cid)
            btn = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"help_cat:{cid}",
                emoji=emoji,
            )
            btn.callback = self._make_callback(cid)
            self.add_item(btn)

    def _make_callback(self, category_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self._show_category(interaction, category_id)
        return callback

    async def _show_category(self, interaction: discord.Interaction, category_id: str) -> None:
        embed = build_help_embed(category_id)
        self._current_category = category_id
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label=_OVERVIEW_LABEL, style=discord.ButtonStyle.primary, custom_id="help_overview", row=2)
    async def _overview_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        embed = build_help_embed()
        self._current_category = None
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        self.clear_items()
        self.add_item(discord.ui.Button(label="Help expired — run /help again", style=discord.ButtonStyle.secondary, disabled=True, custom_id="help_expired"))
