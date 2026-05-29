from __future__ import annotations

import logging
from collections import defaultdict
from io import BytesIO

import discord
from sqlalchemy.orm import Session

from ..combat.catalog import TechniqueDef
from ..combat.learn import learn_technique_from_manual
from ..combat.loadout import (
    ACTIVE_SLOTS,
    PASSIVE_SLOT,
    _load_totals,
    equip_technique,
    get_learned_techniques,
    get_loadout,
    get_technique_rank,
    list_pvp_loadout_violations,
    unequip_slot,
)
from ..combat.ranks import upgrade_technique_rank
from ..technique_info import build_technique_detail_embed
from ..combat.catalog import load_technique_catalog
from ..combat.rules import load_combat_rules
from ..player_guides import format_load_hub_line, guide_text
from ..realms import get_technique_load_budget, get_technique_rank_cap
from ..combat.rarity import RARITY_EMOJI
from ..command_choices import list_player_manuals
from ..db import get_session
from ..models import Player
from ..realms import get_realm_name
from ..technique_info import format_technique_effect_plain, technique_base_power
from ..ui.combat_skills_card import build_combat_skills_card_data, render_combat_skills_card
from ..ui.fonts import card_fonts_available, card_images_enabled

logger = logging.getLogger(__name__)

HUB_VIEW_TIMEOUT = 600
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


def _format_skill_entry(
    tech: TechniqueDef,
    loadout: dict[str, str],
    *,
    rank: int | None = None,
    rank_cap: int | None = None,
) -> str:
    rarity = RARITY_EMOJI.get(tech.rarity, "⚪")
    effect = format_technique_effect_plain(tech)
    if len(effect) > 180:
        effect = effect[:177] + "…"
    rank_bit = ""
    if rank is not None and load_combat_rules().enabled("technique_ranks"):
        cap_note = f"/{rank_cap}" if rank_cap else ""
        rank_bit = f" · rank **{rank}{cap_note}**"
    power = technique_base_power(tech)
    power_bit = f" · base **{power}**" if power is not None else ""
    return f"{rarity} **{tech.name}**{power_bit} — {_equip_status_label(tech, loadout)}{rank_bit}\n{effect}"


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


def _join_skill_entries(
    session: Session,
    player: Player,
    techs: list[TechniqueDef],
    loadout: dict[str, str],
    *,
    max_chars: int = 950,
) -> str:
    rank_cap = get_technique_rank_cap(player.realm_index) if load_combat_rules().enabled("technique_ranks") else None
    lines: list[str] = []
    used = 0
    for tech in techs:
        rank = get_technique_rank(session, player.id, tech.technique_id) if rank_cap else None
        line = _format_skill_entry(tech, loadout, rank=rank, rank_cap=rank_cap)
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

    desc_lines = [
        f"**{total_active}** active · **{total_passive}** passive arts.",
        "Each block below is tinted by the **realm** that art belongs to.",
    ]
    if load_combat_rules().enabled("technique_load_budget"):
        budget = get_technique_load_budget(player.realm_index)
        totals = _load_totals(load_technique_catalog(), loadout)
        desc_lines.insert(
            1,
            format_load_hub_line(
                active_used=totals["active"],
                active_cap=budget["active"],
                passive_used=totals["passive"],
                passive_cap=budget["passive"],
                total_used=totals["total"],
                total_cap=budget["total"],
            ),
        )
    violations = list_pvp_loadout_violations(session, player)
    if violations:
        desc_lines.append("**Arena** — loadout exceeds duel limits.")
    elif load_combat_rules().enabled("pvp_legality_checks"):
        desc_lines.append("**Arena** — loadout legal for duels.")

    summary = discord.Embed(
        title="📖 My Skills",
        description="\n".join(desc_lines),
        color=EMBED_COLOR,
    )
    footer = "Skill Library — read any art · Equip · Upgrade · ← Combat Skills"
    summary.set_footer(text=footer)
    embeds: list[discord.Embed] = [summary]

    for realm_index in realm_indices:
        realm_name = get_realm_name(realm_index)
        actives = active_by_realm.get(realm_index, [])
        passives = passive_by_realm.get(realm_index, [])
        sections: list[str] = []
        if actives:
            sections.append(
                f"**⚔️ Active ({len(actives)})**\n"
                f"{_join_skill_entries(session, player, actives, loadout)}"
            )
        if passives:
            sections.append(
                f"**💠 Passive ({len(passives)})**\n"
                f"{_join_skill_entries(session, player, passives, loadout)}"
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
    data = build_combat_skills_card_data(session, player, ensure_starter=False)
    png = render_combat_skills_card(data)
    return discord.File(BytesIO(png), filename="combat_skills.png")


def build_combat_skills_hub_embed(session: Session, player: Player) -> discord.Embed:
    """Readable Discord embed for the techniques hub (native text size)."""
    data = build_combat_skills_card_data(session, player, ensure_starter=False)
    loadout = get_loadout(session, player.id)
    desc_parts = [
        f"**{data.realm_label}**",
        f"Arts studied **{data.unlocked}** / **{data.total}** · Spirit stones **{data.spirit_stones_display}**",
    ]
    if load_combat_rules().enabled("technique_load_budget"):
        budget = get_technique_load_budget(player.realm_index)
        totals = _load_totals(load_technique_catalog(), loadout)
        desc_parts.append(
            format_load_hub_line(
                active_used=totals["active"],
                active_cap=budget["active"],
                passive_used=totals["passive"],
                passive_cap=budget["passive"],
                total_used=totals["total"],
                total_cap=budget["total"],
            )
        )
    violations = list_pvp_loadout_violations(session, player)
    if violations:
        desc_parts.append("**Duel loadout** — fix limits before **`/duel`**.")
    embed = discord.Embed(
        title=f"Combat Skills — {data.dao_name}",
        description="\n".join(desc_parts),
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
    embed.set_footer(
        text=guide_text(
            "pvp_legality",
            "duel_footer",
            default="Equip · Skill Library · Unlock · Upgrade · Manage Slots",
        )
    )
    return embed


class HubViewBase(discord.ui.View):
    """Sub-views for /techniques; only the owner may press buttons or selects."""

    def __init__(self, owner_discord_id: str, *, timeout: float = HUB_VIEW_TIMEOUT):
        super().__init__(timeout=timeout)
        self.owner_discord_id = owner_discord_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_discord_id:
            await interaction.response.send_message(
                "This martial ledger belongs to another daoist.",
                ephemeral=True,
            )
            return False
        return True


async def _edit_panel(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None,
) -> None:
    """Edit an interaction message; discord.py forbids embed= and embeds= together."""
    if file is not None:
        await interaction.response.edit_message(
            content=content,
            file=file,
            attachments=[],
            view=view,
        )
        return
    if embeds:
        await interaction.response.edit_message(
            content=content,
            embeds=embeds,
            attachments=[],
            view=view,
        )
        return
    if embed is not None:
        await interaction.response.edit_message(
            content=content,
            embed=embed,
            attachments=[],
            view=view,
        )
        return
    await interaction.response.edit_message(
        content=content,
        attachments=[],
        view=view,
    )


async def _send_skills_hub_message(
    interaction: discord.Interaction,
    owner_discord_id: str,
    player: Player,
    *,
    edit: bool,
    use_followup: bool = False,
) -> None:
    """
    Show the combat skills hub.

    Edits always use an embed — Discord cannot reliably re-attach PNG cards when
    returning from sub-menus that cleared attachments.
    """
    session = get_session()
    try:
        view = TechniquesHubView(owner_discord_id, player.id)
        embed = build_combat_skills_hub_embed(session, player)

        if edit:
            await _edit_panel(interaction, embed=embed, view=view)
            return

        card_file: discord.File | None = None
        if card_images_enabled() and card_fonts_available():
            try:
                card_file = _skills_file(session, player)
            except Exception:
                logger.exception(
                    "Combat skills card render failed player_id=%s",
                    player.id,
                )

        if card_file is not None and not use_followup:
            await interaction.response.send_message(file=card_file, view=view)
            return
        if card_file is not None and use_followup:
            await interaction.followup.send(file=card_file, view=view)
            return
        if use_followup:
            await interaction.followup.send(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
    finally:
        session.close()


async def _hub_toast(
    interaction: discord.Interaction,
    owner_discord_id: str,
    player: Player,
    message: str,
) -> None:
    """Return to hub after an action; show result as ephemeral toast."""
    await _send_skills_hub_message(
        interaction, owner_discord_id, player, edit=True
    )
    try:
        await interaction.followup.send(message, ephemeral=True)
    except discord.HTTPException:
        logger.debug("Hub toast skipped (interaction expired)")


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
        try:
            await _send_skills_hub_message(
                interaction, owner_discord_id, player, edit=True
            )
        except discord.HTTPException:
            logger.exception(
                "Hub restore failed player_id=%s — user should re-open /techniques",
                player_id,
            )
            await interaction.response.send_message(
                "Could not refresh this panel — run **`/techniques`** again.",
                ephemeral=True,
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
        await _edit_panel(
            interaction,
            content="**Equip Skill** — choose which slot to fill.",
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
            await _hub_toast(interaction, str(interaction.user.id), player, message)
        finally:
            session.close()


class EquipSlotPickView(HubViewBase):
    def __init__(self, owner_discord_id: str, player_id: int, technique_id: str):
        super().__init__(owner_discord_id)
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(EquipSlotSelect(session, player, technique_id))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()


class EquipSkillView(HubViewBase):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(owner_discord_id)
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(EquipTechniqueSelect(session, player))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()


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
            await _hub_toast(interaction, str(interaction.user.id), player, message)
        finally:
            session.close()


class UnlockSkillView(HubViewBase):
    def __init__(self, owner_discord_id: str, session: Session, player: Player):
        super().__init__(owner_discord_id)
        self.add_item(UnlockManualSelect(session, player))
        self.add_item(BackToSkillsHubButton(owner_discord_id, player.id, row=1))


class InspectSkillSelect(discord.ui.Select):
    """Pick a learned art to read full details."""

    def __init__(self, session: Session, player: Player):
        learned = get_learned_techniques(session, player.id)
        options: list[discord.SelectOption] = []
        loadout = get_loadout(session, player.id)
        for tech in learned[:25]:
            slot = next((s for s, tid in loadout.items() if tid == tech.technique_id), None)
            status = f"Slot {slot}" if slot and slot != PASSIVE_SLOT else ("Passive" if slot else "Not equipped")
            options.append(
                discord.SelectOption(
                    label=tech.name[:100],
                    value=tech.technique_id,
                    description=status[:100],
                )
            )
        if not options:
            options.append(
                discord.SelectOption(
                    label="No arts studied",
                    value="_none",
                    description="Unlock a manual first",
                )
            )
        super().__init__(
            placeholder="Read a learned art…",
            options=options,
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
                "Study an art first — use **Unlock Skill** when you hold a manual.",
                ephemeral=True,
            )
            return
        session = get_session()
        try:
            from ..combat.catalog import get_technique

            player = session.get(Player, self._player_id)
            tech = get_technique(technique_id)
            if player is None or tech is None:
                await interaction.response.send_message("That art could not be found.", ephemeral=True)
                return
            embed = build_technique_detail_embed(tech, session=session, player_id=player.id)
            view = _make_skill_detail_view(str(interaction.user.id), player.id, technique_id)
            await _edit_panel(interaction, embed=embed, view=view)
        finally:
            session.close()


def _make_skill_detail_view(owner_discord_id: str, player_id: int, technique_id: str) -> HubViewBase:
    class _SkillDetailView(HubViewBase):
        pass

    view = _SkillDetailView(owner_discord_id)

    async def equip_cb(interaction: discord.Interaction) -> None:
        pick = EquipSlotPickView(owner_discord_id, player_id, technique_id)
        await _edit_panel(
            interaction,
            content="**Equip Skill** — choose which slot to fill.",
            view=pick,
        )

    equip_btn = discord.ui.Button(
        label="Equip this art",
        style=discord.ButtonStyle.primary,
        emoji="🎯",
        row=0,
    )
    equip_btn.callback = equip_cb
    view.add_item(equip_btn)
    view.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
    return view


class MySkillsView(HubViewBase):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(owner_discord_id)
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(InspectSkillSelect(session, player))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()


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
            await _edit_panel(
                interaction,
                content="**Equip Skill** — pick a learned art, then choose a slot.",
                view=view,
            )
        finally:
            session.close()


class ClearSlotSelect(discord.ui.Select):
    def __init__(self, session: Session, player: Player):
        loadout = get_loadout(session, player.id)
        options: list[discord.SelectOption] = []
        for slot in (*ACTIVE_SLOTS, PASSIVE_SLOT):
            technique_id = loadout.get(slot)
            if not technique_id:
                continue
            from ..combat.catalog import get_technique

            tech = get_technique(technique_id)
            label = f"Clear slot {slot}" if slot != PASSIVE_SLOT else "Clear passive slot"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=slot,
                    description=(tech.name[:100] if tech else technique_id),
                )
            )
        if not options:
            options.append(
                discord.SelectOption(label="All slots empty", value="_none"),
            )
        super().__init__(
            placeholder="Choose a slot to clear…",
            options=options,
            min_values=1,
            max_values=1,
            disabled=not options or options[0].value == "_none",
            row=0,
        )
        self._player_id = player.id

    async def callback(self, interaction: discord.Interaction) -> None:
        slot = self.values[0]
        if slot == "_none":
            await interaction.response.send_message("Every slot is already empty.", ephemeral=True)
            return
        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            ok, message = unequip_slot(session, player.id, slot)
            if not ok:
                await interaction.response.send_message(message, ephemeral=True)
                return
            session.commit()
            await _hub_toast(interaction, str(interaction.user.id), player, message)
        finally:
            session.close()


class ManageSlotsView(HubViewBase):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(owner_discord_id)
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(ClearSlotSelect(session, player))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()


class UpgradeTechniqueSelect(discord.ui.Select):
    def __init__(self, session: Session, player: Player):
        learned = get_learned_techniques(session, player.id)
        options: list[discord.SelectOption] = []
        for tech in learned:
            if player.realm_index < tech.min_realm:
                continue
            rank = get_technique_rank(session, player.id, tech.technique_id)
            options.append(
                discord.SelectOption(
                    label=tech.name[:100],
                    value=tech.technique_id,
                    description=f"Rank {rank}"[:100],
                )
            )
        if not options:
            options.append(
                discord.SelectOption(label="No arts to temper", value="_none"),
            )
        super().__init__(
            placeholder="Choose an art to temper…",
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
                "No studied arts available to temper.", ephemeral=True
            )
            return
        session = get_session()
        try:
            player = session.get(Player, self._player_id)
            if player is None:
                await interaction.response.send_message("Character not found.", ephemeral=True)
                return
            ok, message = upgrade_technique_rank(session, player, technique_id)
            if not ok:
                await interaction.response.send_message(message, ephemeral=True)
                return
            session.commit()
            await _hub_toast(interaction, str(interaction.user.id), player, message)
        finally:
            session.close()


class UpgradeSkillView(HubViewBase):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(owner_discord_id)
        session = get_session()
        try:
            player = session.get(Player, player_id)
            if player is not None:
                self.add_item(UpgradeTechniqueSelect(session, player))
            self.add_item(BackToSkillsHubButton(owner_discord_id, player_id, row=1))
        finally:
            session.close()


class MySkillsButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(label="Skill Library", style=discord.ButtonStyle.secondary, emoji="📖", row=0)
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
            await _edit_panel(interaction, embeds=embeds, view=view)
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
            await _edit_panel(
                interaction,
                content="**Unlock Skill** — study a manual from your inventory.",
                view=view,
            )
        finally:
            session.close()


class ManageSlotsButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int):
        super().__init__(label="Manage Slots", style=discord.ButtonStyle.secondary, emoji="🧹", row=1)
        self._owner = owner_discord_id
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        view = ManageSlotsView(self._owner, self._player_id)
        await _edit_panel(
            interaction,
            content="**Manage Slots** — clear an equipped art (it stays in your library).",
            view=view,
        )


class UpgradeSkillButton(discord.ui.Button):
    def __init__(self, owner_discord_id: str, player_id: int, *, enabled: bool):
        super().__init__(
            label="Upgrade",
            style=discord.ButtonStyle.success,
            emoji="⬆️",
            disabled=not enabled,
            row=1,
        )
        self._owner = owner_discord_id
        self._player_id = player_id

    async def callback(self, interaction: discord.Interaction) -> None:
        view = UpgradeSkillView(self._owner, self._player_id)
        await _edit_panel(
            interaction,
            content="**Upgrade** — temper a studied art (stones, materials, fragments).",
            view=view,
        )


class TechniquesHubView(HubViewBase):
    def __init__(self, owner_discord_id: str, player_id: int, *, timeout: float = HUB_VIEW_TIMEOUT):
        super().__init__(owner_discord_id, timeout=timeout)
        session = get_session()
        try:
            has_manuals = bool(list_player_manuals(session, player_id))
            ranks_on = load_combat_rules().enabled("technique_ranks")
        finally:
            session.close()
        self.add_item(EquipSkillButton(owner_discord_id, player_id))
        self.add_item(MySkillsButton(owner_discord_id, player_id))
        self.add_item(UnlockSkillButton(owner_discord_id, player_id, has_manuals=has_manuals))
        self.add_item(UpgradeSkillButton(owner_discord_id, player_id, enabled=ranks_on))
        self.add_item(ManageSlotsButton(owner_discord_id, player_id))


TechniquesView = TechniquesHubView
