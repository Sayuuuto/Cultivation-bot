from __future__ import annotations

from collections import defaultdict
from io import BytesIO

import discord
from sqlalchemy.orm import Session

from ..combat.catalog import TechniqueDef
from ..combat.learn import learn_technique_from_manual
from ..combat.loadout import (
    ACTIVE_SLOTS,
    PASSIVE_SLOT,
    equip_technique,
    get_learned_techniques,
    get_loadout,
)
from ..combat.rarity import RARITY_EMOJI
from ..command_choices import list_player_manuals
from ..db import get_session
from ..models import Player
from ..realms import get_realm_name
from ..technique_info import format_technique_effect_plain
from ..ui.combat_skills_card import build_combat_skills_card_data, render_combat_skills_card

EMBED_COLOR = 0x5B4B8A

# Sidebar colors for realm-grouped skill embeds (index = min_realm).
REALM_EMBED_COLORS: tuple[int, ...] = (
    0x9CA3AF,  # Mortal
    0x60A5FA,  # Qi Refining
    0x34D399,  # Foundation Establishment
    0xA78BFA,  # Core Formation
    0xF472B6,  # Nascent Soul
    0xFB923C,  # Spirit Severing
    0xF87171,  # Void Refinement
    0xFACC15,  # Immortal Ascension
    0xE879F9,  # Heavenly Transcendence
    0xFDE047,  # Immortal Monarch
)


def _realm_embed_color(realm_index: int) -> int:
    idx = max(0, min(realm_index, len(REALM_EMBED_COLORS) - 1))
    return REALM_EMBED_COLORS[idx]


def _equip_status_label(tech: TechniqueDef, loadout: dict[str, str]) -> str:
    slot = next((s for s, tid in loadout.items() if tid == tech.technique_id), None)
    if slot == PASSIVE_SLOT:
        return "💠 Passive slot"
    if slot:
        return f"🎯 Slot {slot}"
    return "📦 Ready to equip"


def _format_skill_entry(tech: TechniqueDef, loadout: dict[str, str]) -> str:
    rarity = RARITY_EMOJI.get(tech.rarity, "⚪")
    effect = format_technique_effect_plain(tech)
    if len(effect) > 180:
        effect = effect[:177] + "…"
    return f"{rarity} **{tech.name}** — {_equip_status_label(tech, loadout)}\n{effect}"


def _bucket_learned_by_realm(
    learned: list[TechniqueDef],
) -> tuple[dict[int, list[TechniqueDef]], dict[int, list[TechniqueDef]]]:
    active: dict[int, list[TechniqueDef]] = defaultdict(list)
    passive: dict[int, list[TechniqueDef]] = defaultdict(list)
    for tech in learned:
        bucket = passive if tech.slot_type == "passive" else active
        bucket[tech.min_realm].append(tech)
    for realm_buckets in (active, passive):
        for techs in realm_buckets.values():
            techs.sort(key=lambda t: t.name.lower())
    return active, passive


def _join_skill_entries(techs: list[TechniqueDef], loadout: dict[str, str], *, max_chars: int = 950) -> str:
    lines: list[str] = []
    used = 0
    for tech in techs:
        line = _format_skill_entry(tech, loadout)
        extra = len(line) + (2 if lines else 0)
        if lines and used + extra > max_chars:
            remaining = len(techs) - len(lines)
            lines.append(f"_…and {remaining} more art{'s' if remaining != 1 else ''}_")
            break
        lines.append(line)
        used += extra
    return "\n\n".join(lines)


def build_my_skills_embeds(session: Session, player: Player) -> list[discord.Embed]:
    learned = get_learned_techniques(session, player.id)
    loadout = get_loadout(session, player.id)

    if not learned:
        return [
            discord.Embed(
                title="📖 My Skills",
                description="_No arts studied yet — use **Unlock Skill** when you hold a manual._",
                color=EMBED_COLOR,
            )
        ]

    active_by_realm, passive_by_realm = _bucket_learned_by_realm(learned)
    realm_indices = sorted(set(active_by_realm) | set(passive_by_realm))
    total_active = sum(len(v) for v in active_by_realm.values())
    total_passive = sum(len(v) for v in passive_by_realm.values())

    summary = discord.Embed(
        title="📖 My Skills",
        description=(
            f"**{total_active}** active · **{total_passive}** passive arts.\n"
            "Each block below is tinted by the **realm** that art belongs to."
        ),
        color=EMBED_COLOR,
    )
    summary.set_footer(text="Equip Skill places an art in your loadout · ← Combat Skills to return")
    embeds: list[discord.Embed] = [summary]

    for realm_index in realm_indices:
        realm_name = get_realm_name(realm_index)
        actives = active_by_realm.get(realm_index, [])
        passives = passive_by_realm.get(realm_index, [])
        sections: list[str] = []
        if actives:
            sections.append(
                f"**⚔️ Active ({len(actives)})**\n{_join_skill_entries(actives, loadout)}"
            )
        if passives:
            sections.append(
                f"**💠 Passive ({len(passives)})**\n{_join_skill_entries(passives, loadout)}"
            )

        embeds.append(
            discord.Embed(
                title=realm_name,
                description="\n\n".join(sections),
                color=_realm_embed_color(realm_index),
            )
        )

    return embeds[:10]


def build_my_skills_embed(session: Session, player: Player) -> discord.Embed:
    """Primary summary embed (full layout uses :func:`build_my_skills_embeds`)."""
    return build_my_skills_embeds(session, player)[0]


def _skills_file(session: Session, player: Player) -> discord.File:
    data = build_combat_skills_card_data(session, player)
    png = render_combat_skills_card(data)
    return discord.File(BytesIO(png), filename="combat_skills.png")


def build_combat_skills_hub_embed(session: Session, player: Player) -> discord.Embed:
    """Readable Discord embed for the techniques hub (native text size)."""
    data = build_combat_skills_card_data(session, player)
    embed = discord.Embed(
        title=f"Combat Skills — {data.dao_name}",
        description=(
            f"**{data.realm_label}**\n"
            f"Arts studied **{data.unlocked}** / **{data.total}** · "
            f"Spirit stones **{data.spirit_stones_display}**"
        ),
        color=EMBED_COLOR,
    )
    for slot in data.slots:
        if slot.filled:
            value = f"**{slot.technique_name}**\n{slot.effect_text}"
        else:
            value = "_Empty — tap **Equip Skill** to assign an art._"
        if len(value) > 1024:
            value = value[:1021] + "…"
        embed.add_field(name=slot.slot_label, value=value, inline=False)
    embed.set_footer(text="Equip Skill · My Skills · Unlock Skill")
    return embed


async def _send_skills_hub_message(
    interaction: discord.Interaction,
    owner_discord_id: str,
    player: Player,
    *,
    edit: bool,
) -> None:
    session = get_session()
    try:
        embed = build_combat_skills_hub_embed(session, player)
        file = _skills_file(session, player)
        embed.set_image(url="attachment://combat_skills.png")
        view = TechniquesHubView(owner_discord_id, player.id)
        kwargs: dict = {
            "content": None,
            "embed": embed,
            "attachments": [file],
            "view": view,
        }
        if edit:
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    finally:
        session.close()


async def _restore_skills_hub(
    interaction: discord.Interaction,
    owner_discord_id: str,
    player_id: int,
) -> None:
    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            await interaction.response.send_message("Character not found.", ephemeral=True)
            return
        await _send_skills_hub_message(
            interaction, owner_discord_id, player, edit=True
        )
    finally:
        session.close()


class BackToSkillsHubButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int, *, row: int = 0):
        super().__init__(label="← Combat Skills", style=discord.ButtonStyle.secondary, row=row)
        self._owner = owner_discord_id
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await _restore_skills_hub(interaction, self._owner, self._player_id)


class EquipTechniqueSelect(discord.ui.Select):
    """Step 1: pick a learned art."""

    def __init__(self, session: Session, player: Player):
        learned = get_learned_techniques(session, player.id)
        options: list[discord.SelectOption] = []

        for tech in learned:
            if player.realm_index < tech.min_realm:
                continue
            desc = format_technique_effect_plain(tech)[:100]
            options.append(
                discord.SelectOption(
                    label=tech.name[:100],
                    value=tech.technique_id,
                    description=desc or None,
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No learned skills",
                    value="_none",
                    description="Unlock a manual first",
                )
            )

        super().__init__(
            placeholder="Choose a learned skill…",
            options=options[:25],
            min_values=1,
            max_values=1,
            disabled=not options or options[0].value == "_none",
            row=0,
        )
        self._player_id = player.id

    async def callback(self, interaction: discord.Interaction) -> None:
        technique_id = self.values[0]
        if technique_id == "_none":
            await interaction.response.send_message(
                "Learn an art first — use **Unlock Skill** or hunt for manuals.",
                ephemeral=True,
            )
            return

        view = EquipSlotPickView(str(interaction.user.id), self._player_id, technique_id)
        await interaction.response.edit_message(
            content="**Equip Skill** — choose which slot to fill.",
            embed=None,
            attachments=[],
            view=view,
        )


class EquipSlotSelect(discord.ui.Select):
    """Step 2: pick slot for the chosen art."""

    def __init__(self, session: Session, player: Player, technique_id: str):
        from ..combat.catalog import get_technique

        tech = get_technique(technique_id)
        options: list[discord.SelectOption] = []

        if tech is not None:
            if tech.slot_type == "active":
                for slot in ACTIVE_SLOTS:
                    options.append(
                        discord.SelectOption(
                            label=f"Slot {slot}",
                            value=slot,
                            description=f"Equip {tech.name} here",
                        )
                    )
            else:
                options.append(
                    discord.SelectOption(
                        label="Passive slot",
                        value=PASSIVE_SLOT,
                        description=f"Equip {tech.name} as passive",
                    )
                )

        if not options:
            options.append(
                discord.SelectOption(label="No valid slot", value="_none"),
            )

        super().__init__(
            placeholder="Choose a slot (1–4 or passive)…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self._player_id = player.id
        self._technique_id = technique_id

    async def callback(self, interaction: discord.Interaction) -> None:
        slot = self.values[0]
        if slot == "_none":
            await interaction.response.send_message("That art cannot be equipped.", ephemeral=True)
            return

        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            ok, message = equip_technique(session, player, self._technique_id, slot)
            if not ok:
                await interaction.response.send_message(message, ephemeral=True)
                return
            session.commit()
            await _send_skills_hub_message(
                interaction, str(interaction.user.id), player, edit=True
            )
            await interaction.followup.send(message, ephemeral=True)
        finally:
            session.close()


class EquipSlotPickView(discord.ui.View):
    def __init__(self, owner_discord_id: str, player_id: int, technique_id: str):
        super().__init__(timeout=120)
        self.owner_discord_id = owner_discord_id
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(EquipSlotSelect(session, player, technique_id))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This martial ledger belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True


class EquipSkillView(discord.ui.View):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(timeout=120)
        self.owner_discord_id = owner_discord_id
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(EquipTechniqueSelect(session, player))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This martial ledger belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True


class UnlockManualSelect(discord.ui.Select):
    def __init__(self, session: Session, player: Player):
        manuals = list_player_manuals(session, player.id)
        options = [
            discord.SelectOption(label=label[:100], value=item_id)
            for item_id, label in manuals[:25]
        ]
        super().__init__(
            placeholder="Choose a manual from your bag…",
            options=options,
            min_values=1,
            max_values=1,
            disabled=not options,
            row=0,
        )
        self._player_id = player.id
        self._owner = ""

    async def callback(self, interaction: discord.Interaction) -> None:
        manual_id = self.values[0]
        session = get_session()
        try:
            ok, message = learn_technique_from_manual(session, self._player_id, manual_id)
            if not ok:
                await interaction.response.send_message(message, ephemeral=True)
                return
            session.commit()
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message(message, ephemeral=True)
                return
            await _send_skills_hub_message(
                interaction, str(interaction.user.id), player, edit=True
            )
            await interaction.followup.send(message, ephemeral=True)
        finally:
            session.close()


class UnlockSkillView(discord.ui.View):
    def __init__(self, owner_discord_id: str, session: Session, player: Player):
        super().__init__(timeout=120)
        self.owner_discord_id = owner_discord_id
        self.add_item(UnlockManualSelect(session, player))
        self.add_item(BackToSkillsHubButton(owner_discord_id, player.id, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This martial ledger belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True


class MySkillsView(discord.ui.View):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(timeout=120)
        self.owner_discord_id = owner_discord_id
        self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=0))


class EquipSkillButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(label="Equip Skill", style=discord.ButtonStyle.primary, emoji="🎯", row=0)
        self._owner = owner_discord_id
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            view = EquipSkillView(self._owner, self._player_id)
            await interaction.response.edit_message(
                content="**Equip Skill** — pick a learned art, then choose a slot.",
                embed=None,
                attachments=[],
                view=view,
            )
        finally:
            session.close()


class MySkillsButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(label="My Skills", style=discord.ButtonStyle.secondary, emoji="📖", row=0)
        self._owner = owner_discord_id
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            embeds = build_my_skills_embeds(session, player)
            view = MySkillsView(self._owner, self._player_id)
            await interaction.response.edit_message(
                content=None,
                embeds=embeds,
                attachments=[],
                view=view,
            )
        finally:
            session.close()


class UnlockSkillButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int, *, has_manuals: bool):
        super().__init__(
            label="Unlock Skill",
            style=discord.ButtonStyle.success,
            emoji="🔓",
            disabled=not has_manuals,
            row=0,
        )
        self._owner = owner_discord_id
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            if not list_player_manuals(session, player.id):
                await interaction.response.send_message(
                    "No technique manuals in your bag. Hunt, adventure, or visit the shop.",
                    ephemeral=True,
                )
                return
            view = UnlockSkillView(self._owner, session, player)
            await interaction.response.edit_message(
                content="**Unlock Skill** — study a manual from your inventory.",
                embed=None,
                attachments=[],
                view=view,
            )
        finally:
            session.close()


class TechniquesHubView(discord.ui.View):
    def __init__(self, owner_discord_id: str, player_id: int, *, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.owner_discord_id = owner_discord_id
        session = get_session()
        try:
            has_manuals = bool(list_player_manuals(session, player_id))
        finally:
            session.close()
        self.add_item(EquipSkillButton(owner_discord_id, player_id))
        self.add_item(MySkillsButton(owner_discord_id, player_id))
        self.add_item(UnlockSkillButton(owner_discord_id, player_id, has_manuals=has_manuals))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This martial ledger belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True


TechniquesView = TechniquesHubView
