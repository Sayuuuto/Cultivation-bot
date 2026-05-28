"""
Restore rollback-affected players to a target realm/substage and grant random techniques.

Usage (dry-run first):
  set DATABASE_PATH=C:\\path\\to\\cultivation_bot.sqlite3
  py scripts/compensate_rollback.py --discord-ids 111,222 --dry-run
  py scripts/compensate_rollback.py --discord-ids 111,222

List candidates (mortal early with a dao name — likely wiped progress):
  py scripts/compensate_rollback.py --list-candidates

Defaults: Qi Refining late (realm 1, substage 2), qi filled to cap, 10 techniques learnable at that realm.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Allow `py scripts/compensate_rollback.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.combat.catalog import load_technique_catalog
from src.combat.loadout import (
    ACTIVE_SLOTS,
    DEFAULT_STARTER_TECHNIQUES,
    PASSIVE_SLOT,
    equip_technique,
    get_learned_technique_ids,
    learn_technique,
)
from src.db import get_session
from src.models import Player
from src.novice_trial import TRIAL_COMPLETE_STEP
from src.realms import SUBSTAGES, get_realm_name, qi_cap


DEFAULT_REALM = 1
DEFAULT_SUBSTAGE = 2
DEFAULT_TECHNIQUE_COUNT = 10


def _eligible_technique_ids(realm_index: int, *, qi_refining_only: bool) -> list[str]:
    catalog = load_technique_catalog()
    pool: list[str] = []
    for technique_id, tech in catalog.items():
        if technique_id in DEFAULT_STARTER_TECHNIQUES:
            continue
        if qi_refining_only:
            if tech.min_realm != realm_index:
                continue
        elif tech.min_realm > realm_index:
            continue
        pool.append(technique_id)
    return pool


def _pick_techniques(
    session,
    player_id: int,
    realm_index: int,
    count: int,
    rng: random.Random,
    *,
    qi_refining_only: bool,
) -> list[str]:
    learned = get_learned_technique_ids(session, player_id)
    pool = [tid for tid in _eligible_technique_ids(realm_index, qi_refining_only=qi_refining_only) if tid not in learned]
    if len(pool) <= count:
        return pool
    return rng.sample(pool, count)


def _auto_equip(session, player: Player) -> list[str]:
    catalog = load_technique_catalog()
    learned = get_learned_technique_ids(session, player.id)
    notes: list[str] = []
    actives = [
        catalog[tid]
        for tid in sorted(learned)
        if tid in catalog and catalog[tid].slot_type == "active" and tid not in DEFAULT_STARTER_TECHNIQUES
    ]
    passives = [
        catalog[tid]
        for tid in sorted(learned)
        if tid in catalog and catalog[tid].slot_type == "passive"
    ]
    for slot, tech in zip(ACTIVE_SLOTS, actives[: len(ACTIVE_SLOTS)]):
        ok, _ = equip_technique(session, player, tech.technique_id, slot)
        if ok:
            notes.append(f"slot {slot}: {tech.name}")
    if passives:
        ok, _ = equip_technique(session, player, passives[0].technique_id, PASSIVE_SLOT)
        if ok:
            notes.append(f"passive: {passives[0].name}")
    return notes


def compensate_player(
    session,
    player: Player,
    *,
    realm_index: int,
    substage: int,
    technique_count: int,
    rng: random.Random,
    qi_refining_only: bool,
    dry_run: bool,
) -> dict:
    cap = qi_cap(realm_index, substage, player)
    realm_label = f"{get_realm_name(realm_index)} {SUBSTAGES[substage]}"
    picks = _pick_techniques(
        session,
        player.id,
        realm_index,
        technique_count,
        rng,
        qi_refining_only=qi_refining_only,
    )

    summary = {
        "player_id": player.id,
        "discord_id": player.discord_id,
        "dao_name": player.dao_name,
        "before": f"{get_realm_name(player.realm_index)} {SUBSTAGES[player.substage]}, qi={player.qi}",
        "after": f"{realm_label}, qi={cap}",
        "techniques": picks,
    }

    if dry_run:
        return summary

    player.realm_index = realm_index
    player.substage = substage
    player.qi = cap
    player.passive_qi_bank = 0
    player.novice_trial_step = max(int(player.novice_trial_step or 0), TRIAL_COMPLETE_STEP)

    learned_names: list[str] = []
    for technique_id in picks:
        ok, msg = learn_technique(session, player.id, technique_id)
        if ok:
            catalog = load_technique_catalog()
            learned_names.append(catalog[technique_id].name)
        else:
            learned_names.append(f"{technique_id} ({msg})")

    summary["techniques"] = learned_names
    summary["equipped"] = _auto_equip(session, player)
    return summary


def _find_players(session, discord_ids: list[str], guild_id: str | None) -> list[Player]:
    players: list[Player] = []
    for discord_id in discord_ids:
        stmt = select(Player).where(Player.discord_id == discord_id)
        if guild_id:
            stmt = stmt.where(Player.guild_id == guild_id)
        rows = list(session.execute(stmt).scalars().all())
        if not rows:
            print(f"WARNING: no player for discord_id={discord_id}", file=sys.stderr)
            continue
        if len(rows) > 1 and not guild_id:
            print(
                f"WARNING: discord_id={discord_id} matches {len(rows)} guilds — "
                "pass --guild-id or all matches will be compensated",
                file=sys.stderr,
            )
        players.extend(rows)
    return players


def _list_candidates(session) -> None:
    stmt = (
        select(Player)
        .where(Player.realm_index == 0, Player.substage == 0, Player.dao_name != "")
        .order_by(Player.guild_id, Player.discord_id)
    )
    rows = session.execute(stmt).scalars().all()
    if not rows:
        print("No candidates (mortal early with dao_name set).")
        return
    print(f"{'guild_id':<20} {'discord_id':<20} {'dao_name':<24} qi")
    for p in rows:
        print(f"{p.guild_id:<20} {p.discord_id:<20} {p.dao_name:<24} {p.qi}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--discord-ids",
        help="Comma-separated Discord user IDs to compensate",
    )
    parser.add_argument(
        "--guild-id",
        help="Limit lookup to this Discord server ID when a user exists in multiple guilds",
    )
    parser.add_argument("--realm", type=int, default=DEFAULT_REALM, help="Target realm_index (default 1 = Qi Refining)")
    parser.add_argument(
        "--substage",
        type=int,
        default=DEFAULT_SUBSTAGE,
        help="Target substage 0=early 1=mid 2=late (default 2)",
    )
    parser.add_argument(
        "--techniques",
        type=int,
        default=DEFAULT_TECHNIQUE_COUNT,
        help="How many random techniques to grant (default 10)",
    )
    parser.add_argument(
        "--qi-refining-only",
        action="store_true",
        help="Only grant techniques with min_realm equal to --realm (Qi Refining arts only)",
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible technique picks")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")
    parser.add_argument(
        "--list-candidates",
        action="store_true",
        help="Show mortal-early players with a dao name (likely rollback victims)",
    )
    args = parser.parse_args()

    if args.list_candidates:
        session = get_session()
        try:
            _list_candidates(session)
        finally:
            session.close()
        return

    if not args.discord_ids:
        parser.error("Provide --discord-ids or use --list-candidates")

    discord_ids = [s.strip() for s in args.discord_ids.split(",") if s.strip()]
    rng = random.Random(args.seed)

    session = get_session()
    try:
        players = _find_players(session, discord_ids, args.guild_id)
        if not players:
            print("No players matched; nothing to do.")
            return

        for player in players:
            summary = compensate_player(
                session,
                player,
                realm_index=args.realm,
                substage=args.substage,
                technique_count=args.techniques,
                rng=rng,
                qi_refining_only=args.qi_refining_only,
                dry_run=args.dry_run,
            )
            print(
                f"\n{summary['dao_name'] or summary['discord_id']} (id={summary['player_id']}, discord={summary['discord_id']})"
            )
            print(f"  {summary['before']} -> {summary['after']}")
            print(f"  techniques ({len(summary['techniques'])}): {', '.join(summary['techniques'])}")
            if summary.get("equipped"):
                print(f"  equipped: {', '.join(summary['equipped'])}")

        if args.dry_run:
            print("\nDry run — no changes written.")
        else:
            session.commit()
            print(f"\nCommitted compensation for {len(players)} player row(s).")
    finally:
        session.close()


if __name__ == "__main__":
    main()
