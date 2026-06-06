# Stat System Migration Guide

File-by-file change reference for the stat rework.

## Stat Name Mapping

| Old Name | New Name | Location |
|----------|----------|----------|
| `external_strength` | `might` | realm_stats, combat_stats, techniques |
| `internal_strength` | `qi_power` | realm_stats, combat_stats, techniques |
| `defense` | `armor` | realm_stats, combat_stats, combat |
| `agility` | `speed` | realm_stats, combat_stats, combat |
| `spiritual_sense` | `perception` | realm_stats, combat_stats |
| `comprehension` | merged into `perception` | realm_stats, combat_stats |
| `luck` | removed from combat | — |
| (new) | `resolve` | realm_stats, combat_stats |
| (gear) `power` | `might` | equipment_forge, gear_stash |
| (gear) `defense` | `warding` | equipment_forge, gear_stash |
| (gear) `fortune` | `fortune` | equipment_forge, gear_stash |
| (gear) `insight` | `finesse` | equipment_forge, gear_stash |
| (gear) `hp` | `vitality` | equipment_forge, gear_stash |

## Modifier Name Mapping

| Old Name | New Name | Type |
|----------|----------|------|
| `cultivate_qi_mult` | `cultivate_speed` | mult |
| `qi_gathering_mult` | merged into `cultivate_speed` | mult |
| `offline_cap_mult` | `offline_efficiency` | mult |
| `breakthrough_stability` | `breakthrough_luck` | additive |
| `clarity_breakthrough_bonus` | merged into `breakthrough_luck` | additive |
| `breakthrough_setback_mult` | `setback_resistance` | mult |
| `pvp_power` | `damageBonus` | additive |
| `dungeon_damage` | merged into `damageBonus` | additive |
| `adventure_defense` | `damageReduction` | additive |
| `dungeon_defense` | merged into `damageReduction` | additive |
| `adventure_success` | `adventure_luck` | additive |
| `rare_event_mult` | merged into `adventure_luck` | additive |
| `dungeon_luck` | merged into `adventure_luck` | additive |
| `drop_luck` | `dropBonus` | additive |
| `dungeon_risk` | REMOVED | — |
| `pvp_stones_mult` | REMOVED | — |
| `clan_contribution_mult` | REMOVED | — |

## File Changes

### config/realm_stats.json
- Rename stat keys: `external_strength` → `might`, `internal_strength` → `qi_power`, `defense` → `armor`, `agility` → `speed`, `spiritual_sense` → `perception`
- Remove `comprehension` and `luck` from baselines
- Add `resolve` baseline (small values, ~10% of perception)
- Merge comprehension values into perception
- Update `derived` section: new crit/dodge formulas
- Remove `gear_mapping` insight split ratios

### config/techniques.json
- All `scaling_stat: "external_strength"` → `"might"`
- All `scaling_stat: "internal_strength"` → `"qi_power"`
- `scaling_stat: "defense"` → `"armor"`
- `scaling_stat: "agility"` → `"speed"`
- `scaling_stat: "spiritual_sense"` → `"perception"`

### config/combat_rules.json
- No formula changes needed (status definitions stay the same)

### config/affixes.json
- `"power"` → `"damageBonus"`
- `"defense"` → `"damageReduction"`
- `"drop_luck"` → `"dropBonus"`
- `"cultivate_qi_mult"` → `"cultivate_speed"`
- `"breakthrough_stability"` → `"breakthrough_luck"`

### config/spirit_roots.json
- `adventure_success` → `adventure_luck`
- `adventure_defense` → `damageReduction`
- `dungeon_damage` → `damageBonus`
- `pvp_power` → `damageBonus`
- `breakthrough_stability` → `breakthrough_luck`
- `drop_luck` → `dropBonus`
- `cultivate_qi_mult` → `cultivate_speed`
- `rare_event_mult` → `adventure_luck`
- `offline_cap_mult` → `offline_efficiency`
- Remove `clan_contribution_mult`, `pvp_stones_mult`, `dungeon_risk`

### config/origins.json
- Same modifier key remapping as spirit_roots.json

### config/pill_effects.json
- `qi_gathering_mult` → `cultivate_speed`
- `adventure_defense` → `damageReduction`
- `adventure_success` → `adventure_luck`
- `dungeon_damage` → `damageBonus`
- `rare_event_mult` → `adventure_luck`
- `breakthrough_stability` → `breakthrough_luck`

### config/foundation.json
- Rename stat keys in `body_stats`, `body_caps_base`, `body_cap_per_realm`, `body_stack_rates`, `body_input_tiers`
- Rename stat keys in `meridian_stats`, `meridian_caps_base`, `meridian_cap_per_realm`, `meridian_stack_rates`, `meridian_input_tiers`
- `external_strength` → `might`, `internal_strength` → `qi_power`, `defense` → `armor`, `agility` → `speed`, `spiritual_sense` → `perception`, `comprehension` → `perception`

### config/equipment_forge.json
- `stat_power` → `stat_might`
- `stat_defense` → `stat_warding`
- `stat_fortune` → `stat_fortune` (unchanged)
- `stat_insight` → `stat_finesse`
- `stat_hp` → `stat_vitality`

### config/drop_rarity.json
- No changes needed (uses `luck` internally, will be remapped in code)

### src/modifiers.py
- Replace 17 fields with 8 fields (see STAT_REWORK_PLAN.md)

### src/combat_stats.py
- `STAT_KEYS` → new 7 keys
- `realm_baseline_stats()` → use new stat keys
- `_apply_gear()` → new gear stat mapping
- `compute_combat_stats()` → new computation
- Remove `gather_quantity_bonus`, `gather_rare_bonus` (move to loot/fortune system)
- Update `format_combat_stats_block()` display

### src/stats.py
- `EquipmentStats` → 4 fields (might, warding, vitality, finesse)
- `equipment_stats_to_modifiers()` → new mapping
- `format_stats_summary()` → new display
- `format_gear_summary()` → new display
- `_gear_stat_display_bits()` → new stat names

### src/combat/triggers.py
- `_compute_base_damage()` → unified formula
- Remove type-specific `0.15` bonus
- Remove `_gear_tag_damage_bonus()` (redundant)
- `_crit_chance()` → single-stat from perception
- `_resolve_effect()` → use new stat names
- `compute_dot_potency()` → use new stat names

### src/combat/engine.py
- `_opponent_damage()` → same formula as player (40% absorption)
- Remove `adventure_defense` from opponent damage calc

### src/combat/catalog.py
- `TechniqueDef.scaling_stat` → accepts new stat names
- No structural changes needed

### src/combat/loadout.py
- `_technique_has_role()` → no changes needed
- `validate_pvp_loadout()` → no changes needed

### src/character.py
- `get_character_modifiers()` → use new modifier field names
- `compute_adventure_power()` → use new modifier names
- `compute_adventure_defense()` → use new modifier names

### src/auto_combat.py
- `_player_auto_attack()` → unified formula with 40% armor
- `_beast_auto_attack()` → same formula

### src/effects.py
- `apply_effects_from_db()` → new modifier key names

### src/equipment.py
- `AFFIX_FIELD_MAP` → new names
- `get_player_affix_modifiers()` → new field names

### src/game.py
- Cultivation qi formula → use `cultivate_speed` modifier
- Breakthrough formula → use `breakthrough_luck` modifier
- Passive qi bank → use `offline_efficiency` modifier
- PvP strength → use new stat names

### src/adventure.py
- Adventure success → use `adventure_luck` modifier
- Adventure defense → use `damageReduction` modifier

### src/loot.py
- Drop chance → use `dropBonus` modifier (replaces `drop_luck`)
- Fortune stat → from gear

### src/explore.py
- Stat check → use new combat stats
- Affinity → no changes needed

### src/dungeon.py
- Segment success → use `damageBonus` and `damageReduction`

### src/pvp_combat.py
- PvP initiative → use `speed` stat
- PvP strength → use new combat stats

### src/schemas.py
- `Category` literal → update stat references
- `ItemCategory` → no changes
- Add new stat type validation

### src/player_dashboard.py
- Display updates for new stat names

### src/roots_info.py
- Display updates for new modifier names

### src/technique_info.py
- Display updates for scaling_stat names
