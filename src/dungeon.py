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


def _roll_bonus_drop(
    rng: random.Random,
    drop: DropEntry,
    luck: float,
    drop_luck: float,
    *,
    player_realm_index: int,
    area_min_realm: int,
) -> tuple[str, int] | None:
    from .loot import effective_drop_chance

    base = drop.chance if drop.chance is not None else effective_drop_chance(
        drop.rarity,
        combat_tier="boss",
        luck=luck,
        drop_luck=drop_luck,
        player_realm_index=player_realm_index,
        area_min_realm=area_min_realm,
    )
    if rng.random() > base:
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
    for segment in range(1, dungeon.segments + 1):
        chance = dungeon.base_success + mod.dungeon_damage * 0.5 + mod.adventure_success * 0.3
        chance += mod.dungeon_luck
        chance -= mod.dungeon_risk
        chance = max(0.15, min(0.90, chance))

        if rng.random() <= chance:
            segments_cleared += 1
            messages.append(f"Encounter {segment}: cleared.")
        else:
            messages.append(
                f"Encounter {segment}: you take a heavy blow — your formation holds your qi intact."
            )

    boss_chance = dungeon.boss_success + mod.dungeon_damage * 0.4 + mod.dungeon_luck * 0.5
    boss_chance -= mod.dungeon_risk
    boss_chance = max(0.10, min(0.85, boss_chance))
    boss_win = rng.random() <= boss_chance
    weekly_manual_id: str | None = None

    if boss_win:
        messages.append("The cavern boss falls. The path opens.")
        from .combat_stats import compute_combat_stats
        from .loot import LootDropEntry, merge_loot_dicts, roll_creature_loot

        stats = compute_combat_stats(player, session, mod)
        boss_table = tuple(
            LootDropEntry(item_id=d.item_id, rarity=d.rarity, min_qty=d.min_qty, max_qty=d.max_qty)
            for d in dungeon.guaranteed_drops
        )
        boss_loot = roll_creature_loot(
            boss_table,
            rng,
            combat_tier="boss",
            luck=stats.luck,
            drop_luck=mod.drop_luck + mod.dungeon_luck,
            player_realm_index=player.realm_index,
            area_min_realm=dungeon.min_realm,
            skip_manuals=False,
        )
        drops = merge_loot_dicts(drops, boss_loot)
        for drop in dungeon.bonus_drops:
            rolled = _roll_bonus_drop(
                rng,
                drop,
                stats.luck,
                mod.drop_luck + mod.dungeon_luck,
                player_realm_index=player.realm_index,
                area_min_realm=dungeon.min_realm,
            )
            if rolled:
                item_id, qty = rolled
                drops[item_id] = drops.get(item_id, 0) + qty
        weekly_manual_id, weekly_note = roll_weekly_dungeon_manual(
            session, player, dungeon_id, rng, drops
        )
        if weekly_note:
            messages.append(weekly_note)
        from .spirit_stone_drops import grant_solo_dungeon_clear_stones

        stones_gain, stone_msg = grant_solo_dungeon_clear_stones(
            session,
            player,
            rng,
            dungeon_min_realm=dungeon.min_realm,
        )
        if stone_msg:
            messages.append(stone_msg)
        outcome = "success"
    else:
        outcome = "fail"
        messages.append(
            "The boss overwhelms you. You retreat through gritted teeth — cultivation qi untouched."
        )

    consume_effect_charge(session, player.id, "blood_ember")
    consume_effect_charge(session, player.id, "tempering")

    drops = normalize_manual_drops(session, player.id, drops)

    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)

    rewards_json = json.dumps(
        {
            "drops": drops,
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
        qi_delta=0,
    )
