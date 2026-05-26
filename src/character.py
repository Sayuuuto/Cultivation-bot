from __future__ import annotations

from sqlalchemy.orm import Session

from .content import get_origin_modifiers, get_spirit_root_modifiers
from .effects import apply_effects_from_db
from .equipment import get_player_affix_modifiers
from .stats import equipment_stats_to_modifiers, get_total_equipment_stats
from .game import moral_breakthrough_modifiers
from .models import Player
from .modifiers import CharacterModifiers


def _apply_additive(mod: CharacterModifiers, values: dict[str, float]) -> None:
    for key, val in values.items():
        if not hasattr(mod, key):
            continue
        current = getattr(mod, key)
        if key.endswith("_mult") or key in ("stamina_efficiency", "rare_event_mult"):
            setattr(mod, key, current * val)
        else:
            setattr(mod, key, current + val)


def get_character_modifiers(session: Session, player: Player) -> CharacterModifiers:
    mod = CharacterModifiers()

    origin = get_origin_modifiers(player.origin)
    if origin:
        _apply_additive(mod, origin.values)

    root = get_spirit_root_modifiers(player.spirit_root)
    if root:
        _apply_additive(mod, root.values)

    _, setback_mult = moral_breakthrough_modifiers(player.moral_path)
    mod.breakthrough_setback_mult *= setback_mult

    affix_vals = get_player_affix_modifiers(session, player.id)
    _apply_additive(mod, affix_vals)

    gear_stats = get_total_equipment_stats(session, player.id)
    _apply_additive(mod, equipment_stats_to_modifiers(gear_stats))

    apply_effects_from_db(session, mod, player.id)

    return mod


def compute_adventure_power(mod: CharacterModifiers, player: Player) -> float:
    base = player.realm_index * 10 + player.substage * 3 + player.qi / 100
    base *= 1.0 + mod.adventure_success + mod.dungeon_damage * 0.5 + mod.pvp_power * 0.3
    return base


def compute_adventure_defense(mod: CharacterModifiers) -> float:
    return 1.0 + mod.adventure_defense + mod.dungeon_defense
