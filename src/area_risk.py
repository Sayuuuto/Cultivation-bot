from __future__ import annotations

from .content import AreaDef
from .game import REALMS
from .models import Player


def _realm_label(realm_index: int) -> str:
    idx = max(0, min(realm_index, len(REALMS) - 1))
    return REALMS[idx]


def realm_gap(player: Player, area: AreaDef) -> int:
    """How many recommended realm steps the player is below this area."""
    return max(0, area.min_realm - player.realm_index)


def is_underleveled(player: Player, area: AreaDef) -> bool:
    return realm_gap(player, area) > 0


def adventure_realm_modifiers(gap: int) -> tuple[float, float]:
    """Return (success_penalty, minimum_success_chance) for choice segments."""
    if gap <= 0:
        return 0.0, 0.12
    if gap == 1:
        return 0.28, 0.08
    if gap == 2:
        return 0.46, 0.04
    return 0.55, 0.02


def underleveled_drop_bonus(gap: int) -> float:
    """Extra loot multiplier when a below-realm player succeeds."""
    if gap <= 0:
        return 1.0
    if gap == 1:
        return 1.30
    if gap == 2:
        return 1.75
    return 2.25


def danger_label(gap: int) -> str:
    if gap <= 0:
        return ""
    if gap == 1:
        return "dangerous"
    if gap == 2:
        return "deadly"
    return "suicidal"


def format_area_choice_label(player: Player, area: AreaDef) -> str:
    gap = realm_gap(player, area)
    if gap <= 0:
        return f"{area.name} ({area.recommended_text})"
    return f"{area.name} ({danger_label(gap)} — {area.recommended_text})"


def underleveled_entry_message(area: AreaDef, gap: int) -> str:
    label = danger_label(gap)
    return (
        f"_You wander into **{area.name}** far above your cultivation "
        f"({label}). The beasts here could end you — but their spoils "
        f"would be legendary if you survive._"
    )


def apply_drop_quantity_bonus(drops: dict[str, int], gap: int) -> dict[str, int]:
    mult = underleveled_drop_bonus(gap)
    if mult <= 1.0 or not drops:
        return drops
    return {item_id: max(1, int(qty * mult)) for item_id, qty in drops.items()}


def player_realm_status(player: Player | None, area: AreaDef) -> str:
    if player is None:
        return f"Recommended: **{_realm_label(area.min_realm)}** or higher."
    gap = realm_gap(player, area)
    if gap <= 0:
        return f"You match this zone (**{_realm_label(player.realm_index)}**)."
    bonus = int((underleveled_drop_bonus(gap) - 1.0) * 100)
    return (
        f"**{_realm_label(player.realm_index)}** in a **{_realm_label(area.min_realm)}** "
        f"zone — {danger_label(gap)}. Foes are far stronger; success grants **+{bonus}%** loot."
    )
