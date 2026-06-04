# Design

Product goals, progression model, social systems, config standards, and test strategy for the Cultivation Discord Bot.

## Player Fantasy

Begin as a mortal cultivator, gather resources, study manuals, make moral choices, join or leave social orders, and climb toward immortality through ten named realms.

Target session: short daily play with optional deeper sessions for dungeons, build tuning, and PvP.

## Core Loops

| Loop | Commands |
|------|----------|
| Character | `/start` → `/profile` → guidance hints |
| Cultivation | `/daily` → `/cultivate` → `/breakthrough` |
| Resources | `/gather`, `/hunt` |
| Story | `/adventure`, `/adventure-continue` |
| Builds | `/learn` → `/techniques` → `/equip-technique` |
| Crafting | `/recipes` → `/craft pill` / `/craft key` / `/craft manual` / `/forge` → `/equip` |
| PvP | `/duel` |
| Clans | `/clan-*` |
| Sects | `/sect-list` → `/sect-join` → `/sect-task` → `/sect-shop` → `/sect-buy` |

## Tone And Copy

- Serious xianxia, second-person, all-ages safe.
- Use qi, dantian, realm, sect, manual, meridian, dao.
- Player-facing strings: state what is true and what to do next.
- Design rationale in Markdown docs or code comments, not player strings.
- Tested by `tests/test_player_facing_copy.py`.

Avoid: MVP, scaffold, backlog, future update, or phrases that explain removed flows.

## Progression Model

Players advance through ten named realms, each with `early`/`mid`/`late` substages. Realm data: `config/realms.json`, loaded by `src/realms.py`.

Realm config owns: display names, base qi caps, substage multipliers, breakthrough odds, technique load budgets, technique rank caps.

Breakthrough: success → next substage/realm; failure → setback (no permadeath).

## Karma

Range: `-100` to `+100`, starts neutral. Shifted by moral choices in `/adventure` (`karma_delta` in `config/adventure_encounters.json`).

Karma influences: breakthrough odds, cultivation flavor, manual pool weights (`manual_weight_multiplier()` in `src/karma.py`), profile display.

## Social Systems

**Clans** — player-created, server-scoped. Commands: creation, join, leave, invites, contribution, status.

**Sects** — fixed orders in `config/sects.json`. Use karma, realm, invitation gates, merit, daily tasks, sect shops. **Keep sect IDs stable** (skill idea imports and reward mappings depend on them):

- `blood_lotus`, `imperial_guard`, `kunlun`, `mount_hua`, `shadow_pavilion`, `shaolin`, `tang`, `wudang`

## Persistence

SQLite + SQLAlchemy. `create_all()` on startup. Same schema for local and Railway. Seed via `scripts/`.

Key persisted concepts: players, inventory, learned techniques, technique loadouts, combat sessions, PvP matches, dungeon runs, effects, clans, sect state.

## Content Files

All game content is data-first — JSON config files under `config/`. See `PHASE1_PVE_DESIGN.md` for the full file reference.

| File | Purpose |
|------|---------|
| `config/techniques.json` | Technique definitions, effects, passives |
| `config/realms.json` | Realm names, qi caps, load budgets, rank caps |
| `config/realm_stats.json` | Combat stat scaling per realm |
| `config/items.json` | Inventory items, manuals, materials, pills, rewards |
| `config/areas.json` | Adventure zones, realm gates, drops, rare events |
| `config/gather_nodes.json` | Gatherable resources by area |
| `config/hunt_targets.json` | Hunt targets, beast traits, drops |
| `config/dungeons.json` | Dungeon encounters, rewards |
| `config/cooperative_dungeons.json` | Party dungeon definitions |
| `config/recipes.json` | Crafting recipes |
| `config/equipment_forge.json` | Forgeable equipment |
| `config/affixes.json` | Equipment affixes |
| `config/shop.json` | Spirit stone shop |
| `config/sect_shops.json` | Sect-specific shops |
| `config/sect_tasks.json` | Sect daily tasks |
| `config/sects.json` | Fixed martial sects |
| `config/manual_pools.json` | Weighted manual roll pools |
| `config/combat_rules.json` | Feature flags, status rules, PvP caps |
| `config/adventure_encounters.json` | Choice/combat segments |
| `config/monsters.json` | Reusable combat foes |
| `config/drop_rarity.json` | Rarity tuning |

## Configuration Standards

1. Prefer JSON config for content, rewards, tuning, gates.
2. Add loader accessors — don't read JSON directly in command handlers.
3. Migration-safe defaults when adding optional fields.
4. Keep external draft content out of runtime schemas.
5. Add schema or behavior tests when changing required config fields.

Schema validation: `src/schemas.py` with `run_validation()` — cross-file referential integrity checks for all 14 JSON config files. Called in `src/bot.py::main()` before content loading.

## Test Strategy

Default: `python -m pytest tests -v`

| Focus | Command |
|-------|---------|
| Command wiring + views | `python -m pytest tests/test_slash_commands_integration.py -v` |
| Player-facing copy | `python -m pytest tests/test_player_facing_copy.py -v` |
| Combat engine | `python -m pytest tests/test_combat_engine.py -v` |
| Combat + karma triggers | `python -m pytest tests/test_combat_triggers_and_karma.py -v` |
| Manual acquisition | `python -m pytest tests/test_manual_acquisition.py -v` |
| Realm config | `python -m pytest tests/test_realms_config.py -v` |
| Schema validation | `python -m pytest tests/test_asset_schemas.py -v` |
| UI helpers (indirection) | `python -m pytest tests/test_discord_ui_helpers.py -v` |

Add a spec in `tests/slash_command_specs.py` when adding a slash command.
