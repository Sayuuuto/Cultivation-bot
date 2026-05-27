from __future__ import annotations

import random

from ..combat_stats import PlayerCombatStats
from .catalog import TechniqueDef, get_technique
from .rarity import rarity_damage_multiplier
from .effect_defs import EffectDef
from .effects import (
    CombatantState,
    apply_status,
    cleanse_debuffs,
    has_status,
    is_stunned,
)

CC_STATUSES = frozenset({"stun", "seal", "fear"})


def _stat_value(stats: PlayerCombatStats, stat_key: str) -> int:
    return int(getattr(stats, stat_key, 0))


def _target_has_status(target: CombatantState, status_id: str | None) -> bool:
    return status_id is not None and has_status(target, status_id)


def _is_bleed_immune(traits: list[str]) -> bool:
    return "bleed_immune" in traits


def _crit_chance(stats: PlayerCombatStats, passive: TechniqueDef | None) -> float:
    bonus = 0.0
    if passive:
        bonus += passive.passive_crit_bonus
        for trig in passive.passive_triggers:
            if trig.type == "crit_bonus":
                bonus += float(trig.params.get("bonus", 0.0))
    return min(0.55, stats.crit_chance + bonus)


def _consecutive_bonus(state) -> float:
    if state.consecutive_hits <= 0:
        return 0.0
    return state.consecutive_hits * state.consecutive_bonus_per_hit


def _damage_boost_mult(state) -> float:
    if state.damage_boost_turns > 0 and state.damage_boost_pct > 0:
        return 1.0 + state.damage_boost_pct
    return 1.0


def _gear_tag_damage_bonus(stats: PlayerCombatStats, tech: TechniqueDef) -> float:
    counts = stats.technique_tag_counts or {}
    if not counts:
        return 1.0
    category = tech.category.lower()
    if category == "passive":
        return 1.0
    matches = counts.get(category, 0)
    if matches <= 0:
        return 1.0
    return 1.0 + 0.06 * matches


def _compute_base_damage(
    tech: TechniqueDef,
    stats: PlayerCombatStats,
    opponent_defense: int,
    passive: TechniqueDef | None,
    *,
    crit: bool,
    burn_bonus: bool = True,
) -> int:
    if tech.damage_type == "none":
        return 0
    stat_val = _stat_value(stats, tech.scaling_stat)
    raw = tech.base_damage + stat_val * tech.scaling_ratio
    raw *= rarity_damage_multiplier(tech.rarity)
    raw *= _gear_tag_damage_bonus(stats, tech)
    if tech.damage_type == "physical":
        raw += stats.external_strength * 0.15
    elif tech.damage_type == "internal":
        raw += stats.internal_strength * 0.15
    if burn_bonus and passive:
        for trig in passive.passive_triggers:
            if trig.type == "burn_damage_bonus" and tech.status_id == "burn":
                raw *= 1.0 + float(trig.params.get("bonus", 0.0))
            if trig.type == "poison_damage_bonus" and tech.status_id == "poison":
                raw *= 1.0 + float(trig.params.get("bonus", 0.0))
        if passive.passive_burn_bonus and tech.status_id == "burn":
            raw *= 1.0 + passive.passive_burn_bonus
    if crit:
        raw *= 1.5
    mitigation = opponent_defense * 0.45
    return max(1, int(raw - mitigation))


SEAL_EXEMPT_TECHNIQUE_IDS = frozenset({"basic_strike"})


def _apply_shield_damage(target_hp: int, shield: int, damage: int) -> tuple[int, int, int]:
    if shield <= 0:
        return target_hp - damage, 0, damage
    absorbed = min(shield, damage)
    remaining = damage - absorbed
    return target_hp - remaining, shield - absorbed, absorbed


def _deal_damage_to_opponent(state, damage: int) -> int:
    hp, shield, _ = _apply_shield_damage(state.opponent.hp, state.opponent_shield, damage)
    state.opponent.hp = hp
    state.opponent_shield = shield
    return damage


def _deal_damage_to_player(state, damage: int) -> int:
    hp, shield, absorbed = _apply_shield_damage(state.player.hp, state.player_shield, damage)
    state.player.hp = hp
    state.player_shield = shield
    if absorbed > 0:
        state.log.append(f"Your shield absorbs **{absorbed}** damage.")
    return damage - absorbed


def _maybe_apply_status(
    state,
    target: CombatantState,
    status_id: str,
    chance: float,
    rng: random.Random,
    *,
    traits: list[str],
    log_prefix: str = "",
) -> bool:
    if status_id == "bleed" and _is_bleed_immune(traits):
        return False
    if rng.random() >= chance:
        return False
    apply_status(target, status_id)
    name = state.opponent_name if target is state.opponent else "You"
    state.log.append(f"{log_prefix}**{name}** is afflicted with **{status_id}**.")
    return True


def _resolve_on_hit_passives(
    state,
    passive: TechniqueDef | None,
    stats: PlayerCombatStats,
    rng: random.Random,
    *,
    damage_dealt: int,
    is_crit: bool,
) -> None:
    if passive is None or damage_dealt <= 0:
        return
    for trig in passive.passive_triggers:
        if trig.type == "on_hit_bleed_chance":
            chance = float(trig.params.get("chance", 0.0))
            _maybe_apply_status(
                state, state.opponent, "bleed", chance, rng, traits=state.opponent_traits
            )
        elif trig.type == "consecutive_hit_bonus":
            state.consecutive_bonus_per_hit = float(trig.params.get("bonus_per_hit", 0.05))
        elif trig.type == "on_crit_reflect" and is_crit:
            reflect = int(damage_dealt * float(trig.params.get("ratio", 0.3)))
            if reflect > 0:
                _deal_damage_to_opponent(state, reflect)
                state.log.append(f"**{passive.name}** reflects **{reflect}** damage!")
        elif trig.type == "low_hp_crit_bonus":
            threshold = float(trig.params.get("threshold", 0.3))
            if state.opponent.hp / max(1, state.opponent.max_hp) <= threshold:
                pass  # handled in crit roll


def _low_hp_crit_bonus(passive: TechniqueDef | None, opponent: CombatantState) -> float:
    if passive is None:
        return 0.0
    ratio = opponent.hp / max(1, opponent.max_hp)
    bonus = 0.0
    for trig in passive.passive_triggers:
        if trig.type == "low_hp_crit_bonus" and ratio <= float(trig.params.get("threshold", 0.3)):
            bonus += float(trig.params.get("bonus", 0.0))
    return bonus


def _resolve_effect(
    effect: EffectDef,
    state,
    stats: PlayerCombatStats,
    tech: TechniqueDef,
    passive: TechniqueDef | None,
    rng: random.Random,
    *,
    crit: bool,
    damage_mult: float,
) -> int:
    """Returns damage dealt this effect (for lifesteal etc.)."""
    etype = effect.type
    p = effect.params

    if etype == "damage":
        bonus_ratio = 0.0
        req = p.get("requires_status")
        if req and _target_has_status(state.opponent, str(req)):
            bonus_ratio += float(p.get("bonus_ratio", 0.0))
        if p.get("requires_burning") and has_status(state.opponent, "burn"):
            bonus_ratio += float(p.get("bonus_ratio", 0.4))
        if p.get("requires_bleeding") and has_status(state.opponent, "bleed"):
            bonus_ratio += float(p.get("bonus_ratio", 0.25))
        base = _compute_base_damage(tech, stats, state.opponent_defense, passive, crit=crit)
        if bonus_ratio:
            base = int(base * (1.0 + bonus_ratio))
        base = int(base * damage_mult * _damage_boost_mult(state))
        base = int(base * (1.0 + _consecutive_bonus(state)))
        dealt = _deal_damage_to_opponent(state, base)
        return dealt

    if etype == "multi_hit":
        hits = int(p.get("hits", 2))
        total = 0
        hit_ratio = float(p.get("hit_ratio", 0.55))
        for i in range(hits):
            hit_dmg = max(1, int(_compute_base_damage(tech, stats, state.opponent_defense, passive, crit=False) * hit_ratio))
            hit_dmg = int(hit_dmg * damage_mult * _damage_boost_mult(state))
            total += _deal_damage_to_opponent(state, hit_dmg)
            chance = float(p.get("bleed_chance", 0.0))
            if chance > 0:
                _maybe_apply_status(
                    state, state.opponent, "bleed", chance, rng, traits=state.opponent_traits
                )
        return total

    if etype == "apply_status":
        _maybe_apply_status(
            state,
            state.opponent,
            str(p.get("status", tech.status_id or "bleed")),
            float(p.get("chance", tech.status_chance)),
            rng,
            traits=state.opponent_traits,
        )
        return 0

    if etype == "heal":
        ratio = float(p.get("ratio", tech.heal_ratio))
        heal = max(1, int(state.player.max_hp * ratio))
        before = state.player.hp
        state.player.hp = min(state.player.max_hp, state.player.hp + heal)
        gained = state.player.hp - before
        if gained > 0:
            state.log.append(f"**{tech.name}** restores **{gained}** HP.")
        return 0

    if etype == "lifesteal":
        req = p.get("requires_status")
        if req and not _target_has_status(state.opponent, str(req)):
            state.log.append(f"**{tech.name}** finds no opening — the foe is not **{req}**.")
            return 0
        base = _compute_base_damage(tech, stats, state.opponent_defense, passive, crit=crit)
        base = int(base * damage_mult * _damage_boost_mult(state))
        dealt = _deal_damage_to_opponent(state, base)
        if dealt > 0:
            steal = max(1, int(dealt * float(p.get("ratio", 0.25))))
            state.player.hp = min(state.player.max_hp, state.player.hp + steal)
            state.log.append(f"**{tech.name}** drains **{steal}** HP from bleeding prey.")
        return dealt

    if etype == "shield":
        pct = float(p.get("shield_pct", 0.12))
        turns = int(p.get("turns", 2))
        amount = max(1, int(state.player.max_hp * pct))
        state.player_shield = max(state.player_shield, amount)
        state.shield_turns = max(state.shield_turns, turns)
        state.log.append(f"**{tech.name}** raises a shield (**{amount}** absorption).")
        return 0

    if etype == "cleanse":
        count = int(p.get("count", 1))
        removed = cleanse_debuffs(state.player, count)
        if removed:
            state.log.append(f"**{tech.name}** cleanses **{', '.join(removed)}**.")
        heal_ratio = float(p.get("heal_ratio", 0.0))
        if heal_ratio > 0:
            heal = max(1, int(state.player.max_hp * heal_ratio))
            state.player.hp = min(state.player.max_hp, state.player.hp + heal)
            state.log.append(f"**{tech.name}** restores **{heal}** HP.")
        return 0

    if etype == "dodge_next":
        state.player.dodge_next = True
        base = _compute_base_damage(tech, stats, state.opponent_defense, passive, crit=crit)
        if base > 0:
            base = int(base * damage_mult)
            return _deal_damage_to_opponent(state, base)
        return 0

    if etype == "steal_stack_if_status":
        req = str(p.get("requires_status", "poison"))
        if not _target_has_status(state.opponent, req):
            state.log.append(f"**{tech.name}** fails — foe is not **{req}**.")
            return 0
        base = _compute_base_damage(tech, stats, state.opponent_defense, passive, crit=crit)
        base = int(base * (1.0 + float(p.get("bonus_ratio", 0.2))))
        dealt = _deal_damage_to_opponent(state, base)
        for status in list(state.opponent.statuses):
            if status.status_id == req and status.stacks > 0:
                status.stacks = max(0, status.stacks - 1)
                state.log.append(f"**{tech.name}** siphons a stack of **{req}**.")
                break
        return dealt

    return 0


def _legacy_effects_for(tech: TechniqueDef) -> list[EffectDef]:
    effects: list[EffectDef] = []
    if tech.heal_ratio > 0:
        effects.append(EffectDef("on_use", "heal", {"ratio": tech.heal_ratio}))
    elif tech.base_damage > 0 or tech.damage_type != "none":
        if tech.technique_id == "mist_step":
            effects.append(EffectDef("on_use", "dodge_next", {}))
        elif tech.technique_id == "sanguine_drain":
            effects.append(
                EffectDef("on_use", "lifesteal", {"ratio": 0.25, "requires_status": "bleed"})
            )
        elif tech.technique_id == "cinder_lance":
            effects.append(
                EffectDef("on_use", "damage", {"requires_burning": True, "bonus_ratio": 0.4})
            )
        elif tech.technique_id == "iron_cleave":
            effects.append(
                EffectDef("on_use", "damage", {"requires_bleeding": True, "bonus_ratio": 0.25})
            )
        elif tech.technique_id == "rending_flurry":
            effects.append(
                EffectDef("on_use", "multi_hit", {"hits": 2, "hit_ratio": 0.55, "bleed_chance": 0.2})
            )
        elif tech.technique_id == "soul_siphon":
            effects.append(
                EffectDef("on_use", "steal_stack_if_status", {"requires_status": "poison", "bonus_ratio": 0.2})
            )
        elif tech.technique_id == "mountain_guard":
            effects.append(EffectDef("on_use", "shield", {"shield_pct": 0.12, "turns": 2}))
        elif tech.technique_id == "purifying_breath":
            effects.append(EffectDef("on_use", "cleanse", {"count": 1, "heal_ratio": 0.04}))
        else:
            effects.append(EffectDef("on_use", "damage", {}))
        if tech.status_id and tech.status_chance > 0:
            effects.append(
                EffectDef(
                    "on_use",
                    "apply_status",
                    {"status": tech.status_id, "chance": tech.status_chance},
                )
            )
    return effects


def get_technique_effects(tech: TechniqueDef) -> list[EffectDef]:
    if tech.effects:
        return tech.effects
    return _legacy_effects_for(tech)


def resolve_technique(
    state,
    stats: PlayerCombatStats,
    passive: TechniqueDef | None,
    technique_id: str,
    rng: random.Random,
) -> str | None:
    tech = get_technique(technique_id)
    if tech is None:
        return "Unknown technique."
    if state.player.sealed and technique_id not in SEAL_EXEMPT_TECHNIQUE_IDS:
        return "Your meridians are **sealed** — only **Basic Strike** still responds."
    cd = state.technique_cooldowns.get(technique_id, 0)
    if cd > 0:
        return f"**{tech.name}** is on cooldown ({cd} turn(s))."

    crit_chance = _crit_chance(stats, passive) + _low_hp_crit_bonus(passive, state.opponent)
    is_crit = rng.random() < crit_chance
    damage_mult = 0.75 if state.player.feared else 1.0

    total_dealt = 0
    effects = get_technique_effects(tech)
    for effect in effects:
        if effect.trigger != "on_use":
            continue
        total_dealt += _resolve_effect(
            effect, state, stats, tech, passive, rng, crit=is_crit, damage_mult=damage_mult
        )

    if total_dealt > 0 and tech.damage_type != "none":
        crit_note = " **Critical!**" if is_crit else ""
        if not any(e.type in {"lifesteal", "multi_hit"} for e in effects):
            state.log.append(f"**{tech.name}** hits for **{total_dealt}** damage.{crit_note}")
        _resolve_on_hit_passives(state, passive, stats, rng, damage_dealt=total_dealt, is_crit=is_crit)
        state.consecutive_hits += 1
    elif total_dealt == 0 and tech.damage_type != "none" and not any(
        e.type in {"heal", "shield", "cleanse"} for e in effects
    ):
        state.consecutive_hits = 0

    if tech.cooldown > 0:
        state.technique_cooldowns[technique_id] = tech.cooldown
    return None


def process_passive_turn_end(state, passive: TechniqueDef | None) -> None:
    if passive is None:
        return
    for trig in passive.passive_triggers:
        if trig.type == "heal_if_foe_status":
            status_id = str(trig.params.get("status", "bleed"))
            if has_status(state.opponent, status_id):
                heal_pct = float(trig.params.get("heal_pct", 0.05))
                heal = max(1, int(state.player.max_hp * heal_pct))
                before = state.player.hp
                state.player.hp = min(state.player.max_hp, state.player.hp + heal)
                gained = state.player.hp - before
                if gained > 0:
                    state.log.append(f"**{passive.name}** restores **{gained}** HP from bleeding prey.")
        elif trig.type == "consecutive_hit_bonus" and state.consecutive_hits == 0:
            state.consecutive_bonus_per_hit = float(trig.params.get("bonus_per_hit", 0.05))
    if passive.passive_on_bleed and has_status(state.opponent, "bleed"):
        heal_pct = float(passive.passive_on_bleed.get("heal_pct", 0.0))
        if heal_pct > 0:
            heal = max(1, int(state.player.max_hp * heal_pct))
            before = state.player.hp
            state.player.hp = min(state.player.max_hp, state.player.hp + heal)
            gained = state.player.hp - before
            if gained > 0:
                state.log.append(f"**{passive.name}** restores **{gained}** HP from bleeding prey.")


def process_passive_on_cc(state, passive: TechniqueDef | None, status_id: str) -> None:
    if passive is None or status_id not in CC_STATUSES:
        return
    for trig in passive.passive_triggers:
        if trig.type != "cleanse_stun_shield":
            continue
        cd_key = f"{passive.technique_id}:{trig.type}"
        if state.passive_cooldowns.get(cd_key, 0) > 0:
            continue
        if status_id == "stun":
            cleanse_debuffs(state.player, 99, only={"stun"})
            state.log.append(f"**{passive.name}** shatters the stun!")
        shield_pct = float(trig.params.get("shield_pct", 0.15))
        amount = max(1, int(state.player.max_hp * shield_pct))
        state.player_shield = max(state.player_shield, amount)
        state.shield_turns = max(state.shield_turns, 2)
        state.log.append(f"**{passive.name}** raises an emergency shield (**{amount}**).")
        state.passive_cooldowns[cd_key] = int(trig.params.get("cooldown", 5))


def process_passive_hp_threshold(state, passive: TechniqueDef | None) -> None:
    if passive is None:
        return
    ratio = state.player.hp / max(1, state.player.max_hp)
    for trig in passive.passive_triggers:
        if trig.type != "heal_below_threshold":
            continue
        threshold = float(trig.params.get("threshold", 0.3))
        if ratio > threshold:
            continue
        cd_key = f"{passive.technique_id}:{trig.type}"
        if cd_key in state.triggered_once:
            continue
        if state.passive_cooldowns.get(cd_key, 0) > 0:
            continue
        heal_pct = float(trig.params.get("heal_pct", 0.3))
        heal = max(1, int(state.player.max_hp * heal_pct))
        state.player.hp = min(state.player.max_hp, state.player.hp + heal)
        state.log.append(f"**{passive.name}** blooms — you recover **{heal}** HP!")
        state.triggered_once.add(cd_key)
        state.passive_cooldowns[cd_key] = int(trig.params.get("cooldown", 10))


def check_fatal_survival(state, passive: TechniqueDef | None) -> bool:
    if state.player.hp > 0:
        return False
    if passive is None:
        return False
    for trig in passive.passive_triggers:
        if trig.type != "fatal_survive":
            continue
        cd_key = f"{passive.technique_id}:fatal"
        if cd_key in state.triggered_once:
            continue
        state.player.hp = 1
        state.damage_boost_pct = float(trig.params.get("damage_boost", 0.4))
        state.damage_boost_turns = int(trig.params.get("boost_turns", 2))
        state.triggered_once.add(cd_key)
        state.log.append(
            f"**{passive.name}** denies death! You cling to **1 HP** with surging power!"
        )
        return True
    return False


def tick_combat_extras(state) -> None:
    if state.shield_turns > 0:
        state.shield_turns -= 1
        if state.shield_turns <= 0:
            state.player_shield = 0
    if state.damage_boost_turns > 0:
        state.damage_boost_turns -= 1
        if state.damage_boost_turns <= 0:
            state.damage_boost_pct = 0.0
    state.passive_cooldowns = {k: v - 1 for k, v in state.passive_cooldowns.items() if v - 1 > 0}


def opponent_trait_turn(state, rng: random.Random) -> None:
    if "cleanse_every_3_turns" in state.opponent_traits:
        cd = state.opponent_trait_cd.get("cleanse", 0)
        if cd <= 0:
            removed = cleanse_debuffs(state.opponent, 99)
            if removed:
                state.log.append(f"**{state.opponent_name}** cleanses **{', '.join(removed)}**!")
            state.opponent_trait_cd["cleanse"] = 3
        else:
            state.opponent_trait_cd["cleanse"] = cd - 1

    if "seal_on_hit" in state.opponent_traits and rng.random() < 0.22:
        if not state.player.sealed:
            apply_status(state.player, "seal")
            state.log.append(f"**{state.opponent_name}** seals your meridians!")

    if "high_stun_chance" in state.opponent_traits and rng.random() < 0.18:
        apply_status(state.player, "stun")
        state.log.append(f"**{state.opponent_name}** stuns you with a brutal blow!")


def reset_consecutive_if_no_damage(state, damage_dealt: int) -> None:
    if damage_dealt <= 0:
        state.consecutive_hits = 0
