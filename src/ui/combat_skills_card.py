from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from ..combat.catalog import get_technique, load_technique_catalog
from ..combat.loadout import ACTIVE_SLOTS, PASSIVE_SLOT, ensure_starter_techniques, get_loadout
from ..realms import REALMS, SUBSTAGES
from ..technique_info import format_technique_effect_plain

from sqlalchemy.orm import Session

from ..models import Player

CARD_W = 1024
MARGIN = 28
SUPER_SAMPLE = 2
FONT_SCALE = 1.35
SLOT_BLOCK_H = 100
HEADER_H = 72
FOOTER_H = 48

BG_TOP = (14, 20, 34)
BG_BOTTOM = (8, 11, 20)
PANEL = (24, 32, 50)
PANEL_ALT = (30, 38, 56)
PANEL_BORDER = (58, 72, 98)
ACCENT = (91, 75, 138)
TEXT = (245, 248, 252)
TEXT_DIM = (148, 158, 178)
TEXT_MUTED = (95, 108, 128)
CYAN = (120, 210, 230)
GREEN = (85, 210, 140)
EMPTY_NAME = (110, 120, 140)


@dataclass
class SkillSlotView:
    slot_label: str
    technique_name: str
    effect_text: str
    filled: bool


@dataclass
class CombatSkillsCardData:
    dao_name: str
    realm_label: str
    unlocked: int
    total: int
    spirit_stones_display: str
    slots: list[SkillSlotView]


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    )
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for directory in (
        os.path.join(windir, "Fonts"),
        "/usr/share/fonts/truetype/dejavu",
        "/System/Library/Fonts/Supplemental",
    ):
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scale(v: int) -> int:
    return v * SUPER_SAMPLE


def _font_size(points: int) -> int:
    return max(8, int(points * FONT_SCALE * SUPER_SAMPLE))


def _realm_label(realm_index: int, substage: int) -> str:
    realm = REALMS[min(max(realm_index, 0), len(REALMS) - 1)]
    stage = SUBSTAGES[min(max(substage, 0), len(SUBSTAGES) - 1)]
    return f"{realm} ({stage})"


def _format_stones(value: int) -> str:
    n = int(value)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}".rstrip("0").rstrip(".") + "K"
    return str(n)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]


def build_combat_skills_card_data(session: Session, player: Player) -> CombatSkillsCardData:
    ensure_starter_techniques(session, player.id)
    loadout = get_loadout(session, player.id)
    slots: list[SkillSlotView] = []

    for slot in ACTIVE_SLOTS:
        technique_id = loadout.get(slot)
        if technique_id:
            tech = get_technique(technique_id)
            if tech is not None:
                slots.append(
                    SkillSlotView(
                        slot_label=f"Slot {slot}",
                        technique_name=tech.name,
                        effect_text=format_technique_effect_plain(tech),
                        filled=True,
                    )
                )
                continue
        slots.append(
            SkillSlotView(
                slot_label=f"Slot {slot}",
                technique_name="Empty",
                effect_text="No art equipped.",
                filled=False,
            )
        )

    passive_id = loadout.get(PASSIVE_SLOT)
    if passive_id:
        passive = get_technique(passive_id)
        if passive is not None:
            slots.append(
                SkillSlotView(
                    slot_label="Passive",
                    technique_name=passive.name,
                    effect_text=format_technique_effect_plain(passive),
                    filled=True,
                )
            )
        else:
            slots.append(
                SkillSlotView(
                    slot_label="Passive",
                    technique_name="Empty",
                    effect_text="No passive equipped.",
                    filled=False,
                )
            )
    else:
        slots.append(
            SkillSlotView(
                slot_label="Passive",
                technique_name="Empty",
                effect_text="No passive equipped.",
                filled=False,
            )
        )

    from ..combat.loadout import get_learned_technique_ids

    unlocked = len(get_learned_technique_ids(session, player.id))

    return CombatSkillsCardData(
        dao_name=player.dao_name or "Daoist",
        realm_label=_realm_label(player.realm_index, player.substage),
        unlocked=unlocked,
        total=len(load_technique_catalog()),
        spirit_stones_display=_format_stones(player.spirit_stones),
        slots=slots,
    )


def render_combat_skills_card(data: CombatSkillsCardData) -> bytes:
    card_h = HEADER_H + len(data.slots) * SLOT_BLOCK_H + FOOTER_H + 24
    ss = SUPER_SAMPLE
    img = Image.new("RGB", (CARD_W * ss, card_h * ss), BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    # Background gradient
    for row in range(card_h * ss):
        t = row / max(card_h * ss - 1, 1)
        color = (
            int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t),
            int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t),
            int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t),
        )
        draw.line([(0, row), (CARD_W * ss, row)], fill=color)

    # Accent bar
    draw.rectangle((0, 0, _scale(6), card_h * ss), fill=ACCENT)

    font_title = _load_font(_font_size(28), bold=True)
    font_section = _load_font(_font_size(13), bold=True)
    font_name = _load_font(_font_size(17), bold=True)
    font_body = _load_font(_font_size(13))
    font_sm = _load_font(_font_size(11))
    font_stats = _load_font(_font_size(12))

    y = 20
    draw.text((_scale(MARGIN), _scale(y)), "Combat Skills", font=font_title, fill=CYAN)
    draw.text(
        (_scale(CARD_W - MARGIN) - draw.textlength(data.realm_label, font=font_sm), _scale(y + 6)),
        data.realm_label,
        font=font_sm,
        fill=TEXT_DIM,
    )
    y += 42
    draw.text((_scale(MARGIN), _scale(y)), data.dao_name, font=font_section, fill=TEXT_DIM)
    y += 26

    inner_w = CARD_W - 2 * MARGIN - 20
    for slot in data.slots:
        box_y0 = y
        box_y1 = y + SLOT_BLOCK_H - 8
        fill = PANEL_ALT if slot.filled else PANEL
        border = GREEN if slot.filled else PANEL_BORDER
        draw.rounded_rectangle(
            (_scale(MARGIN), _scale(box_y0), _scale(CARD_W - MARGIN), _scale(box_y1)),
            radius=_scale(10),
            fill=fill,
            outline=border,
            width=_scale(1),
        )

        draw.text((_scale(MARGIN + 14), _scale(box_y0 + 10)), slot.slot_label, font=font_section, fill=TEXT_MUTED)
        name_color = TEXT if slot.filled else EMPTY_NAME
        draw.text((_scale(MARGIN + 14), _scale(box_y0 + 28)), slot.technique_name, font=font_name, fill=name_color)

        wrapped = _wrap_text(draw, slot.effect_text, font_body, _scale(inner_w))
        line_y = box_y0 + 48
        for line in wrapped:
            draw.text((_scale(MARGIN + 14), _scale(line_y)), line, font=font_body, fill=TEXT_DIM)
            line_y += 18

        y = box_y1 + 10

    stats = (
        f"Unlocked {data.unlocked}/{data.total}  ·  "
        f"Spirit stones {data.spirit_stones_display}"
    )
    draw.text((_scale(MARGIN), _scale(y + 8)), stats, font=font_stats, fill=TEXT_MUTED)

    draw.rounded_rectangle(
        (_scale(4), _scale(4), _scale(CARD_W - 4), _scale(card_h - 4)),
        radius=_scale(14),
        outline=PANEL_BORDER,
        width=_scale(2),
    )

    if ss > 1:
        img = img.resize((CARD_W, card_h), Image.Resampling.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
