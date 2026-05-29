# PvE, Crafting, And Economy Design

This document captures the current PvE and crafting layer that grew out of the
first expansion design. It is now part of the core game loop alongside combat
and realm progression.

## Goals

- Give every cooldown lane distinct rewards.
- Keep content and tuning in JSON config.
- Make inventory, crafting, dungeons, equipment, and manuals feed technique
  progression.
- Keep command output action-oriented and in-world.

## Content Files

- `config/areas.json`: adventure zones, realm gates, drops, and rare events.
- `config/adventure_encounters.json`: choice and combat segments, including karma choices.
- `config/gather_nodes.json`: gatherable herbs, ore, and rare nodes by area.
- `config/hunt_targets.json`: hunt areas, beasts, traits, drops, and manual routes.
- `config/monsters.json`: reusable combat foes for adventures and dungeons.
- `config/dungeons.json`: dungeon access, encounters, guaranteed drops, and bonus rewards.
- `config/cooperative_dungeons.json`: party dungeon definitions.
- `config/items.json`: materials, keys, pills, manuals, fragments, equipment helpers, and reward items.
- `config/recipes.json`: pill, key, forge, and manual recipes.
- `config/equipment_forge.json`: forgeable equipment and stat ranges.
- `config/affixes.json`: equipment affix definitions.
- `config/shop.json`: spirit stone shop listings.
- `config/drop_rarity.json`: rarity and drop tuning.

Loaders and services should expose typed helpers to command code rather than
letting commands parse config directly.

## Areas And Adventures

Adventure zones are defined in config and surfaced through `/areas` and
`/adventure`. Each run uses a stance, resolves configured segments, can grant
materials or manuals, and can trigger moral choices that shift karma.

Supported adventure concepts:

- realm-gated areas
- cautious, balanced, and reckless stance tuning
- rare events
- choice rewards and penalties
- combat encounters using monsters from config
- paused runs that resume through `/adventure-continue`
- abandoned runs through `/adventure-abandon`

Adventure rewards should flow through inventory services and drop-source helpers
so `/item` can tell players where to seek materials.

## Gathering And Hunting

`/gather` provides material income from configured nodes and rare nodes.
`/hunt` launches beast combat and grants cores, parts, and occasional manuals.

Keep gathering and hunting reward pools distinct:

- gathering should be strongest for herbs, ore, ink, and crafting materials
- hunting should be strongest for beast parts, cores, combat drops, and some manuals
- both should point players toward `/item` for acquisition hints

## Dungeons

Dungeons are keyed, cooldown-limited PvE runs with configured steps, boss checks,
guaranteed rewards, and bonus drops. Cooperative dungeons build on the same
reward and combat concepts with party state.

Dungeon rewards can include:

- high-tier materials
- manual drops
- affix or forge materials
- keys and progression items
- weekly boss rewards

When a dungeon drops a manual, run it through manual normalization so duplicate
known manuals become fragments and high-realm manuals can become sealed.

## Inventory And Items

Inventory is the central store for materials, pills, keys, manuals, fragments,
and reward items. `src/inventory.py` owns add, remove, quantity, and display
helpers.

`/inventory` groups items by type. `/item` displays effects, crafting uses, and
where to obtain more. Drop source hints come from `src/drop_sources.py` and
should stay command-focused.

## Crafting

Crafting uses config-defined recipes and inventory checks.

Player commands:

- `/recipes`: browse available recipes by category.
- `/craft pill`: brew consumables from materials.
- `/craft key`: craft dungeon keys.
- `/craft manual`: bind technique fragments into a manual.
- `/forge`: forge equipment.

Crafting messages should name missing materials, show current quantities, and
point to gather, shop, dungeon, or item inspection commands as appropriate.

## Pills And Effects

Consumables are defined in item and recipe config and applied through
`src/consumables.py` and active effect state. Pill design should keep a clear
trade-off:

- qi acceleration
- breakthrough stability
- adventure safety
- dungeon offense or defense
- rare event targeting

Temporary effects should have explicit duration, charges, or activity scope, and
should be visible through profile or loadout displays when relevant.

## Equipment And Affixes

Equipment supports forgeable slots and affixes that modify combat, adventure,
dungeon, cultivation, drop, and breakthrough values.

Important modules:

- `src/equipment.py`
- `src/forge.py`
- `src/modifiers.py`
- `src/combat_stats.py`
- `src/stats.py`

`/stats` and `/loadout` should explain derived modifiers clearly enough for a
player to decide what to upgrade next.

## Manuals And Technique Progression

Manuals are both items and technique unlocks. They connect PvE content to combat
builds through:

- manual pools
- shop listings
- sect shops
- dungeon and adventure rewards
- cultivation and breakthrough rewards
- manual crafting from fragments

Manual handling belongs in `src/manuals.py`. Keep duplicate handling, sealed
manual conversion, and unlearned-manual preference centralized there.

## Data Model

Relevant persisted state includes:

- `inventory_items`
- `player_effects`
- `player_equipment`
- `dungeon_runs`
- `adventure_runs`
- `active_combats`
- `player_techniques`
- `technique_loadouts`

Use JSON config for content and SQLAlchemy models for player state,
transactions, cooldowns, and run history.

## Tests

Focused checks:

```powershell
py -m pytest tests/test_inventory.py -v
py -m pytest tests/test_manual_acquisition.py -v
py -m pytest tests/test_gather_hunt.py -v
py -m pytest tests/test_adventure_events.py -v
py -m pytest tests/test_dungeon_party.py -v
py -m pytest tests/test_item_info.py -v
py -m pytest tests/test_recipes_info.py -v
```

Add tests when changing recipe inputs, reward normalization, drop-source hints,
manual acquisition, dungeon rewards, or equipment modifiers.
