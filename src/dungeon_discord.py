from __future__ import annotations

import logging
import random

import discord
from discord import app_commands

from .combat.loadout import get_equipped_active_techniques
from .config import Config, get_config
from .cooperative_dungeons import get_cooperative_dungeon, get_cooperative_dungeons
from .db import get_session
from .dungeon_arena import (
    build_dungeon_combat_embed,
    dungeon_channel_slug,
    format_new_log_lines,
    resolve_dungeon_category,
)
from .dungeon_combat import (
    advance_to_next_room,
    load_combat_state,
    pass_turn,
    process_turn_start,
    save_combat_state,
    select_target,
    select_technique,
    should_advance_room,
    start_room_combat,
)
from .dungeon_party import (
    PARTY_LOBBY_TIMEOUT_SECONDS,
    accept_invite,
    apply_dungeon_rewards,
    can_start_party,
    cancel_party_for_player,
    cancel_party,
    create_party_with_invites,
    expire_stale_dungeon_parties,
    format_invite_embed_description,
    load_members,
    member_discord_ids,
    party_ready_to_launch,
    roll_party_rewards,
)
from .game import utcnow
from .inventory import get_item_name
from .models import ActiveDungeonParty, Player
from .ui.formatting import technique_button_emoji

logger = logging.getLogger(__name__)

NOT_STARTED_HINT = "Begin your path with **`/start`** first."
# Ephemeral only for denial toasts; combat buttons live on the pinned channel card.
COMBAT_EPHEMERAL = False
COMBAT_DENY_EPHEMERAL = True


async def coop_dungeon_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    from .command_choices import filter_options
    from .realms import REALMS

    options: list[tuple[str, str]] = []
    for dungeon_id, dungeon in get_cooperative_dungeons().items():
        realm = REALMS[min(dungeon.realm_index, len(REALMS) - 1)]
        label = f"{dungeon.name} ({realm})"
        options.append((dungeon_id, label))
    return [
        app_commands.Choice(name=label[:100], value=value)
        for value, label in filter_options(options, current)
    ]


class DungeonInviteView(discord.ui.View):
    def __init__(self, party_id: int, invited_discord_ids: set[str]):
        super().__init__(timeout=PARTY_LOBBY_TIMEOUT_SECONDS)
        self.party_id = party_id
        self.invited_discord_ids = invited_discord_ids
        self.message: discord.Message | None = None

    def _disable(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _edit(self, interaction: discord.Interaction, embed: discord.Embed) -> None:
        self._disable()
        target = interaction.message if interaction.message is not None else self.message
        if target is not None:
            await target.edit(embed=embed, view=self)

    async def on_timeout(self) -> None:
        from .dungeon_arena import build_dungeon_cancelled_embed

        session = get_session()
        try:
            party = session.get(ActiveDungeonParty, self.party_id)
            if party is None or party.status != "lobby":
                return
            cancel_party(party)
            session.add(party)
            session.commit()
            embed = build_dungeon_cancelled_embed(
                reason=(
                    "No ally accepted in time — the expedition dissolves. "
                    "Run **`/dungeon`** again when your party is ready."
                ),
            )
            self._disable()
            if self.message is not None:
                try:
                    await self.message.edit(embed=embed, view=self)
                except discord.HTTPException:
                    pass
        finally:
            session.close()

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from .bot import ensure_player, get_discord_id, get_guild_id, rng_for

        actor_id = get_discord_id(interaction.user)
        if actor_id not in self.invited_discord_ids:
            await interaction.response.send_message(
                "Only invited daoists may accept this expedition.",
                ephemeral=False,
            )
            return

        await interaction.response.defer(ephemeral=False)
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            party = session.get(ActiveDungeonParty, self.party_id)
            if party is None or party.status != "lobby":
                await interaction.followup.send("This expedition is no longer open.", ephemeral=False)
                return

            player = ensure_player(session, guild_id, actor_id)
            if player is None:
                await interaction.followup.send(NOT_STARTED_HINT, ephemeral=False)
                return

            accepted, msg = accept_invite(session, party, player)
            if not accepted:
                await interaction.followup.send(msg, ephemeral=False)
                return

            session.add(party)
            session.commit()

            embed = discord.Embed(
                title="Dungeon Expedition",
                description=format_invite_embed_description(party),
                color=discord.Color.blurple(),
            )

            if party_ready_to_launch(party):
                await self._edit(interaction, embed)
                err = await _launch_dungeon(
                    interaction,
                    party,
                    session,
                    get_config(),
                    rng_for(guild_id, party.leader_discord_id),
                )
                if err:
                    await interaction.followup.send(err, ephemeral=False)
                return

            await self._edit(interaction, embed)
            await interaction.followup.send(msg, ephemeral=False)
        finally:
            session.close()


class DungeonCombatView(discord.ui.View):
    def __init__(
        self,
        party_id: int,
        actor_discord_id: str,
        participant_ids: set[str],
        *,
        techniques: list | None = None,
        technique_cooldowns: dict[str, int] | None = None,
        enemies: list | None = None,
        pending_technique: str | None = None,
        player_sealed: bool = False,
    ):
        super().__init__(timeout=900)
        self.party_id = party_id
        self.actor_discord_id = actor_discord_id
        self.participant_ids = participant_ids
        self.message: discord.Message | None = None

        if pending_technique and enemies:
            for enemy in enemies:
                if not enemy.alive():
                    continue
                btn = discord.ui.Button(
                    label=f"🎯 {enemy.name}"[:80],
                    style=discord.ButtonStyle.danger,
                )
                btn.callback = self._make_target_callback(enemy.fighter_id)
                self.add_item(btn)
            cancel = discord.ui.Button(
                label="↩ Cancel",
                style=discord.ButtonStyle.secondary,
            )
            cancel.callback = self._make_cancel_callback()
            self.add_item(cancel)
        else:
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
                style = (
                    discord.ButtonStyle.primary
                    if tech.technique_id == "basic_strike"
                    else discord.ButtonStyle.danger
                )
                if cd > 0 or sealed_blocked:
                    style = discord.ButtonStyle.secondary
                button = discord.ui.Button(
                    label=label[:80],
                    style=style,
                    disabled=cd > 0 or sealed_blocked,
                )
                button.callback = self._make_technique_callback(tech.technique_id)
                self.add_item(button)
            pass_btn = discord.ui.Button(
                label="⏭ Pass Turn",
                style=discord.ButtonStyle.secondary,
            )
            pass_btn.callback = self._make_pass_callback()
            self.add_item(pass_btn)

    def _make_technique_callback(self, technique_id: str):
        async def callback(interaction: discord.Interaction):
            await _handle_dungeon_technique(interaction, self.party_id, technique_id)

        return callback

    def _make_target_callback(self, target_id: str):
        async def callback(interaction: discord.Interaction):
            await _handle_dungeon_target(interaction, self.party_id, target_id)

        return callback

    def _make_pass_callback(self):
        async def callback(interaction: discord.Interaction):
            await _handle_dungeon_pass(interaction, self.party_id)

        return callback

    def _make_cancel_callback(self):
        async def callback(interaction: discord.Interaction):
            await _handle_dungeon_cancel_target(interaction, self.party_id)

        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from .bot import get_discord_id

        uid = get_discord_id(interaction.user)
        if uid not in self.participant_ids:
            await interaction.response.send_message(
                "This expedition is not yours.", ephemeral=COMBAT_DENY_EPHEMERAL
            )
            return False
        if uid != self.actor_discord_id:
            await interaction.response.send_message(
                "Wait for your turn.", ephemeral=COMBAT_DENY_EPHEMERAL
            )
            return False
        return True

    async def on_timeout(self) -> None:
        from .dungeon_arena import build_dungeon_cancelled_embed

        for item in self.children:
            item.disabled = True
        session = get_session()
        try:
            party = session.get(ActiveDungeonParty, self.party_id)
            if party is None or party.status != "in_combat":
                return
            cancel_party(party)
            session.add(party)
            session.commit()
            embed = build_dungeon_cancelled_embed(
                reason=(
                    "No one acted in time — the expedition ends. "
                    "Run **`/dungeon`** again when your party is ready."
                ),
            )
            if getattr(self, "message", None) is not None:
                try:
                    await self.message.edit(embed=embed, view=self)
                except discord.HTTPException:
                    pass
        finally:
            session.close()


async def _create_dungeon_channel(
    guild: discord.Guild,
    party: ActiveDungeonParty,
    members: list,
    *,
    category_id: str | None,
) -> discord.TextChannel | None:
    from .dungeon_arena import _party_overwrites

    dungeon = get_cooperative_dungeon(party.dungeon_id)
    leader_name = next((m.dao_name for m in members if m.discord_id == party.leader_discord_id), "leader")
    slug = dungeon_channel_slug(dungeon.name if dungeon else party.dungeon_id, leader_name)
    member_ids = [int(m.discord_id) for m in members]
    overwrites = _party_overwrites(guild, member_ids)
    category = await resolve_dungeon_category(guild, category_id)
    try:
        return await guild.create_text_channel(
            slug,
            category=category,
            overwrites=overwrites,
            reason="Cooperative dungeon expedition",
            topic=(
                f"{dungeon.name if dungeon else 'Dungeon'} — party combat. "
                "Only invited daoists can see this channel."
            ),
        )
    except discord.HTTPException:
        logger.exception("Failed to create dungeon channel guild=%s party=%s", guild.id, party.id)
        return None


async def _launch_dungeon(
    interaction: discord.Interaction,
    party: ActiveDungeonParty,
    session,
    cfg: Config,
    rng: random.Random,
) -> str | None:
    ok, err = can_start_party(party)
    if not ok:
        return err
    if interaction.guild is None:
        return "This command must be used in a server."

    dungeon = get_cooperative_dungeon(party.dungeon_id)
    if dungeon is None:
        return "Dungeon configuration missing."

    members = load_members(party)
    channel = await _create_dungeon_channel(
        interaction.guild,
        party,
        members,
        category_id=cfg.dungeon_category_id,
    )
    if channel is None:
        return "Could not open a dungeon channel — check bot permissions."

    state = start_room_combat(
        session,
        party_id=party.id,
        dungeon=dungeon,
        room_index=0,
        members=members,
        rng=rng,
    )
    process_turn_start(session, state, rng)
    party.status = "in_combat"
    party.channel_id = str(channel.id)
    party.room_index = 0
    save_combat_state(party, state)
    session.add(party)
    session.commit()

    opening_log = format_new_log_lines(state)
    if opening_log:
        await _send_log_chunks(channel, opening_log)
        state.log_cursor = len(state.log)
        save_combat_state(party, state)

    embed = build_dungeon_combat_embed(state)
    launch_view = _combat_message_view(party.id, state)
    combat_msg = await channel.send(embed=embed, view=launch_view)
    party.combat_message_id = str(combat_msg.id)
    save_combat_state(party, state)
    session.add(party)
    session.commit()

    if interaction.channel:
        await interaction.channel.send(
            f"⚔️ **{dungeon.name}** begins — {channel.mention} "
            f"({len(members)} daoists)."
        )
    return None


async def _get_dungeon_channel_by_id(client, channel_id: str | None):
    if not channel_id:
        return None
    channel = client.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await client.fetch_channel(int(channel_id))
        except discord.HTTPException:
            return None
    return channel


async def _get_dungeon_channel(client, party: ActiveDungeonParty):
    return await _get_dungeon_channel_by_id(client, party.channel_id)


def _chunk_log_text(text: str) -> list[str]:
    chunks: list[str] = []
    while text:
        chunk = text[:1900]
        if len(text) > 1900:
            split = chunk.rfind("\n")
            if split > 0:
                chunk = text[:split]
        chunks.append(chunk)
        text = text[len(chunk) :].lstrip("\n")
    return chunks


async def _send_log_chunks(channel, text: str) -> None:
    for chunk in _chunk_log_text(text):
        await channel.send(chunk)


async def _post_new_log_lines(client, channel_id: str | None, state) -> None:
    """Fallback: append log as new messages (used if panel conversion fails)."""
    text = format_new_log_lines(state)
    if not text:
        return
    channel = await _get_dungeon_channel_by_id(client, channel_id)
    if channel is None:
        return
    await _send_log_chunks(channel, text)
    state.log_cursor = len(state.log)


async def _convert_panel_to_log(channel, combat_message_id: str, state) -> bool:
    """Turn the live combat panel message into plain log history."""
    text = format_new_log_lines(state)
    if not text:
        return False
    try:
        msg = await channel.fetch_message(int(combat_message_id))
        chunks = _chunk_log_text(text)
        await msg.edit(content=chunks[0], embed=None, view=None)
        for extra in chunks[1:]:
            await channel.send(extra)
        state.log_cursor = len(state.log)
        return True
    except discord.HTTPException:
        logger.exception("Failed to convert dungeon panel to log msg=%s", combat_message_id)
        return False


def _build_combat_panel_view(party_id: int, state) -> DungeonCombatView | None:
    actor = state.current_actor()
    if state.finished or actor is None or actor.is_enemy or not actor.player_id:
        return None
    session = get_session()
    try:
        party = session.get(ActiveDungeonParty, party_id)
        if party is None:
            return None
        techniques = get_equipped_active_techniques(session, actor.player_id)
        return DungeonCombatView(
            party_id,
            actor.fighter_id,
            member_discord_ids(party),
            techniques=techniques,
            technique_cooldowns=actor.technique_cooldowns,
            enemies=state.living_enemies(),
            pending_technique=state.pending_technique,
            player_sealed=actor.combatant.sealed,
        )
    finally:
        session.close()


def _combat_message_view(party_id: int, state) -> DungeonCombatView | None:
    """Technique/target buttons on the live combat panel (always the channel's last message)."""
    return _build_combat_panel_view(party_id, state)


async def _after_combat_action(session, party, state, rng, cfg) -> object:
    """Advance rooms, mark defeat, or complete run after any combat action."""
    if should_advance_room(state):
        await _handle_room_cleared(session, party, state, rng, cfg)
        loaded = load_combat_state(party)
        if loaded is not None:
            state = loaded
    if state.run_complete or (state.finished and not state.victory):
        if state.finished and not state.victory:
            party.status = "completed"
        session.add(party)
        session.commit()
    return state


async def _sync_combat_ui(
    interaction: discord.Interaction | None,
    party_id: int,
    state,
    rng: random.Random,
) -> None:
    if state is None:
        return

    client = interaction.client if interaction else None
    if client is None:
        return

    session = get_session()
    try:
        party = session.get(ActiveDungeonParty, party_id)
        if party is None:
            return
        loaded = load_combat_state(party)
        if loaded is not None:
            state = loaded
        if should_advance_room(state):
            await _handle_room_cleared(session, party, state, rng, get_config())
            loaded = load_combat_state(party)
            if loaded is not None:
                state = loaded
        if not state.finished:
            actor = state.current_actor()
            if actor and actor.is_enemy:
                process_turn_start(session, state, rng)
        channel_id = party.channel_id
        combat_message_id = party.combat_message_id
        pending_log = format_new_log_lines(state)

        channel = await _get_dungeon_channel_by_id(client, channel_id)
        if channel is None:
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            return

        panel_converted = False
        if combat_message_id and pending_log:
            panel_converted = await _convert_panel_to_log(channel, combat_message_id, state)
            if not panel_converted:
                await _post_new_log_lines(client, channel_id, state)
            combat_message_id = None

        actor = state.current_actor()
        embed = build_dungeon_combat_embed(state)

        if state.finished:
            try:
                final_msg = await channel.send(embed=embed, view=None)
                party.combat_message_id = str(final_msg.id)
            except discord.HTTPException:
                logger.exception("Failed to post dungeon outcome party=%s", party_id)
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            return

        if (
            interaction is not None
            and actor
            and not actor.is_enemy
            and actor.fighter_id
        ):
            from .bot import get_discord_id

            if get_discord_id(interaction.user) != actor.fighter_id:
                try:
                    await channel.send(
                        f"<@{actor.fighter_id}> — your turn. "
                        "Use the buttons on the combat card below."
                    )
                except discord.HTTPException:
                    pass

        combat_view: discord.ui.View | None = None
        if actor and not actor.is_enemy and actor.fighter_id:
            combat_view = _combat_message_view(party_id, state)

        try:
            if panel_converted or not combat_message_id:
                panel_msg = await channel.send(embed=embed, view=combat_view)
                party.combat_message_id = str(panel_msg.id)
            else:
                msg = await channel.fetch_message(int(combat_message_id))
                await msg.edit(embed=embed, view=combat_view)
        except discord.HTTPException:
            logger.exception("Failed to refresh dungeon combat panel party=%s", party_id)

        state.log_cursor = len(state.log)
        save_combat_state(party, state)
        session.add(party)
        session.commit()
    finally:
        session.close()


async def _complete_dungeon(session, party, state, rng, cfg) -> str:
    from .cooldown_haste import consume_haste_for_activity
    from .reminders import schedule_after_activity

    members = load_members(party)
    drops = roll_party_rewards(
        party.dungeon_id,
        rng,
        session=session,
        members=members,
        pending_loot=state.pending_loot,
    )
    apply_dungeon_rewards(session, members, drops)
    now = utcnow()
    lines = ["🏆 **Dungeon conquered!** Rewards shared among the party:"]
    for item_id, qty in drops.items():
        lines.append(f"• **{get_item_name(item_id)}** ×{qty}")
    for member in members:
        player = session.get(Player, member.player_id)
        if player is None:
            continue
        player.last_dungeon_at = now
        player.last_active_at = now
        consume_haste_for_activity(session, player.id, "dungeon")
        schedule_after_activity(session, player, cfg, "dungeon", now)
        from .game_sects import on_sect_activity

        on_sect_activity(session, player, "dungeon")
        session.add(player)
    party.status = "completed"
    state.run_complete = True
    state.finished = True
    state.victory = True
    state.log.extend(lines)
    save_combat_state(party, state)
    session.add(party)
    return "\n".join(lines)


async def _handle_room_cleared(session, party, state, rng, cfg) -> None:
    members = load_members(party)
    dungeon = get_cooperative_dungeon(state.dungeon_id)
    if dungeon is None:
        return
    if state.room_index + 1 >= len(dungeon.rooms):
        await _complete_dungeon(session, party, state, rng, cfg)
        return
    new_state = advance_to_next_room(session, state, members, rng)
    party.room_index = new_state.room_index
    save_combat_state(party, new_state)
    session.add(party)


async def _handle_dungeon_technique(interaction: discord.Interaction, party_id: int, technique_id: str):
    from .bot import get_discord_id, get_guild_id, rng_for

    await interaction.response.defer(ephemeral=COMBAT_EPHEMERAL)
    cfg = get_config()
    session = get_session()
    try:
        party = session.get(ActiveDungeonParty, party_id)
        if party is None or party.status != "in_combat":
            await interaction.followup.send(
                "This expedition is not active.", ephemeral=COMBAT_EPHEMERAL
            )
            return
        state = load_combat_state(party)
        if state is None:
            return
        rng = rng_for(get_guild_id(interaction), get_discord_id(interaction.user))
        res = select_technique(session, state, get_discord_id(interaction.user), technique_id, rng=rng)
        if not res.ok:
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            if res.message:
                await interaction.followup.send(res.message, ephemeral=COMBAT_EPHEMERAL)
            return
        if res.needs_target:
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            await _sync_combat_ui(interaction, party_id, state, rng)
            return
        state = await _after_combat_action(session, party, state, rng, cfg)
        save_combat_state(party, state)
        session.add(party)
        session.commit()
        await _sync_combat_ui(interaction, party_id, state, rng)
    finally:
        session.close()


async def _handle_dungeon_target(interaction: discord.Interaction, party_id: int, target_id: str):
    from .bot import get_discord_id, get_guild_id, rng_for

    await interaction.response.defer(ephemeral=COMBAT_EPHEMERAL)
    cfg = get_config()
    session = get_session()
    try:
        guild_id = get_guild_id(interaction)
        discord_id = get_discord_id(interaction.user)
        party = session.get(ActiveDungeonParty, party_id)
        if party is None or party.status != "in_combat":
            await interaction.followup.send(
                "This expedition is not active.", ephemeral=COMBAT_EPHEMERAL
            )
            return
        state = load_combat_state(party)
        if state is None:
            return
        rng = rng_for(guild_id, discord_id)
        res = select_target(session, state, discord_id, target_id, rng=rng)
        if not res.ok:
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            if res.message:
                await interaction.followup.send(res.message, ephemeral=COMBAT_EPHEMERAL)
            return
        state = await _after_combat_action(session, party, state, rng, cfg)
        save_combat_state(party, state)
        session.add(party)
        session.commit()
        await _sync_combat_ui(interaction, party_id, state, rng)
    finally:
        session.close()


async def _handle_dungeon_pass(interaction: discord.Interaction, party_id: int):
    from .bot import get_discord_id, get_guild_id, rng_for

    await interaction.response.defer(ephemeral=COMBAT_EPHEMERAL)
    cfg = get_config()
    session = get_session()
    try:
        party = session.get(ActiveDungeonParty, party_id)
        if party is None or party.status != "in_combat":
            await interaction.followup.send(
                "This expedition is not active.", ephemeral=COMBAT_EPHEMERAL
            )
            return
        state = load_combat_state(party)
        if state is None:
            return
        rng = rng_for(get_guild_id(interaction), get_discord_id(interaction.user))
        res = pass_turn(session, state, get_discord_id(interaction.user), rng=rng)
        if not res.ok:
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            if res.message:
                await interaction.followup.send(res.message, ephemeral=COMBAT_EPHEMERAL)
            return
        state = await _after_combat_action(session, party, state, rng, cfg)
        save_combat_state(party, state)
        session.add(party)
        session.commit()
        await _sync_combat_ui(interaction, party_id, state, rng)
    finally:
        session.close()


async def _handle_dungeon_cancel_target(interaction: discord.Interaction, party_id: int):
    from .bot import get_guild_id, rng_for

    session = get_session()
    try:
        party = session.get(ActiveDungeonParty, party_id)
        state = load_combat_state(party) if party else None
        if state is None:
            await interaction.response.send_message(
                "No active combat.", ephemeral=COMBAT_EPHEMERAL
            )
            return
        if should_advance_room(state):
            await interaction.response.defer(ephemeral=COMBAT_EPHEMERAL)
            rng = rng_for(get_guild_id(interaction), str(interaction.user.id))
            state = await _after_combat_action(session, party, state, rng, get_config())
            save_combat_state(party, state)
            session.add(party)
            session.commit()
            await _sync_combat_ui(interaction, party_id, state, rng)
            return
        if state.finished:
            await interaction.response.send_message(
                "This fight has ended.", ephemeral=COMBAT_EPHEMERAL
            )
            return
        state.pending_technique = None
        save_combat_state(party, state)
        session.add(party)
        session.commit()
        await interaction.response.defer(ephemeral=COMBAT_EPHEMERAL)
        rng = rng_for(get_guild_id(interaction), str(interaction.user.id))
        await _sync_combat_ui(interaction, party_id, state, rng)
    finally:
        session.close()


def setup_dungeon_command(bot) -> None:
    @bot.tree.command(
        name="dungeon",
        description="Enter a realm dungeon alone, or tag up to 3 allies who must Accept.",
    )
    @app_commands.describe(
        dungeon="Realm dungeon to challenge.",
        ally_1="Optional ally — must Accept before the run begins.",
        ally_2="Optional second ally.",
        ally_3="Optional third ally (maximum 3 invites).",
    )
    @app_commands.autocomplete(dungeon=coop_dungeon_autocomplete)
    async def dungeon_cmd(
        interaction: discord.Interaction,
        dungeon: str,
        ally_1: discord.Member | None = None,
        ally_2: discord.Member | None = None,
        ally_3: discord.Member | None = None,
    ):
        from .bot import ensure_player, get_discord_id, get_guild_id, rng_for

        if interaction.guild is None:
            await interaction.response.send_message("Use this command in a server.", ephemeral=False)
            return

        allies = [m for m in (ally_1, ally_2, ally_3) if m is not None]

        session = get_session()
        try:
            expire_stale_dungeon_parties(session)
            session.commit()

            guild_id = get_guild_id(interaction)
            leader_id = get_discord_id(interaction.user)
            leader = ensure_player(session, guild_id, leader_id)
            if leader is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return

            invitee_players: list[Player] = []
            for member in allies:
                if member.bot:
                    await interaction.response.send_message(
                        "Spirit beasts cannot join dungeon parties.",
                        ephemeral=False,
                    )
                    return
                p = ensure_player(session, guild_id, get_discord_id(member))
                if p is None:
                    await interaction.response.send_message(
                        f"**{member.display_name}** has not started with **`/start`**.",
                        ephemeral=False,
                    )
                    return
                invitee_players.append(p)

            party, err = create_party_with_invites(
                session,
                guild_id=guild_id,
                leader=leader,
                dungeon_id=dungeon,
                invitees=invitee_players,
            )
            if party is None:
                session.commit()
                await interaction.response.send_message(err, ephemeral=False)
                return

            session.commit()

            if not invitee_players:
                await interaction.response.defer(ephemeral=False)
                launch_err = await _launch_dungeon(
                    interaction,
                    party,
                    session,
                    get_config(),
                    rng_for(guild_id, leader_id),
                )
                if launch_err:
                    await interaction.followup.send(launch_err, ephemeral=False)
                return

            coop = get_cooperative_dungeon(dungeon)
            title = coop.name if coop else "Dungeon Expedition"
            embed = discord.Embed(
                title=title,
                description=format_invite_embed_description(party),
                color=discord.Color.blurple(),
            )
            invited_ids = {p.discord_id for p in invitee_players}
            view = DungeonInviteView(party.id, invited_ids)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            msg = await interaction.original_response()
            view.message = msg
            party.lobby_message_id = str(msg.id)
            party.channel_id = str(interaction.channel_id)
            session.add(party)
            session.commit()
        finally:
            session.close()

    @bot.tree.command(
        name="dungeon-cancel",
        description="Withdraw from your current dungeon expedition.",
    )
    async def dungeon_cancel_cmd(interaction: discord.Interaction):
        from .bot import ensure_player, get_discord_id, get_guild_id

        if interaction.guild is None:
            await interaction.response.send_message("Use this command in a server.", ephemeral=False)
            return
        session = get_session()
        try:
            guild_id = get_guild_id(interaction)
            discord_id = get_discord_id(interaction.user)
            player = ensure_player(session, guild_id, discord_id)
            if player is None:
                await interaction.response.send_message(NOT_STARTED_HINT, ephemeral=False)
                return
            ok, message = cancel_party_for_player(session, guild_id, discord_id)
            if ok:
                session.commit()
            await interaction.response.send_message(message, ephemeral=False)
        finally:
            session.close()
