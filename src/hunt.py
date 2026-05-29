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

from .combat.session import COMBAT_BUSY_MESSAGE, create_active_combat, get_active_combat

from .combat_stats import compute_combat_stats, scale_monster_stats
from .ui.formatting import format_compact_number

from .area_risk import (
    apply_drop_quantity_bonus,
    realm_gap,
    underleveled_drop_bonus,
    underleveled_entry_message,
)
from .content import AreaDef, get_area, resolve_area_id

from .loot import LootDropEntry, parse_loot_table, roll_creature_loot

from .inventory import add_item, get_item_name

from .manuals import grant_manual_drop, normalize_manual_drops, pick_manual_from_pool

from .models import Player



CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "hunt_targets.json"

ELITE_MANUAL_POOLS: dict[str, str] = {
    "mortal_grove": "hunt_bamboo_elite",
    "qi_refining_cliffs": "hunt_ashen_elite",
    "foundation_ruins": "hunt_moonwell_elite",
    "core_formation_swamp": "hunt_swamp_elite",
    "nascent_soul_peak": "hunt_verdant_elite",
    "spirit_severing_abyss": "hunt_swamp_elite",
    "void_refinement_expanse": "hunt_moonwell_elite",
    "immortal_ascension_gate": "hunt_verdant_elite",
    "heavenly_transcendence_domain": "hunt_ashen_elite",
    "immortal_monarch_court": "hunt_swamp_elite",
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
    drops: tuple[LootDropEntry, ...]
    traits: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()





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

    combat_tier: str = "normal"


def hunt_elite_encounter_warning(beast_name: str) -> str:
    """Player-facing line when /hunt rolls an elite-tier beast."""
    return (
        f"⚠️ **Elite prey** — **{beast_name}** is far deadlier than the beasts you "
        "usually face here. Fight carefully."
    )





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

                    drops=parse_loot_table(b.get("drops", [])),

                    traits=tuple(b.get("traits", [])),

                    tags=tuple(b.get("tags", [])),

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
    resolved = resolve_area_id(area_id)
    return get_hunt_areas().get(resolved or area_id)





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


def scale_hunt_beast_for_area(beast: HuntBeastDef, area: AreaDef) -> BeastTemplate:
    scaled = scale_monster_stats(
        beast.hp,
        beast.attack,
        beast.defense,
        realm_index=area.min_realm,
        combat_tier=beast.combat_tier,
    )
    return BeastTemplate(
        beast_id=beast.beast_id,
        name=beast.name,
        hp=scaled["hp"],
        attack=scaled["attack"],
        defense=scaled["defense"],
        traits=beast.traits,
    )





def _validate_area(player: Player, area_id: str) -> tuple[AreaDef | None, str | None]:

    area = get_area(area_id)

    if area is None:

        return None, "That region is unknown."

    return area, None





def _roll_hunt_drops(
    session: Session,
    player: Player,
    beast: HuntBeastDef,
    rng: random.Random,
    *,
    area_id: str | None = None,
    area_min_realm: int = 0,
) -> dict[str, int]:
    from .combat_stats import compute_combat_stats

    mod = get_character_modifiers(session, player)
    stats = compute_combat_stats(player, session, mod)
    tier = beast.combat_tier if beast.combat_tier in {"normal", "elite", "boss"} else "elite"
    drops = roll_creature_loot(
        beast.drops,
        rng,
        combat_tier=tier,
        luck=stats.luck,
        drop_luck=mod.drop_luck,
        player_realm_index=player.realm_index,
        area_min_realm=area_min_realm,
    )
    if beast.combat_tier == "elite" and area_id:
        pool_id = ELITE_MANUAL_POOLS.get(area_id)
        if pool_id and rng.random() <= ELITE_MANUAL_CHANCE:
            karma = player.karma
            manual_id = pick_manual_from_pool(
                pool_id,
                rng,
                session=session,
                player_id=player.id,
                karma=karma,
                max_rarity="rare",
            )
            if manual_id is not None:
                grant_manual_drop(session, player.id, manual_id, drops)
    return drops


def _ensure_hunt_victory_drop(drops: dict[str, int], beast: HuntBeastDef) -> dict[str, int]:
    """Hunt wins should never feel empty; guarantee one basic material if rolls miss."""
    if drops:
        return drops
    fallback = next(
        (
            entry
            for entry in beast.drops
            if entry.rarity == "common" and not entry.item_id.startswith("manual_")
        ),
        None,
    )
    if fallback is None:
        fallback = next(
            (entry for entry in beast.drops if not entry.item_id.startswith("manual_")),
            None,
        )
    if fallback is None:
        return drops
    return {fallback.item_id: max(1, fallback.min_qty)}


def get_hunt_beast_def(area_id: str, beast_id: str) -> HuntBeastDef | None:
    area_id = resolve_area_id(area_id) or area_id
    hunt_def = get_hunt_area(area_id)

    if hunt_def is None:

        return None

    return next((b for b in hunt_def.beasts if b.beast_id == beast_id), None)


def find_hunt_beast_by_id(beast_id: str) -> tuple[HuntBeastDef, str] | None:
    """Return (beast, area_id) for a beast id across all hunt areas."""
    for area_id, hunt_def in get_hunt_areas().items():
        for beast in hunt_def.beasts:
            if beast.beast_id == beast_id:
                return beast, area_id
    return None


def _beast_tag_set(beast_id: str, name: str, explicit_tags: tuple[str, ...]) -> set[str]:
    tags = {t.lower() for t in explicit_tags if t}
    hay_id = beast_id.lower()
    hay_name = name.lower()
    if "wolf" in hay_id or "wolf" in hay_name:
        tags.add("wolf")
    if "hound" in hay_id or "hound" in hay_name:
        tags.update({"hound", "wolf"})
    if "hare" in hay_id or "hare" in hay_name:
        tags.add("hare")
    if "scorpion" in hay_id or "scorpion" in hay_name:
        tags.add("scorpion")
    if "devourer" in hay_id or "devourer" in hay_name:
        tags.add("devourer")
    if "serpent" in hay_id or "serpent" in hay_name:
        tags.add("serpent")
    return tags


def beast_matches_sect_tag(beast_id: str, tag: str) -> bool:
    """Whether a defeated hunt target counts toward a sect task beast_tag."""
    if not tag:
        return True
    needle = tag.lower()
    if needle in beast_id.lower():
        return True
    inferred = _beast_tag_set(beast_id, beast_id.replace("_", " "), ())
    if needle in inferred:
        return True
    found = find_hunt_beast_by_id(beast_id)
    if found is None:
        return False
    beast, _ = found
    return needle in _beast_tag_set(beast.beast_id, beast.name, beast.tags)


def list_hunt_beasts_for_sect_tag(tag: str) -> list[tuple[str, str, str]]:
    """(beast_id, beast_name, area_id) entries that satisfy the sect task tag."""
    needle = tag.lower()
    matches: list[tuple[str, str, str]] = []
    for area_id, hunt_def in get_hunt_areas().items():
        for beast in hunt_def.beasts:
            if needle in _beast_tag_set(beast.beast_id, beast.name, beast.tags):
                matches.append((beast.beast_id, beast.name, area_id))
    return matches





def start_hunt_combat(

    session: Session,

    player: Player,

    area_id: str,

    rng: random.Random | None = None,

) -> tuple[HuntCombatStart | None, str | None]:

    rng = rng or random.Random()
    area_id = resolve_area_id(area_id) or area_id

    area, err = _validate_area(player, area_id)

    if err or area is None:

        return None, err



    if get_active_combat(session, player.id) is not None:

        return None, COMBAT_BUSY_MESSAGE



    hunt_def = get_hunt_area(area_id)

    if hunt_def is None:

        return None, "No beasts are configured for this region."



    beast_def = _pick_beast(hunt_def.beasts, rng)

    if beast_def is None:

        return None, "No prey stirs in this region."



    mod = get_character_modifiers(session, player)

    stats = compute_combat_stats(player, session, mod)

    beast = scale_hunt_beast_for_area(beast_def, area)

    opponent = opponent_from_beast(beast)

    state = create_combat_state(

        stats,

        opponent,

        context="hunt",

        context_meta={"area_id": area_id, "beast_id": beast_def.beast_id},

    )

    state.log.insert(0, hunt_def.flavor)
    log_idx = 1
    if beast_def.combat_tier == "elite":
        state.log.insert(log_idx, hunt_elite_encounter_warning(beast_def.name))
        log_idx += 1
    gap = realm_gap(player, area)
    if gap > 0:
        state.log.insert(log_idx, underleveled_entry_message(area, gap))
    active = create_active_combat(session, player, state, context="hunt", context_key=area_id)



    return (

        HuntCombatStart(

            combat_id=active.id,

            area_name=area.name,

            beast_name=beast_def.name,

            beast_hp=beast.hp,

            player_hp=stats.hp,

            player_max_hp=stats.max_hp,

            flavor=hunt_def.flavor,

            area_id=area_id,

            beast_id=beast_def.beast_id,

            combat_tier=beast_def.combat_tier,

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
    area_id = resolve_area_id(area_id) or area_id

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

        drops = _roll_hunt_drops(
            session, player, beast_def, rng, area_id=area_id, area_min_realm=area.min_realm
        )

        drops = normalize_manual_drops(session, player.id, drops)
        drops = _ensure_hunt_victory_drop(drops, beast_def)
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
    area_id = resolve_area_id(area_id) or area_id

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

    beast = scale_hunt_beast_for_area(beast_def, area)



    mod = get_character_modifiers(session, player)

    stats = compute_combat_stats(player, session, mod)

    combat = resolve_auto_combat(stats, beast, mod, rng)



    gap = realm_gap(player, area)
    messages = [hunt_def.flavor]
    if gap > 0:
        messages.append(underleveled_entry_message(area, gap))
    messages.append(f"You encounter **{beast.name}** (HP {format_compact_number(beast.hp)}).")

    messages.extend(combat.log_lines)



    drops: dict[str, int] = {}

    if combat.victory:

        drops = _roll_hunt_drops(
            session, player, beast_def, rng, area_id=area_id, area_min_realm=area.min_realm
        )

        drops = normalize_manual_drops(session, player.id, drops)
        drops = _ensure_hunt_victory_drop(drops, beast_def)
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

