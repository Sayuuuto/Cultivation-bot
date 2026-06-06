from __future__ import annotations

import logging
from typing import Callable, Awaitable

import discord
from sqlalchemy.orm import Session

from .models import Player
from .story_mode import (
    PendingStoryCreation,
    advance_pending,
    build_story_embed_fields,
    get_embed_color,
    get_node,
    get_path_def,
    get_pending,
    list_story_paths,
    node_choices,
    node_has_input,
    normalize_dao_name,
    pick_name_suggestions,
    resolve_next_node,
    validate_dao_name,
)

logger = logging.getLogger(__name__)

GENDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("male", "Man"),
    ("female", "Woman"),
    ("unspecified", "As I am"),
)


def build_story_embed(
    node: dict,
    *,
    dao_name: str = "",
    spirit_root: str = "",
    origin: str = "",
    path_name: str = "",
    chapter: int = 1,
) -> discord.Embed:
    fields = build_story_embed_fields(
        None,
        node,
        dao_name=dao_name,
        spirit_root=spirit_root,
        origin=origin,
        path_name=path_name,
    )
    r, g, b = get_embed_color()
    embed = discord.Embed(
        title=str(fields["title"]),
        description=str(fields["description"]),
        color=discord.Color.from_rgb(r, g, b),
    )
    if fields.get("footer"):
        embed.set_footer(text=str(fields["footer"]))
    return embed


class NameModal(discord.ui.Modal, title="Whisper your dao name"):
    def __init__(
        self,
        guild_id: str,
        discord_id: str,
        next_node: str,
        on_update: Callable[[discord.Interaction, PendingStoryCreation], Awaitable[None]],
    ):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.discord_id = discord_id
        self.next_node = next_node
        self.on_update = on_update
        self.dao_input = discord.ui.TextInput(
            label="How shall the world remember you?",
            placeholder="Write any name — 2 to 32 characters",
            min_length=2,
            max_length=32,
            required=True,
            style=discord.TextStyle.short,
        )
        self.add_item(self.dao_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pending = get_pending(self.guild_id, self.discord_id)
        if pending is None:
            await interaction.response.send_message(
                "Your awakening faded — use **`/start`** again.",
                ephemeral=True,
            )
            return
        err = validate_dao_name(str(self.dao_input.value))
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        pending.dao_name = normalize_dao_name(str(self.dao_input.value))
        advance_pending(pending, self.next_node)
        await self.on_update(interaction, pending)


class StoryView(discord.ui.View):
    def __init__(
        self,
        owner_discord_id: str,
        guild_id: str,
        *,
        chapter: int = 1,
        node_id: str,
        pending: PendingStoryCreation | None = None,
        player_id: int | None = None,
        dao_name: str = "",
        spirit_root: str = "",
        origin: str = "",
        path_name: str = "",
        on_player_update: Callable[[discord.Interaction, int, str], Awaitable[None]] | None = None,
        on_pending_update: Callable[[discord.Interaction, PendingStoryCreation], Awaitable[None]] | None = None,
        on_finalize: Callable[[discord.Interaction, PendingStoryCreation], Awaitable[None]] | None = None,
    ):
        super().__init__(timeout=300)
        self.owner_discord_id = owner_discord_id
        self.guild_id = guild_id
        self.chapter = chapter
        self.node_id = node_id
        self.pending = pending
        self.player_id = player_id
        self.dao_name = dao_name
        self.spirit_root = spirit_root
        self.origin = origin
        self.path_name = path_name
        self.on_player_update = on_player_update
        self.on_pending_update = on_pending_update
        self.on_finalize = on_finalize
        self._build_items()

    def _current_node(self) -> dict | None:
        return get_node(self.chapter, self.node_id)

    def _build_items(self) -> None:
        node = self._current_node()
        if node is None:
            return

        input_type = node_has_input(node)
        if input_type == "modal_name":
            suggestions = pick_name_suggestions(self.owner_discord_id, count=3)
            for suggested in suggestions:
                btn = discord.ui.Button(
                    label=suggested[:80],
                    style=discord.ButtonStyle.secondary,
                )
                btn.callback = self._make_name_suggestion_callback(suggested)
                self.add_item(btn)
            btn = discord.ui.Button(
                label="Write your own name",
                style=discord.ButtonStyle.primary,
                emoji="✍️",
            )
            btn.callback = self._name_callback
            self.add_item(btn)
            return

        if input_type == "gender":
            for gender_id, label in GENDER_OPTIONS:
                btn = discord.ui.Button(
                    label=label[:80],
                    style=discord.ButtonStyle.secondary,
                )
                btn.callback = self._make_gender_callback(gender_id)
                self.add_item(btn)
            return

        if input_type == "path":
            for path_id, name, _blurb in list_story_paths()[:4]:
                btn = discord.ui.Button(
                    label=name[:80],
                    style=discord.ButtonStyle.success,
                )
                btn.callback = self._make_path_callback(path_id)
                self.add_item(btn)
            return

        choices = node_choices(node)
        if choices:
            for choice in choices:
                btn = discord.ui.Button(
                    label=str(choice.get("label", "…"))[:80],
                    style=discord.ButtonStyle.primary,
                )
                btn.callback = self._make_choice_callback(str(choice.get("next", "")))
                self.add_item(btn)
            return

        nxt = resolve_next_node(node)
        if nxt and self.pending is not None:
            btn = discord.ui.Button(
                label="Continue",
                style=discord.ButtonStyle.primary,
            )
            btn.callback = self._make_pending_continue_callback(nxt)
            self.add_item(btn)
        elif nxt and self.player_id is not None and self.on_player_update:
            btn = discord.ui.Button(
                label="Continue",
                style=discord.ButtonStyle.primary,
            )
            btn.callback = self._make_player_continue_callback(nxt)
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This story belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True

    async def _name_callback(self, interaction: discord.Interaction) -> None:
        node = self._current_node()
        if node is None or self.pending is None:
            await interaction.response.send_message("The moment passes — use **`/start`** again.", ephemeral=True)
            return
        nxt = resolve_next_node(node) or "name_confirmed"
        modal = NameModal(
            self.guild_id,
            self.owner_discord_id,
            nxt,
            self.on_pending_update or self._default_pending_update,
        )
        await interaction.response.send_modal(modal)

    def _make_name_suggestion_callback(self, suggested_name: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending is None:
                await interaction.response.send_message("Use **`/start`** again.", ephemeral=True)
                return
            node = self._current_node()
            if node is None:
                return
            err = validate_dao_name(suggested_name)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
            self.pending.dao_name = normalize_dao_name(suggested_name)
            nxt = resolve_next_node(node) or "name_confirmed"
            advance_pending(self.pending, nxt)
            handler = self.on_pending_update or self._default_pending_update
            await handler(interaction, self.pending)

        return callback

    def _make_gender_callback(self, gender_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending is None:
                await interaction.response.send_message("Use **`/start`** again.", ephemeral=True)
                return
            node = self._current_node()
            if node is None:
                return
            self.pending.gender = gender_id
            nxt = resolve_next_node(node) or "memory_flavor"
            advance_pending(self.pending, nxt)
            handler = self.on_pending_update or self._default_pending_update
            await handler(interaction, self.pending)

        return callback

    def _make_path_callback(self, path_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending is None:
                await interaction.response.send_message("Use **`/start`** again.", ephemeral=True)
                return
            node = self._current_node()
            if node is None:
                return
            self.pending.story_path = path_id
            nxt = resolve_next_node(node) or "spirit_reveal"
            advance_pending(self.pending, nxt)
            # Defer first so on_finalize can send followups; then disable buttons.
            await interaction.response.defer()
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            try:
                await interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass
            if self.on_finalize and nxt == "spirit_reveal":
                await self.on_finalize(interaction, self.pending)
            elif self.on_pending_update:
                await self.on_pending_update(interaction, self.pending)

        return callback

    def _make_choice_callback(self, next_node: str):
        async def callback(interaction: discord.Interaction) -> None:
            if not next_node:
                await interaction.response.defer()
                return
            if self.pending is not None:
                advance_pending(self.pending, next_node)
                handler = self.on_pending_update or self._default_pending_update
                await handler(interaction, self.pending)
            elif self.player_id is not None and self.on_player_update:
                await interaction.response.defer()
                await self.on_player_update(interaction, self.player_id, next_node)

        return callback

    def _make_pending_continue_callback(self, next_node: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.pending is None:
                return
            advance_pending(self.pending, next_node)
            if self.on_finalize and next_node == "spirit_reveal":
                await self.on_finalize(interaction, self.pending)
            elif self.on_pending_update:
                await self.on_pending_update(interaction, self.pending)
            else:
                await self._default_pending_update(interaction, self.pending)

        return callback

    def _make_player_continue_callback(self, next_node: str):
        async def callback(interaction: discord.Interaction) -> None:
            if self.on_player_update and self.player_id is not None:
                await interaction.response.defer()
                await self.on_player_update(interaction, self.player_id, next_node)

        return callback

    async def _default_pending_update(
        self, interaction: discord.Interaction, pending: PendingStoryCreation
    ) -> None:
        node = get_node(1, pending.node_id)
        if node is None:
            await interaction.response.send_message("The story falters — use **`/start`** again.", ephemeral=True)
            return
        path_name = ""
        if pending.story_path:
            path_def = get_path_def(pending.story_path)
            if path_def:
                path_name = str(path_def.get("name", ""))
        embed = build_story_embed(node, chapter=1, dao_name=pending.dao_name, path_name=path_name)
        view = StoryView(
            self.owner_discord_id,
            self.guild_id,
            chapter=1,
            node_id=pending.node_id,
            pending=pending,
            dao_name=pending.dao_name,
            path_name=path_name,
            on_pending_update=self.on_pending_update,
            on_finalize=self.on_finalize,
        )
        await interaction.response.edit_message(embed=embed, view=view)


async def send_story_node_for_pending(
    interaction: discord.Interaction,
    pending: PendingStoryCreation,
    *,
    edit: bool = False,
    on_pending_update,
    on_finalize,
) -> None:
    node = get_node(1, pending.node_id)
    if node is None:
        msg = "The story falters — use **`/start`** again."
        if edit:
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        else:
            await interaction.followup.send(msg, ephemeral=False)
        return

    path_name = ""
    if pending.story_path:
        path_def = get_path_def(pending.story_path)
        if path_def:
            path_name = str(path_def.get("name", ""))

    embed = build_story_embed(
        node,
        chapter=1,
        dao_name=pending.dao_name,
        path_name=path_name,
    )
    view = StoryView(
        str(interaction.user.id),
        pending.guild_id,
        chapter=1,
        node_id=pending.node_id,
        pending=pending,
        dao_name=pending.dao_name,
        path_name=path_name,
        on_pending_update=on_pending_update,
        on_finalize=on_finalize,
    )
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)


async def send_story_node_for_player(
    interaction: discord.Interaction,
    session: Session,
    player: Player,
    *,
    edit: bool = False,
    on_player_update=None,
) -> None:
    from .story_mode import player_story_chapter, player_story_step

    chapter = player_story_chapter(player)
    step = player_story_step(player)
    node = get_node(chapter, step)
    if node is None:
        msg = "Your story continues on the path — check **`/profile`** for your next step."
        if edit and not interaction.response.is_done():
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        elif edit:
            await interaction.edit_original_response(content=msg, embed=None, view=None)
        else:
            await interaction.followup.send(msg, ephemeral=False)
        return

    path_name = ""
    if player.story_path:
        path_def = get_path_def(player.story_path)
        if path_def:
            path_name = str(path_def.get("name", ""))

    embed = build_story_embed(
        node,
        chapter=chapter,
        dao_name=player.dao_name,
        spirit_root=player.spirit_root,
        origin=player.origin,
        path_name=path_name,
    )
    view = None
    if node_choices(node) or (
        resolve_next_node(node)
        and step not in ("complete", "chapter1_complete")
        and not node.get("await_command")
        and not node_has_input(node)
    ):
        view = StoryView(
            str(interaction.user.id),
            player.guild_id,
            chapter=chapter,
            node_id=step,
            player_id=player.id,
            dao_name=player.dao_name,
            spirit_root=player.spirit_root,
            origin=player.origin,
            path_name=path_name,
            on_player_update=on_player_update,
        )

    if edit and not interaction.response.is_done():
        await interaction.response.edit_message(embed=embed, view=view)
    elif edit:
        await interaction.edit_original_response(embed=embed, view=view)
    elif interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        kwargs = {"embed": embed}
        if view is not None:
            kwargs["view"] = view
        await interaction.followup.send(**kwargs)
