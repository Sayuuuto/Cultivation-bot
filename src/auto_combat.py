from __future__ import annotations

import random
from dataclasses import dataclass, field

from .combat_stats import PlayerCombatStats
from .modifiers import CharacterModifiers


MAX_AUTO_COMBAT_ROUNDS = 3
PARTIAL_WIN_BEAST_HP_FRACTION = 0.40


@dataclass(frozen=True)
class BeastTemplate:
    beast_id: str
    name: str
    hp: int
    attack: int
    defense: int
    traits: tuple[str, ...] = ()


@dataclass
class AutoCombatResult:
    victory: bool
    beast_name: str
    rounds_fought: int
    player_hp_remaining: int
    player_hp_start: int
    beast_hp_remaining: int
    beast_hp_start: int
    log_lines: list[str] = field(default_factory=list)
    message: str = ""


def _player_attack_damage(
    stats: PlayerCombatStats,
    beast_defense: int,
    rng: random.Random,
    *,
    crit: bool = False,
) -> int:
    base = stats.internal_strength + stats.external_strength
    variance = rng.uniform(0.88, 1.12)
    raw = base * variance
    if crit:
        raw *= 1.5
    mitigation = beast_defense * 0.45
    return max(1, int(raw - mitigation))


def _beast_attack_damage(
    beast_attack: int,
    stats: PlayerCombatStats,
    mod: CharacterModifiers | None,
    rng: random.Random,
) -> int:
    variance = rng.uniform(0.90, 1.10)
    raw = beast_attack * variance
    defense = stats.defense * (1.0 + (mod.adventure_defense if mod else 0.0))
    return max(1, int(raw - defense * 0.35))


def resolve_auto_combat(
    stats: PlayerCombatStats,
    beast: BeastTemplate,
    mod: CharacterModifiers | None = None,
    rng: random.Random | None = None,
) -> AutoCombatResult:
    rng = rng or random.Random()

    player_hp = stats.hp
    beast_hp = beast.hp
    player_start = player_hp
    beast_start = beast_hp
    log: list[str] = []

    rounds = 0
    victory = False

    for round_num in range(1, MAX_AUTO_COMBAT_ROUNDS + 1):
        if player_hp <= 0 or beast_hp <= 0:
            break

        rounds = round_num

        is_crit = rng.random() < stats.crit_chance
        damage = _player_attack_damage(stats, beast.defense, rng, crit=is_crit)
        beast_hp -= damage
        crit_note = " **Critical!**" if is_crit else ""
        log.append(f"**Turn {round_num}** — You strike **{beast.name}** for **{damage}** damage.{crit_note}")

        if beast_hp <= 0:
            victory = True
            log.append(f"**{beast.name}** falls. The hunt is yours.")
            break

        if rng.random() < stats.dodge:
            log.append(f"**{beast.name}** lunges — you slip aside unharmed.")
        else:
            taken = _beast_attack_damage(beast.attack, stats, mod, rng)
            player_hp -= taken
            log.append(f"**{beast.name}** hits you for **{taken}** damage. (**{max(0, player_hp)}** HP left)")

            if player_hp <= 0:
                log.append("Your vision darkens — you withdraw before the beast finishes you.")
                break

    if not victory and beast_hp > 0 and player_hp > 0:
        beast_damage_ratio = 1.0 - (beast_hp / beast_start)
        if beast_damage_ratio >= (1.0 - PARTIAL_WIN_BEAST_HP_FRACTION):
            victory = True
            log.append(
                f"You press the advantage — **{beast.name}** flees, wounded beyond recovery."
            )
        else:
            log.append(f"**{beast.name}** escapes into the wild. You failed to bring it down.")

    if victory:
        message = f"You defeated **{beast.name}** after {rounds} turn(s)."
    else:
        message = f"**{beast.name}** got away. Train your strength and try again."

    return AutoCombatResult(
        victory=victory,
        beast_name=beast.name,
        rounds_fought=rounds,
        player_hp_remaining=max(0, player_hp),
        player_hp_start=player_start,
        beast_hp_remaining=max(0, beast_hp),
        beast_hp_start=beast_start,
        log_lines=log,
        message=message,
    )
