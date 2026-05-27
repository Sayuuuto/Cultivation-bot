from __future__ import annotations



import json

import random

from dataclasses import dataclass

from pathlib import Path



from sqlalchemy.orm import Session



from .auto_combat import AutoCombatResult

from .auto_combat import BeastTemplate

from .character import get_character_modifiers

from .combat.engine import create_combat_state, opponent_from_beast

from .combat.session import create_active_combat, get_active_combat

from .combat_stats import compute_combat_stats

from .content import get_area

from .gather import GatherNode

from .inventory import add_item, get_item_name

from .manuals import normalize_manual_drops

from .models import Player



CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "hunt_targets.json"



_hunt_config: dict | None = None





@dataclass(frozen=True)

class HuntBeastDef:

    beast_id: str

    name: str

    weight: int

    hp: int

    attack: int

    defense: int

    combat_tier: str
    drops: tuple[GatherNode, ...]
    traits: tuple[str, ...] = ()





@dataclass(frozen=True)

class HuntAreaDef:

    area_id: str

    flavor: str

    beasts: tuple[HuntBeastDef, ...]





@dataclass(frozen=True)

class HuntResult:

    success: bool

    area_name: str

    combat: AutoCombatResult | None

    drops: dict[str, int]

    messages: list[str]





@dataclass(frozen=True)

class HuntCombatStart:

    combat_id: int

    area_name: str

    beast_name: str

    beast_hp: int

    player_hp: int

    player_max_hp: int

    flavor: str

    area_id: str

    beast_id: str





def _load_hunt_config() -> dict[str, HuntAreaDef]:

    global _hunt_config

    if _hunt_config is None:

        with CONFIG_PATH.open(encoding="utf-8") as f:

            raw = json.load(f)

        parsed: dict[str, HuntAreaDef] = {}

        for area_id, data in raw.items():

            beasts = tuple(

                HuntBeastDef(

                    beast_id=b["beast_id"],

                    name=b["name"],

                    weight=b["weight"],

                    hp=b["hp"],

                    attack=b["attack"],

                    defense=b["defense"],

                    combat_tier=b.get("combat_tier", "normal"),

                    drops=tuple(

                        GatherNode(

                            item_id=d["item_id"],

                            weight=d["weight"],

                            min_qty=d["min"],

                            max_qty=d["max"],

                        )

                        for d in b.get("drops", [])

                    ),

                    traits=tuple(b.get("traits", [])),

                )

                for b in data["beasts"]

            )

            parsed[area_id] = HuntAreaDef(

                area_id=area_id,

                flavor=data.get("flavor", "You track prey through the wilds."),

                beasts=beasts,

            )

        _hunt_config = parsed

    return _hunt_config





def get_hunt_areas() -> dict[str, HuntAreaDef]:

    return _load_hunt_config()





def get_hunt_area(area_id: str) -> HuntAreaDef | None:

    return get_hunt_areas().get(area_id)





def _pick_beast(beasts: tuple[HuntBeastDef, ...], rng: random.Random) -> HuntBeastDef | None:

    if not beasts:

        return None

    total = sum(b.weight for b in beasts)

    if total <= 0:

        return None

    roll = rng.randint(1, total)

    cumulative = 0

    for beast in beasts:

        cumulative += beast.weight

        if roll <= cumulative:

            return beast

    return beasts[-1]





def _validate_area(player: Player, area_id: str) -> tuple[str | None, str | None]:

    area = get_area(area_id)

    if area is None:

        return None, "That region is unknown."

    if player.realm_index < area.min_realm:

        return None, f"You are not ready for **{area.name}**. {area.recommended_text} recommended."

    return area.name, None





def _roll_hunt_drops(beast: HuntBeastDef, rng: random.Random) -> dict[str, int]:

    drops: dict[str, int] = {}

    for entry in beast.drops:

        if rng.randint(1, 100) > entry.weight:

            continue

        qty = rng.randint(entry.min_qty, entry.max_qty)

        drops[entry.item_id] = drops.get(entry.item_id, 0) + qty

    if not drops and beast.drops:

        fallback = beast.drops[0]

        drops[fallback.item_id] = rng.randint(fallback.min_qty, fallback.max_qty)

    return drops





def get_hunt_beast_def(area_id: str, beast_id: str) -> HuntBeastDef | None:

    hunt_def = get_hunt_area(area_id)

    if hunt_def is None:

        return None

    return next((b for b in hunt_def.beasts if b.beast_id == beast_id), None)





def start_hunt_combat(

    session: Session,

    player: Player,

    area_id: str,

    rng: random.Random | None = None,

) -> tuple[HuntCombatStart | None, str | None]:

    rng = rng or random.Random()

    area_name, err = _validate_area(player, area_id)

    if err:

        return None, err



    if get_active_combat(session, player.id) is not None:

        return None, "You are already in combat. Finish or flee first."



    hunt_def = get_hunt_area(area_id)

    if hunt_def is None:

        return None, "No beasts are configured for this region."



    beast_def = _pick_beast(hunt_def.beasts, rng)

    if beast_def is None:

        return None, "No prey stirs in this region."



    mod = get_character_modifiers(session, player)

    stats = compute_combat_stats(player, session, mod)

    beast = BeastTemplate(

        beast_id=beast_def.beast_id,

        name=beast_def.name,

        hp=beast_def.hp,

        attack=beast_def.attack,

        defense=beast_def.defense,

        traits=beast_def.traits,

    )

    opponent = opponent_from_beast(beast)

    state = create_combat_state(

        stats,

        opponent,

        context="hunt",

        context_meta={"area_id": area_id, "beast_id": beast_def.beast_id},

    )

    state.log.insert(0, hunt_def.flavor)

    active = create_active_combat(session, player, state, context="hunt", context_key=area_id)



    return (

        HuntCombatStart(

            combat_id=active.id,

            area_name=area_name or area_id,

            beast_name=beast_def.name,

            beast_hp=beast_def.hp,

            player_hp=stats.hp,

            player_max_hp=stats.max_hp,

            flavor=hunt_def.flavor,

            area_id=area_id,

            beast_id=beast_def.beast_id,

        ),

        None,

    )





def finalize_hunt_combat(

    session: Session,

    player: Player,

    area_id: str,

    beast_id: str,

    victory: bool,

    rng: random.Random | None = None,

) -> HuntResult:

    rng = rng or random.Random()

    area_name, err = _validate_area(player, area_id)

    if err:

        return HuntResult(success=False, area_name=area_id, combat=None, drops={}, messages=[err])



    hunt_def = get_hunt_area(area_id)

    beast_def = get_hunt_beast_def(area_id, beast_id)

    if hunt_def is None or beast_def is None:

        return HuntResult(

            success=False,

            area_name=area_name or area_id,

            combat=None,

            drops={},

            messages=["The hunt trail went cold."],

        )



    messages: list[str] = []

    drops: dict[str, int] = {}

    if victory:

        drops = _roll_hunt_drops(beast_def, rng)

        drops = normalize_manual_drops(session, player.id, drops)

        from .novice_trial import apply_first_hunt_bonus, on_hunt_victory

        trial_drop_msg = apply_first_hunt_bonus(session, player, drops)
        trial_msgs = on_hunt_victory(player)

        for item_id, qty in drops.items():

            add_item(session, player.id, item_id, qty)

        messages.append(f"You defeated **{beast_def.name}**.")
        if trial_drop_msg:
            messages.append(trial_drop_msg)
        for note in trial_msgs:
            messages.append(note)

        if drops:

            loot_lines = ", ".join(f"**{get_item_name(i)}** ×{q}" for i, q in drops.items())

            messages.append(f"Spoils: {loot_lines}.")

        else:

            messages.append("The beast yielded no usable materials.")

    else:

        messages.append(f"**{beast_def.name}** got away or drove you off.")



    return HuntResult(

        success=victory,

        area_name=area_name or area_id,

        combat=None,

        drops=drops,

        messages=messages,

    )





def run_hunt(

    session: Session,

    player: Player,

    area_id: str,

    rng: random.Random | None = None,

) -> HuntResult:

    """Auto-resolve hunt via finish button logic (tests / fallback)."""

    from .auto_combat import resolve_auto_combat



    rng = rng or random.Random()

    area_name, err = _validate_area(player, area_id)

    if err:

        return HuntResult(success=False, area_name=area_id, combat=None, drops={}, messages=[err])



    hunt_def = get_hunt_area(area_id)

    if hunt_def is None:

        return HuntResult(

            success=False,

            area_name=area_name or area_id,

            combat=None,

            drops={},

            messages=["No beasts are configured for this region."],

        )



    beast_def = _pick_beast(hunt_def.beasts, rng)

    if beast_def is None:

        return HuntResult(

            success=False,

            area_name=area_name or area_id,

            combat=None,

            drops={},

            messages=["No prey stirs in this region."],

        )

    beast = BeastTemplate(

        beast_id=beast_def.beast_id,

        name=beast_def.name,

        hp=beast_def.hp,

        attack=beast_def.attack,

        defense=beast_def.defense,

        traits=beast_def.traits,

    )



    mod = get_character_modifiers(session, player)

    stats = compute_combat_stats(player, session, mod)

    combat = resolve_auto_combat(stats, beast, mod, rng)



    messages = [hunt_def.flavor, f"You encounter **{beast.name}** (HP {beast.hp})."]

    messages.extend(combat.log_lines)



    drops: dict[str, int] = {}

    if combat.victory:

        drops = _roll_hunt_drops(beast_def, rng)

        drops = normalize_manual_drops(session, player.id, drops)

        for item_id, qty in drops.items():

            add_item(session, player.id, item_id, qty)

        if drops:

            loot_lines = ", ".join(f"**{get_item_name(i)}** ×{q}" for i, q in drops.items())

            messages.append(f"Spoils: {loot_lines}.")

        else:

            messages.append("The beast yielded no usable materials.")



    return HuntResult(

        success=combat.victory,

        area_name=area_name or area_id,

        combat=combat,

        drops=drops,

        messages=messages,

    )

