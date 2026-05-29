from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.combat.catalog import TechniqueDef, load_technique_catalog
from src.combat.loadout import ACTIVE_SLOTS, PASSIVE_SLOT
from src.combat.rarity import rarity_rank
from src.combat_stats import PlayerCombatStats, _load_realm_stats, realm_baseline_stats
from src.auto_combat import resolve_auto_combat
from src.content import get_area, get_areas
from src.hunt import get_hunt_area, scale_hunt_beast_for_area
from src.realms import get_technique_load_budget


@dataclass(frozen=True)
class BuildReport:
    realm_index: int
    build_id: str
    techniques: list[str]
    load: int
    score: float


def _technique_score(tech: TechniqueDef) -> float:
    score = tech.base_damage * (1.0 + rarity_rank(tech.rarity) * 0.12)
    score += tech.scaling_ratio * 20
    score += max(0.0, tech.status_chance) * 10
    score += 6 if tech.slot_type == "passive" else 0
    if any(effect.type in {"heal", "lifesteal", "shield", "cleanse"} for effect in tech.effects):
        score += 8
    if tech.status_id in {"stun", "fear", "seal"}:
        score += 10
    return round(score, 2)


def enumerate_legal_builds(realm_index: int, *, limit: int = 50) -> list[BuildReport]:
    budget = get_technique_load_budget(realm_index)
    catalog = [
        tech
        for tech in load_technique_catalog().values()
        if tech.min_realm <= realm_index and tech.technique_id != "basic_strike"
    ]
    actives = [tech for tech in catalog if tech.slot_type == "active"]
    passives = [tech for tech in catalog if tech.slot_type == "passive"]
    reports: list[BuildReport] = []
    for passive in [None, *passives]:
        passive_load = passive.load if passive else 0
        if passive_load > budget["passive"]:
            continue
        for active in actives:
            total_load = active.load + passive_load
            if active.load > budget["active"] or total_load > budget["total"]:
                continue
            techniques = ["basic_strike", active.technique_id]
            if passive:
                techniques.append(passive.technique_id)
            score = _technique_score(active) + (_technique_score(passive) if passive else 0)
            reports.append(
                BuildReport(
                    realm_index=realm_index,
                    build_id="+".join(techniques),
                    techniques=techniques,
                    load=total_load,
                    score=round(score, 2),
                )
            )
    reports.sort(key=lambda row: row.score, reverse=True)
    return reports[:limit]


def simulate_matchups(builds: list[BuildReport], *, rounds: int, seed: int) -> dict:
    rng = random.Random(seed)
    results: dict[str, dict[str, float]] = {
        build.build_id: {"wins": 0, "games": 0, "avg_score_delta": 0.0} for build in builds
    }
    for _ in range(rounds):
        a, b = rng.sample(builds, 2)
        noise_a = rng.uniform(0.85, 1.15)
        noise_b = rng.uniform(0.85, 1.15)
        score_a = a.score * noise_a
        score_b = b.score * noise_b
        winner = a if score_a >= score_b else b
        delta = abs(score_a - score_b)
        for build in (a, b):
            bucket = results[build.build_id]
            bucket["games"] += 1
            bucket["avg_score_delta"] += delta
        results[winner.build_id]["wins"] += 1
    for bucket in results.values():
        games = max(1, int(bucket["games"]))
        bucket["win_rate"] = round(bucket["wins"] / games, 3)
        bucket["avg_score_delta"] = round(bucket["avg_score_delta"] / games, 2)
    return results


def fragment_economy_telemetry() -> dict:
    from src.combat.ranks import category_material
    from src.manuals import FRAGMENT_ITEM_ID, MANUAL_CRAFT_INPUTS

    return {
        "fragment_item_id": FRAGMENT_ITEM_ID,
        "sinks": {
            "craft_manual": dict(MANUAL_CRAFT_INPUTS),
            "rank_upgrade": {
                "config": "config/technique_upgrade.json",
                "category_materials": {cat: category_material(cat) for cat in ("sword", "body", "fire", "soul", "poison", "utility", "passive")},
            },
            "duplicate_manual": "2x technique_fragment per duplicate",
        },
        "earn_notes": [
            "cultivate_enlightenment ~4%",
            "breakthrough_success ~15%",
            "hunt/adventure/dungeon area tables",
            "duplicate manual conversion",
        ],
    }


def _stats_for_realm(realm_index: int) -> PlayerCombatStats:
    cfg = _load_realm_stats()
    base = realm_baseline_stats(realm_index, 1, cfg)
    derived = cfg["derived"]
    crit = (
        base["spiritual_sense"] * derived["crit_per_spiritual_sense"]
        + base["luck"] * derived["crit_per_luck"]
    )
    dodge = base["agility"] * derived["dodge_per_agility"]
    return PlayerCombatStats(
        hp=base["hp"],
        max_hp=base["hp"],
        internal_strength=base["internal_strength"],
        external_strength=base["external_strength"],
        agility=base["agility"],
        spiritual_sense=base["spiritual_sense"],
        defense=base["defense"],
        comprehension=base["comprehension"],
        luck=base["luck"],
        crit_chance=max(0.0, min(0.45, crit)),
        dodge=max(0.0, min(0.40, dodge)),
    )


def simulate_hunt_realm_matrix(*, rounds: int, seed: int) -> dict:
    rng = random.Random(seed)
    matrix: dict[str, dict[str, float | int | str]] = {}
    for area_id, area in get_areas().items():
        hunt_area = get_hunt_area(area_id)
        if hunt_area is None or not hunt_area.beasts:
            continue
        beast = next((b for b in hunt_area.beasts if b.combat_tier == "normal"), hunt_area.beasts[0])
        area_def = get_area(area_id)
        if area_def is None:
            continue
        scaled = scale_hunt_beast_for_area(beast, area_def)
        for realm_index in range(0, 5):
            stats = _stats_for_realm(realm_index)
            wins = 0
            total_rounds = 0
            for _ in range(rounds):
                result = resolve_auto_combat(stats, scaled, rng=rng)
                wins += 1 if result.victory else 0
                total_rounds += result.rounds_fought
            key = f"realm_{realm_index}_vs_{area_id}"
            matrix[key] = {
                "player_realm": realm_index,
                "area_realm": area.min_realm,
                "area": area.name,
                "beast": scaled.name,
                "beast_hp": scaled.hp,
                "beast_attack": scaled.attack,
                "win_rate": round(wins / max(1, rounds), 3),
                "avg_rounds": round(total_rounds / max(1, rounds), 2),
            }
    return matrix


def build_report(realm_index: int, *, rounds: int, seed: int, limit: int, include_economy: bool = False) -> dict:
    builds = enumerate_legal_builds(realm_index, limit=limit)
    report = {
        "realm_index": realm_index,
        "slots": {"active": list(ACTIVE_SLOTS), "passive": PASSIVE_SLOT},
        "builds": [asdict(build) for build in builds],
        "matchups": simulate_matchups(builds, rounds=rounds, seed=seed) if len(builds) >= 2 else {},
        "hunt_realm_matrix": simulate_hunt_realm_matrix(rounds=max(20, rounds // 10), seed=seed),
    }
    if include_economy:
        report["fragment_economy"] = fragment_economy_telemetry()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Enumerate legal builds and simulate rough PvP matchup outliers.")
    parser.add_argument("--realm", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=500)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=Path, default=Path("reports") / "balance_report.json")
    parser.add_argument("--economy", action="store_true", help="Include fragment earn/spend telemetry")
    args = parser.parse_args()
    report = build_report(
        args.realm,
        rounds=args.rounds,
        seed=args.seed,
        limit=args.limit,
        include_economy=args.economy,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(report['builds'])} builds to {args.output}")


if __name__ == "__main__":
    main()
