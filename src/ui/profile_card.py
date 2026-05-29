from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from ..combat.catalog import get_technique
from ..technique_info import technique_base_power
from .fonts import load_card_font
from ..combat.loadout import ACTIVE_SLOTS, PASSIVE_SLOT, ensure_starter_techniques, get_loadout
from ..effects import EFFECT_LABELS, list_active_player_effects
from ..equipment import get_player_equipment
from ..game import qi_cap
from ..inventory import get_item_name
from ..karma import karma_tier, karma_tier_label
from ..realms import REALMS, SUBSTAGES, get_realm_name
from ..reputation import reputation_tier_label

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..combat_stats import PlayerCombatStats
    from ..config import Config
    from ..models import Player

CARD_W = 1024
FOOTER_H = 56
MARGIN = 28
SUPER_SAMPLE = 2
# Discord shrinks attachment previews; scale type above 1.0 for legible card images.
FONT_SCALE = 1.35

BG_TOP = (14, 20, 34)
BG_BOTTOM = (8, 11, 20)
PANEL = (24, 32, 50)
PANEL_ALT = (32, 42, 62)
PANEL_BORDER = (58, 72, 98)
TEXT = (245, 248, 252)
TEXT_DIM = (148, 158, 178)
TEXT_MUTED = (95, 108, 128)
CYAN = (100, 210, 230)
CYAN_BRIGHT = (140, 230, 245)
CYAN_BAR = (45, 140, 175)
CYAN_BAR_HI = (70, 185, 210)
RED = (235, 95, 105)
GREEN = (85, 210, 140)
GOLD = (255, 220, 110)
GOLD_BRIGHT = (255, 238, 160)
GOLD_PANEL = (48, 40, 16)
GOLD_PANEL_HI = (62, 52, 22)
GOLD_BORDER = (215, 175, 60)
SHADOW = (0, 0, 0)

EQUIPMENT_ORDER = ("weapon", "armor", "accessory", "talisman")
ROMAN = ("I", "II", "III", "IV", "V", "VI")


@dataclass
class StatCell:
    label: str
    value: str


@dataclass
class EquipmentSlotView:
    slot_name: str
    title: str
    subtitle: str
    filled: bool


@dataclass
class ProfileCardData:
    dao_name: str
    guild_label: str
    realm_banner: str
    realm_detail: str
    origin: str
    spirit_root: str
    karma_label: str
    karma_tier_key: str
    reputation_label: str
    substage_label: str
    adventures: int
    pvp_record: str
    qi: int
    qi_cap: int
    qi_pct: int
    breakthrough_ready: bool
    spirit_stones: int
    spirit_stones_display: str
    daily_streak: int
    sect_name: str | None
    clan_name: str | None
    martial_lines: list[str]
    martial_hint: str | None
    equipment_slots: list[EquipmentSlotView]
    next_action_line: str
    activity_line: str
    trial_complete: bool
    effect_lines: list[str] = field(default_factory=list)
    trial_line: str | None = None
    passive_qi_line: str | None = None
    active_cultivate_line: str | None = None


def format_compact_number(value: int) -> str:
    n = int(value)
    if n >= 1_000_000:
        text = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{text}M"
    if n >= 10_000:
        text = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}K"
    if n >= 1_000:
        text = f"{n / 1_000:.2f}".rstrip("0").rstrip(".")
        return f"{text}K"
    return str(n)


def format_spirit_stones(value: int) -> str:
    return f"{int(value):,}"


def plain_card_text(text: str) -> str:
    """Single-line text safe for PIL measurement (no markdown/newlines)."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\n", " · ")
    cleaned = cleaned.replace("**", "").replace("`", "").replace("_", "")
    return " ".join(cleaned.split())


def realm_banner(realm_index: int, substage: int) -> str:
    realm = REALMS[min(max(realm_index, 0), len(REALMS) - 1)]
    roman = ROMAN[min(max(substage, 0), len(ROMAN) - 1)]
    return f"{realm.upper()} · {roman}"


def _effect_charge_unit(effect_id: str) -> str:
    units = {
        "qi_gathering": "cultivate",
        "tempering": "adventure/dungeon run",
        "clarity": "breakthrough",
        "swiftwind": "adventure",
        "blood_ember": "dungeon run",
        "moonwell_tonic": "adventure",
        "shrine_boon": "run",
        "shrine_curse": "run",
    }
    return units.get(effect_id, "use")


def _short_effect_line(effect_id: str, charges: int | None, now: datetime, expires_at: datetime | None = None) -> str:
    label = EFFECT_LABELS.get(effect_id, effect_id.replace("_", " ").title())
    count = charges or 1
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = _short_time(max(0, int((expires_at - now).total_seconds())))
        return f"{label} (expires in {remaining})"
    unit = _effect_charge_unit(effect_id)
    if count == 1:
        return f"{label} (expires after next {unit})"
    return f"{label} x{count} (expires after {count} {unit}s)"


def _seconds_remaining(now: datetime, last_at: datetime | None, cooldown_seconds: int) -> int:
    if last_at is None:
        return 0
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    elapsed = max(0, int((now - last_at).total_seconds()))
    return max(0, cooldown_seconds - elapsed)


def _short_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds <= 0:
        return "Ready"
    minutes = (seconds + 59) // 60
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"


def _activity_status_line(player: Player, cfg: Config, now: datetime) -> str:
    activities = (
        ("Cultivate", player.last_cultivate_at, cfg.cultivate_cooldown_seconds),
        ("Gather", player.last_gather_at, cfg.gather_cooldown_seconds),
        ("Hunt", player.last_hunt_at, cfg.hunt_cooldown_seconds),
        ("Adventure", player.last_adventure_at, cfg.adventure_cooldown_seconds),
        ("Dungeon", player.last_dungeon_at, cfg.dungeon_cooldown_seconds),
    )
    parts = []
    for label, last_at, cooldown in activities:
        remaining = _seconds_remaining(now, last_at, cooldown)
        parts.append(f"{label}: {_short_time(remaining)}")
    return " · ".join(parts)


def _next_action_line(player: Player, cfg: Config, now: datetime, cap: int, cultivate_gain_line: str | None) -> str:
    if player.qi >= cap:
        return "▶ Breakthrough ready · press Breakthrough below"
    daily_remaining = _seconds_remaining(now, player.last_daily_at, cfg.daily_cooldown_seconds)
    if daily_remaining <= 0:
        return f"▶ Claim /daily (streak: {player.daily_streak})"
    cultivate_remaining = _seconds_remaining(now, player.last_cultivate_at, cfg.cultivate_cooldown_seconds)
    if cultivate_remaining <= 0:
        if cultivate_gain_line:
            return f"▶ Cultivate now · {cultivate_gain_line}"
        return "▶ Cultivate now"
    hunt_remaining = _seconds_remaining(now, player.last_hunt_at, cfg.hunt_cooldown_seconds)
    if hunt_remaining <= 0:
        return "▶ Hunt for beast cores and parts"
    gather_remaining = _seconds_remaining(now, player.last_gather_at, cfg.gather_cooldown_seconds)
    if gather_remaining <= 0:
        return "▶ Gather herbs and ore"
    adventure_remaining = _seconds_remaining(now, player.last_adventure_at, cfg.adventure_cooldown_seconds)
    if adventure_remaining <= 0:
        return "▶ Adventure for route rewards"
    return f"▶ Cultivate in {_short_time(cultivate_remaining)} · check /inventory or /techniques"


def _technique_archetype(tech) -> str:
    effect_types = {effect.type for effect in tech.effects}
    if "shield" in effect_types or "heal" in effect_types or tech.scaling_stat == "defense" or tech.role == "sustain":
        return "Defense"
    if tech.base_damage > 0:
        return "Strike"
    if tech.status_id or "apply_status" in effect_types or tech.role == "control":
        return "Control"
    if tech.slot_type == "passive":
        return "Passive"
    return "Utility"


def _technique_profile_entry(session: Session, player_id: int, technique_id: str) -> tuple[str, str] | None:
    from ..combat.loadout import get_technique_rank

    tech = get_technique(technique_id)
    if tech is None:
        return None
    rank = get_technique_rank(session, player_id, technique_id)
    tag = _technique_archetype(tech)
    power = technique_base_power(tech)
    power_bit = f" · {power}" if power is not None else ""
    return f"{tech.name} [{tag}{power_bit} · Rank {rank}]", tag


def _martial_skew_hint(active_archetypes: list[str]) -> str | None:
    if len(active_archetypes) < 3:
        return None
    counts = {tag: active_archetypes.count(tag) for tag in set(active_archetypes)}
    dominant, count = max(counts.items(), key=lambda item: item[1])
    missing = [tag for tag in ("Control", "Utility", "Passive") if counts.get(tag, 0) == 0]
    if count >= 3:
        missing_text = f", 0 {missing[0]}" if missing else ""
        return f"Kit skewed — {count} {dominant}{missing_text}; open /techniques to adjust"
    return None


def build_profile_card_data(
    session: Session,
    player: Player,
    combat: PlayerCombatStats,
    cfg: Config,
    now: datetime,
    *,
    guild_label: str,
    display_name: str,
) -> ProfileCardData:
    from ..character import get_character_modifiers
    from ..cultivation_preview import (
        format_active_cultivate_line,
        format_passive_qi_rate_line,
        preview_cultivate_qi,
    )
    from ..game_sects import get_sect_def
    from ..models import Clan
    from ..novice_trial import format_trial_progress

    ensure_starter_techniques(session, player.id)
    cap = qi_cap(player.realm_index, player.substage, player)
    qi_pct = 0 if cap <= 0 else int(min(100, player.qi / cap * 100))
    mod = get_character_modifiers(session, player)

    substage_name = SUBSTAGES[min(max(player.substage, 0), len(SUBSTAGES) - 1)].title()
    realm_detail = f"{get_realm_name(player.realm_index)} · {substage_name}"

    loadout = get_loadout(session, player.id)
    passive_id = loadout.get(PASSIVE_SLOT)
    passive_name = "none"
    if passive_id:
        passive = _technique_profile_entry(session, player.id, passive_id)
        passive_name = passive[0] if passive is not None else "none"

    active_names: list[str] = []
    active_archetypes: list[str] = []
    for slot in ACTIVE_SLOTS:
        tid = loadout.get(slot)
        if tid:
            entry = _technique_profile_entry(session, player.id, tid)
            if entry is not None:
                line, archetype = entry
                active_names.append(line)
                active_archetypes.append(archetype)

    martial_lines = [f"Passive — {passive_name}"]
    if active_names:
        martial_lines.extend(f"Active — {name}" for name in active_names[:4])
    else:
        martial_lines.append("Actives — —")
    martial_hint = _martial_skew_hint(active_archetypes)

    eq_rows = {eq.slot: eq for eq in get_player_equipment(session, player.id)}
    from ..gear_stash import resolve_equipped_gear
    from ..equipment_tiers import gear_status_label, path_label
    from ..stats import equipment_row_is_active

    equipment_views: list[EquipmentSlotView] = []
    for slot in EQUIPMENT_ORDER:
        eq = eq_rows.get(slot)
        view = resolve_equipped_gear(session, eq) if eq is not None else None
        if view is None or not view.item_id:
            equipment_views.append(
                EquipmentSlotView(
                    slot_name=slot.title(),
                    title="Empty",
                    subtitle="Forge · /equip",
                    filled=False,
                )
            )
            continue
        name = get_item_name(view.item_id)
        active = equipment_row_is_active(session, eq, player.realm_index)
        bits: list[str] = []
        if active:
            if view.stat_power:
                bits.append(f"+{view.stat_power} pow")
            if view.stat_defense:
                bits.append(f"+{view.stat_defense} def")
            if view.stat_fortune:
                bits.append(f"+{view.stat_fortune} luck")
            if view.stat_insight:
                bits.append(f"+{view.stat_insight} insight")
            grade = path_label(view.gear_grade or "external")
            subtitle = " · ".join(bits) if bits else "Forged"
            subtitle = f"{grade} · {subtitle}"
        else:
            status = gear_status_label(view, player.realm_index) or "Outgrown"
            subtitle = status
        equipment_views.append(
            EquipmentSlotView(
                slot_name=slot.title(),
                title=name[:24],
                subtitle=subtitle,
                filled=True,
            )
        )

    _ = combat

    effect_lines = [
        _short_effect_line(eff.effect_id, eff.charges, now, eff.expires_at)
        for eff in list_active_player_effects(session, player.id)[:5]
    ]

    sect_name = None
    if player.game_sect_id:
        sect_def = get_sect_def(player.game_sect_id)
        if sect_def is not None:
            sect_name = sect_def.name

    clan_name = None
    if player.clan_id is not None:
        clan = session.get(Clan, player.clan_id)
        if clan is not None:
            clan_name = clan.name

    cult_preview = preview_cultivate_qi(player, mod, cfg, now)
    passive_qi_line = plain_card_text(format_passive_qi_rate_line(cult_preview).replace("**", ""))
    active_cultivate_line = plain_card_text(
        format_active_cultivate_line(cult_preview, mod).replace("**", "").replace("_", "")
    )
    cultivate_gain_line = f"+{cult_preview.active_qi_min}-{cult_preview.active_qi_max} Qi"
    next_action_line = _next_action_line(player, cfg, now, cap, cultivate_gain_line)
    activity_line = _activity_status_line(player, cfg, now)

    pvp_total = player.pvp_wins + player.pvp_losses
    pvp_record = f"{player.pvp_wins}W / {player.pvp_losses}L" if pvp_total else "No duels yet"

    return ProfileCardData(
        dao_name=player.dao_name or display_name,
        guild_label=guild_label[:28],
        realm_banner=realm_banner(player.realm_index, player.substage),
        realm_detail=realm_detail,
        origin=player.origin or "Unknown origin",
        spirit_root=player.spirit_root or "Unrevealed root",
        karma_label=karma_tier_label(player.karma),
        karma_tier_key=karma_tier(player.karma),
        reputation_label=reputation_tier_label(player.reputation),
        substage_label=substage_name,
        adventures=player.adventures_completed,
        pvp_record=pvp_record,
        qi=player.qi,
        qi_cap=cap,
        qi_pct=qi_pct,
        breakthrough_ready=player.qi >= cap,
        spirit_stones=player.spirit_stones,
        spirit_stones_display=format_spirit_stones(player.spirit_stones),
        daily_streak=player.daily_streak,
        sect_name=sect_name,
        clan_name=clan_name,
        martial_lines=[plain_card_text(line)[:130] for line in martial_lines],
        martial_hint=plain_card_text(martial_hint)[:140] if martial_hint else None,
        equipment_slots=equipment_views,
        next_action_line=plain_card_text(next_action_line)[:150],
        activity_line=plain_card_text(activity_line)[:150],
        trial_complete=player.novice_trial_step >= 6,
        effect_lines=effect_lines,
        trial_line=plain_card_text(trial) if (trial := format_trial_progress(player)) else None,
        passive_qi_line=passive_qi_line[:140] if passive_qi_line else None,
        active_cultivate_line=active_cultivate_line[:140] if active_cultivate_line else None,
    )


def _scale(v: int) -> int:
    return v * SUPER_SAMPLE


def _font_size(points: int) -> int:
    return max(8, int(points * FONT_SCALE * SUPER_SAMPLE))


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    radius: int = 8,
    width: int = 1,
) -> None:
    r = _scale(radius)
    draw.rounded_rectangle(
        tuple(_scale(x) for x in box),
        radius=r,
        fill=fill,
        outline=outline,
        width=max(1, _scale(width)),
    )


def _fill_gradient(img: Image.Image, box: tuple[int, int, int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = (_scale(v) for v in box)
    h = max(1, y1 - y0)
    patch = Image.new("RGB", (x1 - x0, h))
    pdraw = ImageDraw.Draw(patch)
    for row in range(h):
        t = row / max(h - 1, 1)
        color = (
            int(top[0] * (1 - t) + bottom[0] * t),
            int(top[1] * (1 - t) + bottom[1] * t),
            int(top[2] * (1 - t) + bottom[2] * t),
        )
        pdraw.line([(0, row), (x1 - x0, row)], fill=color)
    img.paste(patch, (x0, y0))


def _text_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill: tuple[int, int, int],
    *,
    offset: int = 2,
) -> None:
    sx, sy = _scale(offset), _scale(offset)
    x, y = _scale(xy[0]), _scale(xy[1])
    draw.text((x + sx, y + sy), text, font=font, fill=(20, 24, 32))
    draw.text((x, y), text, font=font, fill=fill)


def _center_text(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], font, fill) -> None:
    box = tuple(_scale(x) for x in box)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = box[0] + (box[2] - box[0] - tw) // 2
    y = box[1] + (box[3] - box[1] - th) // 2
    draw.text((x, y), text, font=font, fill=fill)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, font, fill, xy: tuple[int, int]) -> None:
    text = plain_card_text(text)
    max_w = _scale(max_width)
    while text and draw.textlength(text, font=font) > max_w and len(text) > 3:
        text = text[:-4] + "..."
    draw.text((_scale(xy[0]), _scale(xy[1])), text, font=font, fill=fill)


def _section_title(draw: ImageDraw.ImageDraw, text: str, y: int, font_title, font_line) -> int:
    cy = _scale(y)
    label = f"  {text}  "
    tw = draw.textlength(label, font=font_title)
    cx = (_scale(CARD_W) - tw) // 2
    line_y = cy + _scale(6)
    draw.line([(MARGIN * SUPER_SAMPLE, line_y), (cx - _scale(8), line_y)], fill=PANEL_BORDER, width=_scale(1))
    draw.text((cx, cy), label, font=font_title, fill=TEXT_DIM)
    draw.line(
        [(cx + tw + _scale(8), line_y), (_scale(CARD_W) - MARGIN * SUPER_SAMPLE, line_y)],
        fill=PANEL_BORDER,
        width=_scale(1),
    )
    return y + 22


def _draw_stat_grid(
    draw: ImageDraw.ImageDraw,
    stats: list[StatCell],
    *,
    x: int,
    y: int,
    cols: int,
    cell_w: int,
    cell_h: int,
    gap: int,
    font_label,
    font_value,
) -> int:
    for idx, stat in enumerate(stats):
        col, row = idx % cols, idx // cols
        x0 = x + col * (cell_w + gap)
        y0 = y + row * (cell_h + gap)
        _draw_rounded_rect(draw, (x0, y0, x0 + cell_w, y0 + cell_h), PANEL_ALT, PANEL_BORDER, radius=10)
        draw.text((_scale(x0 + 12), _scale(y0 + 10)), stat.label.upper(), font=font_label, fill=TEXT_MUTED)
        _fit_text(draw, stat.value, cell_w - 20, font_value, TEXT, (x0 + 12, y0 + 30))
    rows = (len(stats) + cols - 1) // cols
    return y + rows * (cell_h + gap)


def _draw_gem_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int) -> None:
    half = _scale(size // 2)
    cx, cy = _scale(cx), _scale(cy)
    points = [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)]
    draw.polygon(points, fill=GOLD_BRIGHT, outline=GOLD_BORDER)
    draw.polygon(
        [(cx, cy - half + _scale(4)), (cx + half - _scale(4), cy), (cx, cy + _scale(2)), (cx - half + _scale(4), cy)],
        fill=GOLD,
    )


def _draw_resources_footer(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    data: ProfileCardData,
    footer_y: int,
    *,
    font_label,
    font_stones,
    font_value,
    font_hint,
) -> None:
    fy = footer_y
    _fill_gradient(img, (MARGIN, fy, CARD_W - MARGIN, fy + FOOTER_H), (18, 24, 38), BG_BOTTOM)
    _draw_rounded_rect(draw, (MARGIN, fy, CARD_W - MARGIN, fy + FOOTER_H), PANEL, PANEL_BORDER, radius=14, width=1)
    _draw_gem_icon(draw, MARGIN + 42, fy + FOOTER_H // 2, 22)
    footer = f"{data.spirit_stones_display} spirit stones  ·  /stats  ·  /tech  ·  /gear"
    _fit_text(draw, footer, CARD_W - 2 * MARGIN - 82, font_value, GOLD_BRIGHT, (MARGIN + 68, fy + 16))


def _gear_summary(equipment_slots: list[EquipmentSlotView]) -> str:
    filled = [slot for slot in equipment_slots if slot.filled]
    if not filled:
        return "Gear: none forged yet · use /forge when materials are ready"
    bits = [f"{slot.slot_name}: {slot.title}" for slot in filled]
    empty_count = len(equipment_slots) - len(filled)
    if empty_count:
        bits.append(f"{empty_count} empty")
    return " · ".join(bits)


def _has_forged_gear(data: ProfileCardData) -> bool:
    return any(slot.filled for slot in data.equipment_slots)


def _estimate_card_height(data: ProfileCardData) -> int:
    h = 108 + 102 + 72  # header, identity, qi bar
    h += 22 + 74 + 12  # next action
    martial_h = max(48, len(data.martial_lines) * 26 + 18)
    if data.martial_hint:
        martial_h += 30
    h += 22 + martial_h + 10  # martial dao
    if _has_forged_gear(data):
        h += 22 + 42 + 14  # forged gear summary
    if data.trial_line and not (data.breakthrough_ready and data.trial_complete):
        h += 38
    if data.effect_lines:
        h += 48
    return h + FOOTER_H + 20


def render_profile_card(data: ProfileCardData, avatar: Image.Image | None = None) -> bytes:
    card_h = _estimate_card_height(data)
    ss = SUPER_SAMPLE
    img = Image.new("RGB", (CARD_W * ss, card_h * ss), BG_BOTTOM)
    _fill_gradient(img, (0, 0, CARD_W, card_h), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    font_xs = load_card_font(_font_size(11))
    font_sm = load_card_font(_font_size(13))
    font_md = load_card_font(_font_size(15))
    font_lg = load_card_font(_font_size(20), bold=True)
    font_xl = load_card_font(_font_size(26), bold=True)
    font_banner = load_card_font(_font_size(32), bold=True)
    font_stones = load_card_font(_font_size(30), bold=True)
    font_stat = load_card_font(_font_size(17), bold=True)
    font_section = load_card_font(_font_size(12), bold=True)

    x_inner = MARGIN

    # Header band
    header_h = 108
    _fill_gradient(img, (0, 0, CARD_W, header_h), (22, 32, 52), BG_TOP)
    draw.line((_scale(MARGIN), _scale(header_h - 1), _scale(CARD_W - MARGIN), _scale(header_h - 1)), fill=PANEL_BORDER, width=_scale(1))

    draw.text((_scale(x_inner), _scale(18)), "CULTIVATION PROFILE", font=font_section, fill=TEXT_MUTED)
    rd_w = draw.textlength(data.realm_detail, font=font_sm)
    draw.text((_scale(CARD_W - MARGIN) - rd_w, _scale(18)), data.realm_detail, font=font_sm, fill=CYAN_BRIGHT)

    _text_shadow(draw, (x_inner, 42), data.realm_banner, font_banner, CYAN_BRIGHT, offset=2)

    karma_color = RED if data.karma_tier_key == "demonic" else GREEN if data.karma_tier_key == "righteous" else TEXT_DIM
    draw.text((_scale(x_inner), _scale(82)), data.karma_label, font=font_sm, fill=karma_color)

    # Identity
    id_y = header_h + 8
    id_h = 102
    _draw_rounded_rect(draw, (MARGIN, id_y, CARD_W - MARGIN, id_y + id_h), PANEL, PANEL_BORDER, radius=14)

    av_size = 72
    av_x, av_y = MARGIN + 14, id_y + 15
    ring = (_scale(av_x - 2), _scale(av_y - 2), _scale(av_x + av_size + 2), _scale(av_y + av_size + 2))
    draw.ellipse(ring, outline=CYAN, width=_scale(3))
    if avatar is not None:
        av = avatar.convert("RGBA").resize((av_size * ss, av_size * ss), Image.Resampling.LANCZOS)
        mask = Image.new("L", (av_size * ss, av_size * ss), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, av_size * ss, av_size * ss), fill=255)
        img.paste(av, (_scale(av_x), _scale(av_y)), mask)
    else:
        draw.ellipse(
            tuple(_scale(v) for v in (av_x + 4, av_y + 4, av_x + av_size - 4, av_y + av_size - 4)),
            fill=PANEL_ALT,
        )

    text_x = MARGIN + 104
    _text_shadow(draw, (text_x, id_y + 16), data.dao_name, font_lg, TEXT, offset=1)
    draw.text((_scale(text_x), _scale(id_y + 42)), data.guild_label, font=font_xs, fill=TEXT_MUTED)
    identity = f"{data.origin}  ·  {data.spirit_root}"
    _fit_text(draw, identity, CARD_W - text_x - 200, font_sm, TEXT_DIM, (text_x, id_y + 58))
    meta = f"{data.reputation_label}  ·  {data.adventures} adventures  ·  {data.pvp_record}"
    _fit_text(draw, meta, CARD_W - text_x - 200, font_xs, TEXT_MUTED, (text_x, id_y + 76))

    badge_x = CARD_W - MARGIN - 178
    _draw_rounded_rect(draw, (badge_x, id_y + 14, badge_x + 96, id_y + 38), PANEL_ALT, karma_color, radius=8)
    _center_text(draw, data.karma_tier_key.upper(), (badge_x, id_y + 14, badge_x + 96, id_y + 38), font_xs, karma_color)
    if data.sect_name:
        _draw_rounded_rect(draw, (badge_x, id_y + 46, badge_x + 168, id_y + 70), (24, 44, 34), GREEN, radius=8)
        _fit_text(draw, data.sect_name, 155, font_xs, GREEN, (badge_x + 10, id_y + 54))
    if data.clan_name:
        _fit_text(draw, f"Clan {data.clan_name}", 155, font_xs, TEXT_DIM, (badge_x, id_y + 78))

    # Qi bar
    qi_y = id_y + id_h + 14
    breakthrough_banner = data.breakthrough_ready and data.trial_complete
    if breakthrough_banner:
        callout = (MARGIN, qi_y, CARD_W - MARGIN, qi_y + 58)
        _fill_gradient(img, callout, GOLD_PANEL_HI, GOLD_PANEL)
        _draw_rounded_rect(draw, callout, GOLD_PANEL, GOLD_BORDER, radius=12, width=2)
        _fit_text(draw, "QI POOL FULL — attempt /breakthrough now", CARD_W - 2 * MARGIN - 24, font_md, GOLD_BRIGHT, (MARGIN + 14, qi_y + 10))
        trial = data.trial_line or "Outer Disciple Trial complete"
        _fit_text(draw, trial, CARD_W - 2 * MARGIN - 24, font_xs, TEXT_DIM, (MARGIN + 14, qi_y + 36))
        y = qi_y + 72
    else:
        draw.text((_scale(x_inner), _scale(qi_y)), "QI POOL", font=font_section, fill=TEXT_MUTED)
        qi_vals = f"{data.qi:,}  /  {data.qi_cap:,}"
        draw.text((_scale(CARD_W - MARGIN) - draw.textlength(qi_vals, font=font_md), _scale(qi_y)), qi_vals, font=font_md, fill=TEXT)

        bar_y = qi_y + 22
        bar_box = (MARGIN, bar_y, CARD_W - MARGIN, bar_y + 28)
        _draw_rounded_rect(draw, bar_box, (14, 20, 34), radius=14)
        inner_w = (bar_box[2] - bar_box[0]) - 4
        fill_w = max(12, int(inner_w * data.qi_pct / 100))
        if fill_w > 0:
            fill_box = (bar_box[0] + 2, bar_y + 2, bar_box[0] + 2 + fill_w, bar_y + 26)
            _fill_gradient(img, fill_box, CYAN_BAR_HI, CYAN_BAR)
            _draw_rounded_rect(draw, fill_box, CYAN_BAR, radius=12)
        bar_label = f"{data.qi_pct}% toward breakthrough"
        _center_text(draw, bar_label, bar_box, font_sm, TEXT)
        y = bar_y + 40

    # Next action — the card should answer what to do now before showing stats.
    y = _section_title(draw, "NEXT ACTION", y, font_section, font_xs)
    _draw_rounded_rect(draw, (MARGIN, y, CARD_W - MARGIN, y + 72), (18, 36, 46), CYAN, radius=10, width=2)
    _fit_text(draw, data.next_action_line, CARD_W - 2 * MARGIN - 24, font_md, CYAN_BRIGHT, (MARGIN + 14, y + 12))
    _fit_text(draw, data.activity_line, CARD_W - 2 * MARGIN - 24, font_xs, TEXT_DIM, (MARGIN + 14, y + 42))
    y += 84

    # Martial dao
    y = _section_title(draw, "MARTIAL DAO", y, font_section, font_xs)
    martial_h = max(48, len(data.martial_lines) * 26 + 18)
    if data.martial_hint:
        martial_h += 30
    _draw_rounded_rect(draw, (MARGIN, y, CARD_W - MARGIN, y + martial_h), PANEL_ALT, PANEL_BORDER, radius=10)
    for idx, line in enumerate(data.martial_lines):
        prefix = "○ " if idx == 0 else "◆ "
        _fit_text(draw, prefix + line, CARD_W - 2 * MARGIN - 24, font_sm, TEXT, (MARGIN + 14, y + 12 + idx * 26))
    if data.martial_hint:
        hint_y = y + 12 + len(data.martial_lines) * 26
        _fit_text(draw, "⚠ " + data.martial_hint, CARD_W - 2 * MARGIN - 24, font_xs, GOLD_BRIGHT, (MARGIN + 14, hint_y))
    y += martial_h + 10

    if _has_forged_gear(data):
        y = _section_title(draw, "FORGED GEAR", y, font_section, font_xs)
        _draw_rounded_rect(draw, (MARGIN, y, CARD_W - MARGIN, y + 40), PANEL, PANEL_BORDER, radius=10)
        _fit_text(draw, _gear_summary(data.equipment_slots), CARD_W - 2 * MARGIN - 24, font_sm, TEXT_DIM, (MARGIN + 14, y + 11))
        y += 54

    if data.trial_line and not breakthrough_banner:
        _draw_rounded_rect(draw, (MARGIN, y, CARD_W - MARGIN, y + 32), (30, 36, 56), CYAN, radius=8)
        _fit_text(draw, data.trial_line, CARD_W - 2 * MARGIN - 24, font_sm, CYAN, (MARGIN + 12, y + 8))
        y += 38

    if data.effect_lines:
        y = _section_title(draw, "LINGERING EFFECTS", y, font_section, font_xs)
        _draw_rounded_rect(draw, (MARGIN, y, CARD_W - MARGIN, y + 34), (20, 38, 30), GREEN, radius=8)
        fx = "   ·   ".join(data.effect_lines)
        _fit_text(draw, fx, CARD_W - 2 * MARGIN - 24, font_sm, GREEN, (MARGIN + 14, y + 10))
        y += 40

    # Footer — always pinned; spirit stones never clipped
    footer_y = card_h - FOOTER_H
    _draw_resources_footer(
        draw, img, data, footer_y,
        font_label=font_section, font_stones=font_stones, font_value=font_md, font_hint=font_xs,
    )

    # Outer frame
    draw.rounded_rectangle(
        (_scale(4), _scale(4), _scale(CARD_W - 4), _scale(card_h - 4)),
        radius=_scale(16),
        outline=PANEL_BORDER,
        width=_scale(2),
    )

    if ss > 1:
        img = img.resize((CARD_W, card_h), Image.Resampling.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
