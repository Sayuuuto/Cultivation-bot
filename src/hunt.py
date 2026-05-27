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

from .area_risk import (
    apply_drop_quantity_bonus,
    realm_gap,
    underleveled_drop_bonus,
    underleveled_entry_message,
)
from .content import AreaDef, get_area

from .gather import GatherNode

from .inventory import add_item, get_item_name

from .manuals import grant_manual_drop, normalize_manual_drops, pick_manual_from_pool

from .models import Player



CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "hunt_targets.json"

ELITE_MANUAL_POOLS: dict[str, str] = {
    "bamboo_grove": "hunt_bamboo_elite",
    "ashen_cliff": "hunt_ashen_elite",
    "moonwell_ruins": "hunt_moonwell_elite",
    "mistwood_village": "hunt_mistwood_elite",
    "verdant_depths": "hunt_verdant_elite",
    "cursed_swamp": "hunt_swamp_elite",
}

ELITE_MANUAL_CHANCE = 0.85



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





def _validate_area(player: Player, area_id: str) -> tuple[AreaDef | None, str | None]:

    area = get_area(area_id)

    if area is None:

        return None, "That region is unknown."

    return area, None





def _roll_hunt_drops(
    session: Session,
    player_id: int,
    beast: HuntBeastDef,
    rng: random.Random,
    *,
    area_id: str | None = None,
) -> dict[str, int]:
    drops: dict[str, int] = {}
    for entry in beast.drops:
        if entry.item_id.startswith("manual_"):
            continue
        if rng.randint(1, 100) > entry.weight:
            continue
        qty = rng.randint(entry.min_qty, entry.max_qty)
        drops[entry.item_id] = drops.get(entry.item_id, 0) + qty
    if not drops and beast.drops:
        fallback = next((d for d in beast.drops if not d.item_id.startswith("manual_")), beast.drops[0])
        if not fallback.item_id.startswith("manual_"):
            drops[fallback.item_id] = rng.randint(fallback.min_qty, fallback.max_qty)
    if beast.combat_tier == "elite" and area_id:
        pool_id = ELITE_MANUAL_POOLS.get(area_id)
        if pool_id and rng.random() <= ELITE_MANUAL_CHANCE:
            player = session.get(Player, player_id)
            karma = player.karma if player is not None else 0
            manual_id = pick_manual_from_pool(
                pool_id,
                rng,
                session=session,
                player_id=player_id,
                karma=karma,
                max_rarity="rare",
            )
            if manual_id is not None:
                grant_manual_drop(session, player_id, manual_id, drops)
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

    area, err = _validate_area(player, area_id)

    if err or area is None:

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
    gap = realm_gap(player, area)
    if gap > 0:
        state.log.insert(1, underleveled_entry_message(area, gap))
    active = create_active_combat(session, player, state, context="hunt", context_key=area_id)



    return (

        HuntCombatStart(

            combat_id=active.id,

            area_name=area.name,

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

    area, err = _validate_area(player, area_id)

    if err or area is None:

        return HuntResult(success=False, area_name=area_id, combat=None, drops={}, messages=[err or "Unknown area."])



    hunt_def = get_hunt_area(area_id)

    beast_def = get_hunt_beast_def(area_id, beast_id)

    if hunt_def is None or beast_def is None:

        return HuntResult(

            success=False,

            area_name=area.name,

            combat=None,

            drops={},

            messages=["The hunt trail went cold."],

        )



    messages: list[str] = []

    drops: dict[str, int] = {}

    if victory:

        drops = _roll_hunt_drops(session, player.id, beast_def, rng, area_id=area_id)

        drops = normalize_manual_drops(session, player.id, drops)
        gap = realm_gap(player, area)
        drops = apply_drop_quantity_bonus(drops, gap)

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
            if gap > 0:
                bonus_pct = int((underleveled_drop_bonus(gap) - 1.0) * 100)
                messages.append(
                    f"_The beasts of **{area.name}** yielded **+{bonus_pct}%** spoils for your daring._"
                )

        else:

            messages.append("The beast yielded no usable materials.")

    else:

        messages.append(f"**{beast_def.name}** got away or drove you off.")



    return HuntResult(

        success=victory,

        area_name=area.name,

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

    area, err = _validate_area(player, area_id)

    if err or area is None:

        return HuntResult(success=False, area_name=area_id, combat=None, drops={}, messages=[err or "Unknown area."])



    hunt_def = get_hunt_area(area_id)

    if hunt_def is None:

        return HuntResult(

            success=False,

            area_name=area.name,

            combat=None,

            drops={},

            messages=["No beasts are configured for this region."],

        )



    beast_def = _pick_beast(hunt_def.beasts, rng)

    if beast_def is None:

        return HuntResult(

            success=False,

            area_name=area.name,

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



    gap = realm_gap(player, area)
    messages = [hunt_def.flavor]
    if gap > 0:
        messages.append(underleveled_entry_message(area, gap))
    messages.append(f"You encounter **{beast.name}** (HP {beast.hp}).")

    messages.extend(combat.log_lines)



    drops: dict[str, int] = {}

    if combat.victory:

        drops = _roll_hunt_drops(session, player.id, beast_def, rng, area_id=area_id)

        drops = normalize_manual_drops(session, player.id, drops)
        drops = apply_drop_quantity_bonus(drops, gap)

        for item_id, qty in drops.items():

            add_item(session, player.id, item_id, qty)

        if drops:

            loot_lines = ", ".join(f"**{get_item_name(i)}** ×{q}" for i, q in drops.items())

            messages.append(f"Spoils: {loot_lines}.")
            if gap > 0:
                bonus_pct = int((underleveled_drop_bonus(gap) - 1.0) * 100)
                messages.append(
                    f"_The beasts of **{area.name}** yielded **+{bonus_pct}%** spoils for your daring._"
                )

        else:

            messages.append("The beast yielded no usable materials.")

        from .game_sects import on_sect_activity

        messages.extend(
            on_sect_activity(
                session,
                player,
                "hunt",
                area_id=area_id,
                beast_id=beast_def.beast_id,
            )
        )



    return HuntResult(

        success=combat.victory,

        area_name=area.name,

        combat=combat,

        drops=drops,

        messages=messages,

    )

