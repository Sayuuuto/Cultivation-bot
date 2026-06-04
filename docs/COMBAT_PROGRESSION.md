# Combat And Progression — Maintainer Guide

Combat/progression contract for current code and safe extension points.

**See also:** `COMBAT_DESIGN.md` (system overview), `DESIGN.md` (product goals), `PHASE1_PVE_DESIGN.md` (PvE/crafting).

## Core Principles

1. **JSON data + generic parsers** — prefer data over Python branches.
2. **Preserve combat turn flow** — cooldowns, button combat, turn order, statuses, PvP channels.
3. **Gate broad behavior** — `config/combat_rules.json` feature flags; switch systems independently.
4. **Draft skills are review material** — runtime schema is `config/techniques.json`, not external draft files.
5. **Stable sect IDs** — map sect-linked rewards to existing IDs in `config/sects.json`.

## Runtime Files

| File | Responsibility |
|------|---------------|
| `config/techniques.json` | Active + passive technique definitions |
| `src/combat/catalog.py` | Technique JSON → `TechniqueDef` |
| `src/combat/effect_defs.py` | Effect/trigger dataclasses |
| `src/combat/triggers.py` | Passive trigger event resolution |
| `src/combat/effects.py` | Status state, ticks, cleanse, combatant state |
| `src/combat/loadout.py` | Learned techniques, equip, budgets, PvP legality, rank lookup |
| `config/combat_rules.json` | Feature flags, status rules, PvP caps, constants |
| `src/combat/rules.py` | Typed loader for combat rules + status metadata |
| `config/realms.json` | Realm names, qi caps, load budgets, rank caps |
| `src/realms.py` | Realm config accessors + compatibility constants |

## Technique Schema (`config/techniques.json`)

Each entry uses data, not code branches:

- **Identity:** `name`, `category`, `tier`, `rarity`, `alignment`, `role`, `slot_type`
- **Access:** `min_realm`, `manual_item_id`, pool/shop/sect placement
- **Combat:** `damage_type`, `base_damage`, `scaling_stat`, `scaling_ratio`, `cooldown`, `targeting`, `effects[]`, `passive_triggers[]`
- **Balance:** `load`, `tags`, `synergy_hint`, `rank_effects`

Active effects: `{trigger, type, ...}`. Passive triggers: `{event, type, ...}`.

**Rule:** When a mechanic appears in multiple skills, add a reusable primitive or shared resolver path. Never hardcode behavior by technique ID.

### Legacy passive fields (removed)

`TechniqueDef` no longer carries `passive_crit_bonus`, `passive_burn_bonus`, `passive_on_bleed`. All migrated to `passive_triggers`. See `src/combat/catalog.py` for the normalization layer that converts any remaining legacy-format entries.

## Feature Flags (`config/combat_rules.json`)

`technique_load_budget`, `pvp_legality_checks`, `sealed_manuals`, `technique_ranks`, `status_diminishing_returns`, `sect_identity_gates`, `pvp_telemetry`.

Check via `load_combat_rules().enabled("flag_name")`. Defaults keep existing characters playable when new fields are absent.

## Load Budgets And PvP Limits

- Four active + one passive slot
- Budget: `get_technique_load_budget(player.realm_index)` → `validate_loadout_budget()`
- PvP: `validate_pvp_loadout()` → caps legendary, control, shield, healing, survival_passive (`config/combat_rules.json`)
- Update `_technique_has_role()` when adding a new tag/effect type that should count toward a PvP cap

## Manuals And Acquisition

- `config/manual_pools.json` → weighted rolls
- `src/manuals.py` → prefers unlearned manuals
- `manual_weight_multiplier()` in `src/karma.py` → karma-adjusted weights
- `src/combat/rarity.py` → source rarity caps
- Duplicates → technique fragments
- `sealed_manuals` enabled → high-realm manuals seal on drop, open when player qualifies

Acquisition hints point to next-action commands: `/item`, `/areas`, `/recipes`, `/dungeon`, `/shop buy`, `/gather`.

## Technique Ranks

Stored on `PlayerTechnique.rank` (default `1`). Realm cap: `get_technique_rank_cap()`.

Rank-sensitive code reads via `get_technique_rank()` and scales via `rank_effects` or generic resolver. Keep technique identity stable as ranks improve.

## Status Rules

Belongs in `config/combat_rules.json`. Each status can define: damage per stack, duration, max stacks, damage multipliers, spread chance, turn canceling, control markers, DR window, tags (`dot`, `control`, `cleanseable`, `anti_heal`).

Adding a status: (1) add metadata to `combat_rules.json`, (2) add effect/trigger handling, (3) add tests for application, ticking, cleansing, PvP interactions.

## Sect Identities

Fixed orders in `config/sects.json`. Shops + tasks: `config/sect_shops.json`, `config/sect_tasks.json`.

Stable IDs: `blood_lotus`, `imperial_guard`, `kunlun`, `mount_hua`, `shadow_pavilion`, `shaolin`, `tang`, `wudang`.

## Skill Idea Extraction

`scripts/extract_skill_ideas.py` → reads `allskills.json` → writes `config/skill_idea_mapping.json` (review artifact: normalized IDs, roles, categories, manual IDs, source taxonomy, sect remaps, backlog reasons).

Do not import draft schema into runtime code.

## Validation And Tests

```sh
python -m pytest tests/test_combat_engine.py -v
python -m pytest tests/test_combat_triggers_and_karma.py -v
python -m pytest tests/test_pvp_combat.py -v
python -m pytest tests/test_manual_acquisition.py -v
python -m pytest tests/test_realms_config.py -v
python -m pytest tests/test_player_facing_copy.py -v
python -m pytest tests/test_asset_schemas.py -v
```

Add/update tests when changing: effect firing order, passive trigger events, load budget validation, PvP legality, sealed manual conversion, rank caps, status behavior, sect gates/merit/tasks.

## Documentation Checklist (for combat/progression changes)

1. Update `config/` JSON + parser docs together.
2. Keep `README.md` current for setup, commands, deployment.
3. Update this guide for maintainer contracts.
4. Keep player-facing copy in-world and action-oriented.
5. Add tests before tuning values broadly.
