from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .character import get_character_modifiers
from .content import DropEntry, get_dungeon
from .effects import consume_effect_charge
from .inventory import add_item, get_item_name, get_item_quantity, remove_item
from .manuals import normalize_manual_drops, roll_weekly_dungeon_manual
from .models import DungeonRun, Player


@dataclass
class DungeonResult:
    success: bool
    outcome: str
    dungeon_name: str
    segments_cleared: int
    drops: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    qi_delta: int = 0


def _roll_bonus_drop(rng: random.Random, drop: DropEntry, luck: float) -> tuple[str, int] | None:
    chance = drop.chance or 0.0
    if rng.random() > chance * (1.0 + luck):
        return None
    qty = rng.randint(drop.min_qty, drop.max_qty)
    return drop.item_id, qty


def run_dungeon(
    session: Session,
    player: Player,
    dungeon_id: str,
    mode: str = "solo",
    rng: random.Random | None = None,
) -> DungeonResult:
    rng = rng or random.Random()
    mode = mode.lower()

    dungeon = get_dungeon(dungeon_id)
    if dungeon is None:
        return DungeonResult(
            success=False,
            outcome="invalid",
            dungeon_name="",
            segments_cleared=0,
            messages=["That dungeon is unknown."],
        )

    if player.realm_index < dungeon.min_realm:
        return DungeonResult(
            success=False,
            outcome="underleveled",
            dungeon_name=dungeon.name,
            segments_cleared=0,
            messages=[f"You need a stronger cultivation base for {dungeon.name}."],
        )

    if get_item_quantity(session, player_id=player.id, item_id=dungeon.key_item_id) < 1:
        return DungeonResult(
            success=False,
            outcome="no_key",
            dungeon_name=dungeon.name,
            segments_cleared=0,
            messages=[f"You need a {get_item_name(dungeon.key_item_id)} to enter."],
        )

    if not remove_item(session, player.id, dungeon.key_item_id, 1):
        return DungeonResult(
            success=False,
            outcome="no_key",
            dungeon_name=dungeon.name,
            segments_cleared=0,
            messages=[f"You need a {get_item_name(dungeon.key_item_id)} to enter."],
        )

    mod = get_character_modifiers(session, player)
    messages: list[str] = [f"You descend into **{dungeon.name}** ({mode})."]
    segments_cleared = 0
    drops: dict[str, int] = {}
    qi_penalty = 0
    blood_ember_active = "blood_ember" in mod.active_effects

    for segment in range(1, dungeon.segments + 1):
        chance = dungeon.base_success + mod.dungeon_damage * 0.5 + mod.adventure_success * 0.3
        chance += mod.dungeon_luck
        chance -= mod.dungeon_risk
        chance = max(0.15, min(0.90, chance))

        if rng.random() <= chance:
            segments_cleared += 1
            messages.append(f"Encounter {segment}: cleared.")
        else:
            qi_penalty += 8 + dungeon.min_realm * 3
            messages.append(f"Encounter {segment}: you take a heavy blow.")

    boss_chance = dungeon.boss_success + mod.dungeon_damage * 0.4 + mod.dungeon_luck * 0.5
    boss_chance -= mod.dungeon_risk
    boss_chance = max(0.10, min(0.85, boss_chance))
    boss_win = rng.random() <= boss_chance
    weekly_manual_id: str | None = None

    if boss_win:
        messages.append("The cavern boss falls. The path opens.")
        for drop in dungeon.guaranteed_drops:
            qty = rng.randint(drop.min_qty, drop.max_qty)
            drops[drop.item_id] = drops.get(drop.item_id, 0) + qty
        for drop in dungeon.bonus_drops:
            rolled = _roll_bonus_drop(rng, drop, mod.drop_luck + mod.dungeon_luck)
            if rolled:
                item_id, qty = rolled
                drops[item_id] = drops.get(item_id, 0) + qty
        weekly_manual_id, weekly_note = roll_weekly_dungeon_manual(
            session, player, dungeon_id, rng, drops
        )
        if weekly_note:
            messages.append(weekly_note)
        outcome = "success"
    else:
        outcome = "fail"
        extra = int(qi_penalty * (0.5 + mod.dungeon_risk))
        if blood_ember_active:
            extra = int(extra * 1.5)
        qi_penalty += extra
        messages.append("The boss overwhelms you. You retreat through gritted teeth.")

    consume_effect_charge(session, player.id, "blood_ember")
    consume_effect_charge(session, player.id, "tempering")

    drops = normalize_manual_drops(session, player.id, drops)

    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)

    player.qi = max(0, player.qi - qi_penalty)

    rewards_json = json.dumps(
        {
            "drops": drops,
            "qi_penalty": qi_penalty,
            "boss_win": boss_win,
            "weekly_manual": weekly_manual_id if boss_win else None,
        }
    )
    session.add(
        DungeonRun(
            player_id=player.id,
            dungeon_id=dungeon_id,
            mode=mode,
            outcome=outcome,
            rewards_json=rewards_json,
        )
    )

    if drops:
        drop_lines = [f"{get_item_name(k)} ×{v}" for k, v in sorted(drops.items())]
        messages.append("Rewards: " + ", ".join(drop_lines))
    if qi_penalty:
        messages.append(f"Qi lost: {qi_penalty}.")

    if boss_win:
        from .game_sects import on_sect_activity

        messages.extend(on_sect_activity(session, player, "dungeon"))

    return DungeonResult(
        success=boss_win,
        outcome=outcome,
        dungeon_name=dungeon.name,
        segments_cleared=segments_cleared,
        drops=drops,
        messages=messages,
        qi_delta=-qi_penalty,
    )
