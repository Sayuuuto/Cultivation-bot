# Combat And Progression Maintainer Guide

This guide documents the combat/progression contract used by the current code
and the next safe extension points. Keep player-facing wording in command
strings, embeds, tutorials, and autocomplete labels in-world. Use this document
for design rationale, validation notes, and rollout details.

## Core Principles

- Prefer JSON data plus generic parsers before adding Python branches.
- Keep existing combat turn flow intact: cooldowns, button combat, turn order,
  statuses, and PvP channels remain the runtime shape.
- Gate broad behavior through `config/combat_rules.json` so systems can be
  switched independently while content is tuned.
- Treat draft skill imports as idea review material. The runtime schema is
  `config/techniques.json`, not external draft files.
- Map sect-linked rewards to existing sect IDs in `config/sects.json`.

## Runtime Files

- `config/techniques.json`: active and passive technique definitions.
- `src/combat/catalog.py`: parses technique JSON into `TechniqueDef`.
- `src/combat/effect_defs.py`: small dataclasses for active effects and passive triggers.
- `src/combat/triggers.py`: resolves passive trigger events during combat.
- `src/combat/effects.py`: status state, status ticks, cleanse behavior, and combatant state.
- `src/combat/loadout.py`: learned techniques, equipped slots, load budgets, PvP legality checks, and rank lookup.
- `config/combat_rules.json`: feature flags, status rules, PvP caps, and combat constants.
- `src/combat/rules.py`: typed loader for combat rules and status metadata.
- `config/realms.json`: realm names, qi caps, load budgets, and rank caps.
- `src/realms.py`: realm config accessors and compatibility constants.

## Technique Schema

Each technique entry should describe behavior with data:

- Identity: `name`, `category`, `tier`, `rarity`, `alignment`, `role`, and `slot_type`.
- Access: `min_realm`, `manual_item_id`, and source placement in manual/shop/sect pools.
- Combat: `damage_type`, `base_damage`, `scaling_stat`, `scaling_ratio`, `cooldown`, `targeting`, `effects`, and `passive_triggers`.
- Balance: `load`, `tags`, `synergy_hint`, and `rank_effects`.

Active effects use objects with `trigger`, `type`, and type-specific parameters.
Passive triggers use `event`, `type`, and type-specific parameters. Parser code
should accept new generic primitives, then combat resolution should interpret
those primitives consistently across techniques.

When a mechanic appears in multiple skills, add a reusable primitive or shared
resolver path. Avoid hardcoding behavior by technique ID.

## Feature Flags

`config/combat_rules.json` currently exposes these gates:

- `technique_load_budget`
- `pvp_legality_checks`
- `sealed_manuals`
- `technique_ranks`
- `status_diminishing_returns`
- `sect_identity_gates`
- `pvp_telemetry`

Code that depends on a broad progression rule should check the relevant flag
through `load_combat_rules().enabled("flag_name")`. Defaults should keep
existing characters and older config entries playable when new fields are absent.

## Load Budgets And PvP Limits

Load budgets preserve four active slots and one passive slot while limiting how
heavy a build can be for a realm. The budget comes from
`get_technique_load_budget(player.realm_index)` and is enforced by
`validate_loadout_budget()`.

PvP loadout legality is checked before a duel starts. `validate_pvp_loadout()`
currently caps legendary techniques, control tools, shield tools, healing tools,
and survival passives according to `config/combat_rules.json`.

When adding a new technique tag or effect type, update `_technique_has_role()` if
that behavior should count toward a PvP cap.

## Manuals And Acquisition

Manuals connect items to techniques through `manual_item_id`. Acquisition routes
are spread across manual pools, shop listings, sect shops, dungeon rewards,
adventure rewards, cultivation events, and crafting.

Important rules:

- `config/manual_pools.json` controls weighted manual rolls.
- `src/manuals.py` prefers unlearned manuals when possible.
- Karma adjusts manual weights through `manual_weight_multiplier()`.
- Source rarity caps are defined in `src/combat/rarity.py`.
- Duplicate known manuals convert into technique fragments.
- Manuals above the player's realm become sealed when `sealed_manuals` is enabled.
- Sealed manuals can be stored and later opened once the player reaches the required realm.

Player-facing acquisition hints should point to commands the player can use next:
`/item`, `/areas`, `/recipes`, `/dungeon`, `/shop buy`, `/gather`, and related
activity commands.

## Technique Ranks

Learned technique rank is stored on `PlayerTechnique.rank` and defaults to `1`.
Realm caps come from `get_technique_rank_cap()`.

Rank-sensitive behavior should read the rank through `get_technique_rank()` and
apply numeric tuning through `rank_effects` or a generic resolver. Keep technique
identity stable as ranks improve; use rank effects for value scaling, added
trigger nodes, or limited evolution hooks.

## Status Rules

Status metadata belongs in `config/combat_rules.json`. Current status entries can
define damage per stack, duration, max stacks, damage multipliers, spread chance,
turn canceling, control markers, diminishing-return windows, and tags such as
`dot`, `control`, `cleanseable`, and `anti_heal`.

Status behavior is interpreted by `src/combat/effects.py` and combat resolution
code. When adding a status:

1. Add rule metadata in `config/combat_rules.json`.
2. Add generic effect or trigger handling if the status needs new behavior.
3. Add tests for application, ticking, cleansing, and PvP interactions.

## Sect Identity

Sects are fixed game orders defined in `config/sects.json`. Sect shops and tasks
are defined in `config/sect_shops.json` and `config/sect_tasks.json`.

Use existing sect IDs for new reward mapping:

- `blood_lotus`
- `imperial_guard`
- `kunlun`
- `mount_hua`
- `shadow_pavilion`
- `shaolin`
- `tang`
- `wudang`

The skill idea extraction script maps known aliases to these IDs and sends
unknown sect codes to explicit backlog buckets for review.

## Skill Idea Extraction

`scripts/extract_skill_ideas.py` reads draft ideas from
`%USERPROFILE%\Downloads\allskills.json` and writes
`config/skill_idea_mapping.json`.

The output is a review artifact containing normalized IDs, roles, categories,
manual IDs, source taxonomy, sect remaps, and backlog reasons. Use it to decide
which ideas should become entries in `config/techniques.json`, `manual_pools`,
shops, or sect rewards.

Do not import the draft schema directly into runtime code.

## Validation And Tests

Recommended checks after combat/progression changes:

```powershell
py -m pytest tests/test_combat_engine.py -v
py -m pytest tests/test_combat_triggers_and_karma.py -v
py -m pytest tests/test_pvp_combat.py -v
py -m pytest tests/test_manual_acquisition.py -v
py -m pytest tests/test_realms_config.py -v
py -m pytest tests/test_player_facing_copy.py -v
```

Add or update tests when changing:

- effect firing order or passive trigger events
- load budget validation
- PvP legality checks
- sealed manual conversion and unsealing
- rank cap behavior
- status duration, cleansing, control, or damage over time
- sect gates, merit shops, or task rewards

## Documentation Checklist

When changing combat/progression systems:

1. Update JSON config and parser docs together.
2. Keep `README.md` current for setup, commands, and deployment.
3. Update this guide for maintainer-facing design contracts.
4. Keep player-facing copy in-world and action-oriented.
5. Add tests that cover the new rule branch before tuning values broadly.
