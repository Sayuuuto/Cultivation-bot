# PvE, Crafting, And Economy Design

PvE and crafting layer — now part of the core game loop alongside combat and realm progression.

## Goals

- Every cooldown lane has distinct rewards.
- Content and tuning live in JSON config.
- Inventory, crafting, dungeons, equipment, and manuals feed technique progression.
- Command output stays action-oriented and in-world.

## Content Files

| File | Purpose |
|------|---------|
| `config/areas.json` | Adventure zones, realm gates, drops, rare events |
| `config/adventure_encounters.json` | Choice/combat segments, karma choices |
| `config/gather_nodes.json` | Gatherable herbs, ore, rare nodes by area |
| `config/hunt_targets.json` | Hunt areas, beasts, traits, drops, manual routes |
| `config/monsters.json` | Reusable combat foes (adventures, dungeons) |
| `config/dungeons.json` | Dungeon access, encounters, guaranteed drops, bonus rewards |
| `config/cooperative_dungeons.json` | Party dungeon definitions |
| `config/items.json` | Materials, keys, pills, manuals, fragments, equipment, rewards |
| `config/recipes.json` | Pill, key, forge, manual recipes |
| `config/equipment_forge.json` | Forgeable equipment + stat ranges |
| `config/affixes.json` | Equipment affix definitions |
| `config/shop.json` | Spirit stone shop listings |
| `config/drop_rarity.json` | Rarity and drop tuning |

**Rule:** Loaders expose typed helpers — commands should not parse config directly.

## Areas And Adventures

- Realm-gated adventure zones surfaced via `/areas` and `/adventure`
- Stances: cautious, balanced, reckless
- Rare events, choice rewards/penalties, combat encounters
- Paused runs resume via `/adventure-continue`; abandon via `/adventure-abandon`
- Rewards flow through inventory services and drop-source helpers (so `/item` tells players where to seek materials)

## Gathering And Hunting

- `/gather` → material income from configured + rare nodes
- `/hunt` → beast combat → cores, parts, occasional manuals
- Both point players toward `/item` for acquisition hints

## Dungeons

Keyed, cooldown-limited PvE runs with configured steps, boss checks, guaranteed + bonus drops. Cooperative dungeons share the same reward/combat concepts with party state.

Dungeon rewards: high-tier materials, manuals, affix/forge materials, keys, weekly boss rewards. Manual drops run through normalization (duplicate → fragments, high-realm → sealed).

## Inventory And Items

Central store: materials, pills, keys, manuals, fragments, reward items.

- `src/inventory.py` — add, remove, quantity, display
- `/inventory` — grouped by type
- `/item` — effects, crafting uses, where to obtain more
- `src/drop_sources.py` — acquisition hints (command-focused)

## Crafting

| Command | Product |
|---------|---------|
| `/recipes` | Browse available recipes by category |
| `/craft pill` | Brew consumables from materials |
| `/craft key` | Craft dungeon keys |
| `/craft manual` | Bind technique fragments into a manual |
| `/forge` | Forge equipment |

Messages name missing materials, show current quantities, and point to gather/shop/dungeon/inspection commands.

## Pills And Effects

Consumables defined in item/recipe config, applied through `src/consumables.py` + active effect state.

Trade-offs: qi acceleration, breakthrough stability, adventure safety, dungeon offense/defense, rare event targeting. Temporary effects have explicit duration/charges/activity scope.

## Equipment And Affixes

Forgeable slots + affixes modifying combat, adventure, dungeon, cultivation, drop, and breakthrough values.

Key modules: `src/equipment.py`, `src/forge.py`, `src/modifiers.py`, `src/combat_stats.py`, `src/stats.py`.

`/stats` and `/loadout` should explain modifiers clearly enough for a player to decide what to upgrade next.

## Manuals And Technique Progression

Manuals = items + technique unlocks. Connect PvE content to combat builds via:

- Manual pools, shop/sect listings, dungeon/adventure/cultivation rewards, manual crafting from fragments

Central handler: `src/manuals.py` (duplicate handling, sealed manual conversion, unlearned-manual preference).

## Data Model (persisted)

`inventory_items`, `player_effects`, `player_equipment`, `dungeon_runs`, `adventure_runs`, `active_combats`, `player_techniques`, `technique_loadouts`.

Content → JSON config. Player state → SQLAlchemy models (transactions, cooldowns, history).

## Tests

```sh
python -m pytest tests/test_inventory.py -v
python -m pytest tests/test_manual_acquisition.py -v
python -m pytest tests/test_gather_hunt.py -v
python -m pytest tests/test_adventure_events.py -v
python -m pytest tests/test_dungeon_party.py -v
python -m pytest tests/test_item_info.py -v
python -m pytest tests/test_recipes_info.py -v
```

Add tests when changing: recipe inputs, reward normalization, drop-source hints, manual acquisition, dungeon rewards, equipment modifiers.
