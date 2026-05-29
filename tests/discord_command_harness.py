"""Simulate Discord slash commands and component interactions for integration tests."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands
from sqlalchemy.orm import Session

# Test guild/user ids (match conftest cfg.guild_id when possible).
TEST_GUILD_ID = 986320746710183937
TEST_USER_ID = 900001001001001001
TEST_OPPONENT_ID = 900002002002002002

# Sentinel for kwargs discord.py treats as "not passed" (see discord.utils.MISSING).
_MISSING = object()


def validate_discord_message_kwargs(
    *,
    embed: Any = _MISSING,
    embeds: Any = _MISSING,
    file: Any = _MISSING,
    files: Any = _MISSING,
    attachments: Any = _MISSING,
) -> None:
    """Mirror discord.py webhook/message param validation used on real API calls."""
    if embed is not _MISSING and embeds is not _MISSING:
        raise TypeError("Cannot mix embed and embeds keyword arguments.")
    if file is not _MISSING and files is not _MISSING:
        raise TypeError("Cannot mix file and files keyword arguments.")
    if attachments is not _MISSING and file is not _MISSING:
        raise TypeError("Cannot mix attachments and file keyword arguments.")
    if attachments is not _MISSING and files is not _MISSING:
        raise TypeError("Cannot mix attachments and files keyword arguments.")


# 1x1 PNG
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass
class CapturedInteractionPayload:
    deferred: bool = False
    thinking: bool = False
    ephemeral: bool | None = None
    content: str | None = None
    embed: discord.Embed | None = None
    embeds: list[discord.Embed] = field(default_factory=list)
    view: discord.ui.View | None = None
    files: list[discord.File] = field(default_factory=list)
    edit: dict[str, Any] | None = None
    followup_messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        parts: list[str] = []
        if self.content:
            parts.append(self.content)
        if self.embed and self.embed.description:
            parts.append(self.embed.description)
        for emb in self.embeds:
            if emb.description:
                parts.append(emb.description)
        for msg in self.followup_messages:
            if msg.get("content"):
                parts.append(str(msg["content"]))
            emb = msg.get("embed")
            if isinstance(emb, discord.Embed) and emb.description:
                parts.append(emb.description)
        if self.edit:
            if self.edit.get("content"):
                parts.append(str(self.edit["content"]))
            emb = self.edit.get("embed")
            if isinstance(emb, discord.Embed) and emb.description:
                parts.append(emb.description)
        return "\n".join(parts)


class _MockResponse:
    def __init__(self, *, followup: _MockFollowup, captured: CapturedInteractionPayload):
        self._followup = followup
        self.captured = captured
        self._done = False

    @property
    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.captured.deferred = True
        self.captured.thinking = thinking
        self.captured.ephemeral = ephemeral
        self._done = True

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: Any = _MISSING,
        embeds: Any = _MISSING,
        view: discord.ui.View | None = None,
        file: Any = _MISSING,
        files: Any = _MISSING,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        validate_discord_message_kwargs(embed=embed, embeds=embeds, file=file, files=files)
        self.captured.content = content
        if embed is not _MISSING:
            self.captured.embed = embed
        if embeds is not _MISSING and embeds:
            self.captured.embeds = list(embeds)
        self.captured.view = view
        if file is not _MISSING and file is not None:
            self.captured.files.append(file)
        if files is not _MISSING and files:
            self.captured.files.extend(files)
        self.captured.ephemeral = ephemeral
        self._done = True
        _ = kwargs

    async def edit_message(
        self,
        *,
        content: str | None = None,
        embed: Any = _MISSING,
        embeds: Any = _MISSING,
        view: discord.ui.View | None = None,
        attachments: Any = _MISSING,
        file: Any = _MISSING,
        **kwargs: Any,
    ) -> None:
        validate_discord_message_kwargs(
            embed=embed,
            embeds=embeds,
            file=file,
            attachments=attachments,
        )
        edit_payload: dict[str, Any] = {"view": view, **kwargs}
        if content is not None:
            edit_payload["content"] = content
        if embed is not _MISSING:
            edit_payload["embed"] = embed
        if embeds is not _MISSING:
            edit_payload["embeds"] = list(embeds) if embeds else []
        if attachments is not _MISSING:
            edit_payload["attachments"] = attachments
        if file is not _MISSING:
            edit_payload["file"] = file
        self.captured.edit = edit_payload
        if view is not None:
            self.captured.view = view
        if embed is not _MISSING and embed is not None:
            self.captured.embed = embed
        if embeds is not _MISSING and embeds:
            self.captured.embeds = list(embeds)
        if content is not None:
            self.captured.content = content
        if file is not _MISSING and file is not None:
            self.captured.files.append(file)
        self._done = True


class _MockFollowup:
    def __init__(self, captured: CapturedInteractionPayload):
        self._captured = captured

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        file: discord.File | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> None:
        self._captured.followup_messages.append(
            {
                "content": content,
                "embed": embed,
                "view": view,
                "file": file,
                "ephemeral": ephemeral,
                **kwargs,
            }
        )
        if view is not None:
            self._captured.view = view
        if file is not None:
            self._captured.files.append(file)


def make_mock_user(
    *,
    user_id: int = TEST_USER_ID,
    name: str = "TestDaoist",
) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.display_name = name
    user.__str__ = lambda _self=None: name
    avatar = MagicMock()
    avatar.read = AsyncMock(return_value=_MINIMAL_PNG)
    avatar.url = "https://example.invalid/avatar.png"
    user.display_avatar = avatar
    return user


def make_mock_member(
    *,
    user_id: int = TEST_OPPONENT_ID,
    name: str = "RivalDaoist",
    guild_id: int = TEST_GUILD_ID,
) -> MagicMock:
    member = make_mock_user(user_id=user_id, name=name)
    member.guild_permissions = MagicMock()
    member.roles = []
    member.guild = MagicMock()
    member.guild.id = guild_id
    return member


def make_mock_interaction(
    *,
    user: Any | None = None,
    guild_id: int = TEST_GUILD_ID,
    client: Any = None,
) -> MagicMock:
    user = user or make_mock_user()
    guild = MagicMock()
    guild.id = guild_id
    guild.name = "Test Guild"
    dungeon_category = MagicMock()
    dungeon_category.name = "Dungeons"
    guild.categories = [dungeon_category]
    channel = MagicMock()
    channel.id = 888888888888888888
    channel.send = AsyncMock()

    captured = CapturedInteractionPayload()
    followup = _MockFollowup(captured)
    response = _MockResponse(followup=followup, captured=captured)

    interaction = MagicMock()
    interaction.user = user
    interaction.guild = guild
    interaction.channel = channel
    interaction.response = response
    interaction.followup = followup
    interaction.client = client
    interaction.id = 1
    interaction._captured = captured
    return interaction


def install_bot_db_patch(session: Session, monkeypatch: Any) -> None:
    """Route bot.get_session() to the pytest session; ignore close()."""
    monkeypatch.setattr(session, "close", lambda: None)

    def _test_session() -> Session:
        return session

    monkeypatch.setattr("src.db.get_session", _test_session)
    monkeypatch.setattr("src.bot.get_session", _test_session)
    monkeypatch.setattr("src.dungeon_discord.get_session", _test_session)
    monkeypatch.setattr("src.autocomplete_cache.get_session", _test_session)
    monkeypatch.setattr("src.combat.technique_ui.get_session", _test_session)


def install_discord_stubs(monkeypatch: Any) -> None:
    """Avoid real channel/DM provisioning during /start and profile avatar fetch."""

    async def _noop_provision(*args: Any, **kwargs: Any) -> Any:
        from src.discord_guild import AbodeProvisionResult

        return AbodeProvisionResult(channel=None, role=None)

    async def _avatar_read(self: Any) -> bytes:
        return _MINIMAL_PNG

    monkeypatch.setattr("src.discord_guild.provision_new_cultivator", _noop_provision)
    monkeypatch.setattr("src.bot.provision_new_cultivator", _noop_provision)

    async def _mock_dungeon_channel(*args: Any, **kwargs: Any) -> MagicMock:
        ch = MagicMock()
        ch.id = 777777777777777777
        ch.mention = "#dungeon-test"
        combat_msg = MagicMock()
        combat_msg.id = 888888888888888889
        ch.send = AsyncMock(return_value=combat_msg)
        return ch

    monkeypatch.setattr("src.dungeon_discord._create_dungeon_channel", _mock_dungeon_channel)

    async def _noop_post_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("src.dungeon_discord._post_new_log_lines", _noop_post_log)


def resolve_slash_command(tree: app_commands.CommandTree, qualified_name: str) -> app_commands.Command:
    parts = qualified_name.strip().split()
    if len(parts) == 1:
        cmd = tree.get_command(parts[0])
        if cmd is None:
            raise KeyError(f"Unknown command /{parts[0]}")
        if isinstance(cmd, app_commands.Group):
            raise KeyError(f"/{parts[0]} is a group; qualify subcommand")
        return cmd
    group = tree.get_command(parts[0])
    if group is None or not isinstance(group, app_commands.Group):
        raise KeyError(f"Unknown group /{parts[0]}")
    sub = group.get_command(parts[1])
    if sub is None:
        raise KeyError(f"Unknown subcommand /{parts[0]} {parts[1]}")
    return sub


def iter_slash_commands(tree: app_commands.CommandTree) -> list[tuple[str, app_commands.Command]]:
    found: list[tuple[str, app_commands.Command]] = []
    for cmd in tree.get_commands():
        if isinstance(cmd, app_commands.Group):
            for sub in cmd.commands:
                found.append((f"{cmd.name} {sub.name}", sub))
        else:
            found.append((cmd.name, cmd))
    return sorted(found, key=lambda row: row[0])


def assert_discord_view_valid(view: discord.ui.View | None, *, context: str = "") -> None:
    if view is None:
        return
    seen: set[str] = set()
    for row_idx, item in enumerate(view.children):
        custom_id = getattr(item, "custom_id", None)
        if not custom_id:
            continue
        assert custom_id not in seen, f"{context} duplicate custom_id {custom_id!r}"
        seen.add(custom_id)
        assert len(custom_id) <= 100, f"{context} custom_id too long"
    # Discord allows 5 action rows × 5 components; our combat views stay on one row.
    assert len(view.children) <= 25, f"{context} too many components ({len(view.children)})"


def assert_response_ok(
    captured: CapturedInteractionPayload,
    *,
    must_respond: bool = True,
    forbidden_substrings: list[str] | None = None,
    context: str = "",
) -> None:
    prefix = f"{context}: " if context else ""
    if must_respond:
        assert (
            captured.deferred
            or captured.content
            or captured.embed
            or captured.embeds
            or captured.files
            or captured.followup_messages
            or captured.edit
        ), f"{prefix}Command produced no Discord response"
    text = captured.text.lower()
    for bad in forbidden_substrings or ():
        assert bad.lower() not in text, (
            f"{prefix}Unexpected error text containing {bad!r}: {captured.text[:300]}"
        )
    assert "something went wrong" not in text, f"{prefix}{captured.text[:300]}"
    assert "check the bot logs" not in text, f"{prefix}{captured.text[:300]}"
    if captured.view is not None:
        assert_discord_view_valid(captured.view, context=context)


def _coerce_slash_kwargs(cmd: app_commands.Command, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Discord passes Choice objects; tests may pass raw values."""
    coerced = dict(kwargs)
    sig = inspect.signature(cmd.callback)
    for name, param in sig.parameters.items():
        if name not in coerced:
            continue
        value = coerced[name]
        if isinstance(value, app_commands.Choice):
            continue
        ann = param.annotation
        if ann is app_commands.Choice or getattr(ann, "__origin__", None) is app_commands.Choice:
            coerced[name] = app_commands.Choice(name=str(value), value=value)
    return coerced


async def invoke_slash(
    tree: app_commands.CommandTree,
    qualified_name: str,
    interaction: discord.Interaction,
    /,
    **kwargs: Any,
) -> CapturedInteractionPayload:
    cmd = resolve_slash_command(tree, qualified_name)
    bound_kwargs = _coerce_slash_kwargs(cmd, kwargs)
    sig = inspect.signature(cmd.callback)
    for name, param in sig.parameters.items():
        if name in bound_kwargs or name == "interaction":
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        raise TypeError(f"Missing required parameter {name!r} for /{qualified_name}")
    await cmd.callback(interaction, **bound_kwargs)
    return interaction._captured  # type: ignore[attr-defined]


async def click_view_button(
    view: discord.ui.View,
    interaction: discord.Interaction,
    *,
    custom_id_contains: str,
) -> CapturedInteractionPayload:
    for item in view.children:
        cid = getattr(item, "custom_id", None)
        if cid and custom_id_contains in cid:
            fresh = make_mock_interaction(
                user=interaction.user,
                guild_id=interaction.guild.id,  # type: ignore[union-attr]
                client=interaction.client,
            )
            await item.callback(fresh)  # type: ignore[misc]
            captured = fresh._captured  # type: ignore[attr-defined]
            assert_response_ok(captured, context=f"button id~{custom_id_contains!r}")
            return captured
    raise AssertionError(f"No button with custom_id containing {custom_id_contains!r}")


def _fresh_interaction_from(interaction: discord.Interaction) -> MagicMock:
    return make_mock_interaction(
        user=interaction.user,
        guild_id=interaction.guild.id,  # type: ignore[union-attr]
        client=interaction.client,
    )


async def click_view_button_label(
    view: discord.ui.View,
    interaction: discord.Interaction,
    *,
    label: str,
) -> CapturedInteractionPayload:
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.label == label:
            fresh = _fresh_interaction_from(interaction)
            await item.callback(fresh)  # type: ignore[misc]
            captured = fresh._captured  # type: ignore[attr-defined]
            assert_response_ok(captured, context=f"button {label!r}")
            return captured
    labels = [getattr(c, "label", None) for c in view.children if isinstance(c, discord.ui.Button)]
    raise AssertionError(f"No button with label {label!r}; have {labels!r}")


async def click_view_button_label_contains(
    view: discord.ui.View,
    interaction: discord.Interaction,
    *,
    substring: str,
) -> CapturedInteractionPayload:
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.label and substring in item.label:
            fresh = _fresh_interaction_from(interaction)
            await item.callback(fresh)  # type: ignore[misc]
            captured = fresh._captured  # type: ignore[attr-defined]
            assert_response_ok(captured, context=f"button containing {substring!r}")
            return captured
    labels = [getattr(c, "label", None) for c in view.children if isinstance(c, discord.ui.Button)]
    raise AssertionError(f"No button label containing {substring!r}; have {labels!r}")


async def select_view_option(
    view: discord.ui.View,
    interaction: discord.Interaction,
    *,
    option_value: str,
    select_index: int = 0,
) -> CapturedInteractionPayload:
    from unittest.mock import PropertyMock, patch

    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    if not selects:
        raise AssertionError("No select menu on view")
    item = selects[select_index]
    fresh = _fresh_interaction_from(interaction)
    with patch.object(type(item), "values", new_callable=PropertyMock, return_value=[option_value]):
        await item.callback(fresh)  # type: ignore[misc]
    captured = fresh._captured  # type: ignore[attr-defined]
    assert_response_ok(captured, context=f"select {option_value!r}")
    return captured


def view_from_capture(captured: CapturedInteractionPayload) -> discord.ui.View | None:
    """Active view after a slash command, button click, or select."""
    if captured.view is not None:
        return captured.view
    for msg in captured.followup_messages:
        if msg.get("view") is not None:
            return msg["view"]
    if captured.edit and captured.edit.get("view") is not None:
        return captured.edit["view"]
    return None


def enable_production_card_ui(monkeypatch: Any) -> None:
    """PNG skill cards + profile cards — default player-facing UI in production."""
    monkeypatch.setattr("src.combat.technique_ui.card_images_enabled", lambda: True)
    monkeypatch.setattr("src.combat.technique_ui.card_fonts_available", lambda: True)


def hub_view_from_captured(captured: CapturedInteractionPayload) -> discord.ui.View | None:
    return view_from_capture(captured)


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def prepare_ready_player(session: Session, player: Any, *, guild_id: str | None = None) -> None:
    """Clear timers and grant resources so activity commands succeed."""
    player.guild_id = guild_id or str(TEST_GUILD_ID)
    player.discord_id = str(TEST_USER_ID)
    player.novice_trial_step = 6
    player.realm_index = max(player.realm_index, 1)
    player.qi = max(player.qi, 80)
    player.spirit_stones = max(player.spirit_stones, 500)
    player.last_cultivate_at = None
    player.last_gather_at = None
    player.last_hunt_at = None
    player.last_adventure_at = None
    player.last_dungeon_at = None
    player.last_daily_at = None
    player.last_pvp_at = None
    session.add(player)
    session.flush()

    from src.combat.loadout import ensure_starter_techniques, learn_technique
    from src.inventory import add_item

    ensure_starter_techniques(session, player.id)
    learn_technique(session, player.id, "ember_palm")
    add_item(session, player.id, "qi_gathering_pill", 3)
    session.commit()
