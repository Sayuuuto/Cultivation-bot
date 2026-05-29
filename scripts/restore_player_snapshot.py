"""
Restore a player from a JSON snapshot (scripts/profiles/*.json).

Usage:
  set DATABASE_PATH=C:\\path\\to\\cultivation_bot.sqlite3
  py scripts/restore_player_snapshot.py scripts/profiles/void_great_emperor.json --dry-run
  py scripts/restore_player_snapshot.py scripts/profiles/void_great_emperor.json --discord-id YOUR_ID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from src.combat.loadout import equip_technique, learn_technique
from src.db import get_session
from src.inventory import get_item_def, get_player_inventory
from src.models import InventoryItem, Player, PlayerTechnique, TechniqueLoadout
from src.novice_trial import TRIAL_COMPLETE_STEP
from src.realms import SUBSTAGES, get_realm_name


def _find_player(session, profile: dict, discord_id: str | None, guild_id: str | None) -> Player | None:
    if discord_id:
        stmt = select(Player).where(Player.discord_id == discord_id)
        if guild_id:
            stmt = stmt.where(Player.guild_id == guild_id)
        rows = list(session.execute(stmt).scalars().all())
        return rows[0] if rows else None

    match = profile.get("match") or {}
    dao_name = (match.get("dao_name") or "").strip()
    if dao_name:
        stmt = select(Player).where(Player.dao_name == dao_name)
        if guild_id:
            stmt = stmt.where(Player.guild_id == guild_id)
        rows = list(session.execute(stmt).scalars().all())
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            print(f"Multiple players named {dao_name!r}; pass --discord-id", file=sys.stderr)
            for p in rows:
                print(f"  id={p.id} discord_id={p.discord_id} guild_id={p.guild_id}", file=sys.stderr)
            return None
    return None


def restore_from_profile(
    session,
    player: Player,
    profile: dict,
    *,
    dry_run: bool,
    clear_other_inventory: bool,
) -> None:
    prog = profile["progression"]
    realm_index = int(prog["realm_index"])
    substage = int(prog["substage"])
    realm_label = f"{get_realm_name(realm_index)} {SUBSTAGES[substage]}"

    print(f"Player: {player.dao_name or player.discord_username} (id={player.id}, discord={player.discord_id})")
    print(
        f"  realm: {get_realm_name(player.realm_index)} {SUBSTAGES[player.substage]} "
        f"-> {realm_label}"
    )
    print(f"  qi: {player.qi} -> {prog['qi']}")
    print(f"  spirit_stones: {player.spirit_stones} -> {prog['spirit_stones']}")

    raw_techniques = list(profile["techniques"])
    technique_ranks: dict[str, int] = dict(profile.get("technique_ranks") or {})
    techniques: list[str] = []
    for entry in raw_techniques:
        if isinstance(entry, str):
            techniques.append(entry)
        elif isinstance(entry, dict):
            tid = entry.get("technique_id") or entry.get("id")
            if tid:
                techniques.append(str(tid))
                if "rank" in entry:
                    technique_ranks[str(tid)] = int(entry["rank"])
    loadout = profile.get("loadout") or {}
    inventory = profile.get("inventory") or {}
    schema_version = profile.get("profile_schema_version", 1)

    print(f"  profile_schema_version: {schema_version}")
    print(f"  techniques: {len(techniques)} arts")
    print(f"  loadout: {loadout}")
    print(f"  inventory: {len(inventory)} item types, {sum(inventory.values())} stacks")

    if dry_run:
        return

    player.realm_index = realm_index
    player.substage = substage
    player.qi = int(prog["qi"])
    player.spirit_stones = int(prog["spirit_stones"])
    player.passive_qi_bank = int(prog.get("passive_qi_bank", 0))
    player.novice_trial_step = max(int(player.novice_trial_step or 0), TRIAL_COMPLETE_STEP)

    session.execute(delete(PlayerTechnique).where(PlayerTechnique.player_id == player.id))
    session.execute(delete(TechniqueLoadout).where(TechniqueLoadout.player_id == player.id))
    session.flush()

    for technique_id in techniques:
        ok, msg = learn_technique(session, player.id, technique_id)
        if not ok:
            print(f"  WARNING learn {technique_id}: {msg}", file=sys.stderr)
        rank = technique_ranks.get(technique_id)
        if rank and rank > 1:
            from src.models import PlayerTechnique

            stmt = select(PlayerTechnique).where(
                PlayerTechnique.player_id == player.id,
                PlayerTechnique.technique_id == technique_id,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is not None:
                row.rank = max(1, int(rank))
                session.add(row)

    for slot, technique_id in loadout.items():
        ok, msg = equip_technique(session, player, technique_id, slot)
        if not ok:
            print(f"  WARNING equip {technique_id} -> {slot}: {msg}", file=sys.stderr)

    if clear_other_inventory:
        session.execute(delete(InventoryItem).where(InventoryItem.player_id == player.id))
        session.flush()
    else:
        for row in get_player_inventory(session, player.id):
            if row.item_id not in inventory:
                row.quantity = 0

    for item_id, qty in inventory.items():
        if get_item_def(item_id) is None:
            raise ValueError(f"unknown item_id in profile: {item_id}")
        if qty <= 0:
            continue
        if clear_other_inventory:
            stack = InventoryItem(player_id=player.id, item_id=item_id, quantity=qty)
            session.add(stack)
        else:
            stmt = select(InventoryItem).where(
                InventoryItem.player_id == player.id,
                InventoryItem.item_id == item_id,
            )
            stack = session.execute(stmt).scalar_one_or_none()
            if stack is None:
                session.add(InventoryItem(player_id=player.id, item_id=item_id, quantity=qty))
            else:
                stack.quantity = qty

    session.flush()
    print("  applied.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="Path to scripts/profiles/*.json")
    parser.add_argument("--discord-id", help="Discord user ID (overrides dao_name match)")
    parser.add_argument("--guild-id", help="Discord server ID when matching is ambiguous")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keep-extra-items",
        action="store_true",
        help="Zero stacks not in the profile instead of wiping the whole bag",
    )
    args = parser.parse_args()

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    session = get_session()
    try:
        player = _find_player(session, profile, args.discord_id, args.guild_id)
        if player is None:
            print("No matching player found.", file=sys.stderr)
            sys.exit(1)
        restore_from_profile(
            session,
            player,
            profile,
            dry_run=args.dry_run,
            clear_other_inventory=not args.keep_extra_items,
        )
        if not args.dry_run:
            session.commit()
            print("Committed.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
