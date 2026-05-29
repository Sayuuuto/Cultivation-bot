# Cultivation Discord Bot Design

## Product Goal

Build a serious xianxia Discord game with casual daily pacing, long-term realm
growth, expressive martial builds, cooperative and competitive combat, and
config-driven content that can be tuned without rewriting command code.

The player fantasy is simple: begin as a mortal cultivator, gather resources,
study manuals, make moral choices, join or leave social orders, and climb toward
immortality.

## Player Experience

Core player loops:

- Begin a character with `/start`, then follow `/profile` and guidance hints.
- Cultivate qi through `/daily`, `/cultivate`, and `/breakthrough`.
- Gather materials with `/gather`, hunt beasts with `/hunt`, and explore story
  encounters with `/adventure`.
- Study martial manuals through `/learn`, shape a build with `/techniques` and
  `/equip-technique`, and inspect art details with `/technique`.
- Craft pills, dungeon keys, equipment, and manuals through `/recipes`, `/craft`,
  `/forge`, and `/equip`.
- Challenge other cultivators with `/duel`.
- Create player clans through `/clan-*` commands.
- Join fixed martial sects through `/sect-list`, `/sect-join`, `/sect-task`,
  `/sect-shop`, and `/sect-buy`.

The target session is short daily play with optional deeper sessions for
dungeons, build tuning, and PvP.

## Tone And Copy

- Serious xianxia tone, second-person voice, all-ages safe.
- Use terms such as qi, dantian, realm, sect, manual, meridian, and dao.
- Player-facing strings should say what is true and what to do next.
- Design rationale belongs in Markdown docs or code comments.

The copy guardrail lives in `.cursor/rules/player-facing-copy.mdc` and is tested
by `tests/test_player_facing_copy.py`.

## Progression Model

Players advance through ten named realms, each with `early`, `mid`, and `late`
substages. Realm data is loaded from `config/realms.json` through `src/realms.py`.

Realm config owns:

- display names
- base qi caps
- substage multipliers
- breakthrough odds tuning
- technique load budgets
- technique rank caps

Breakthrough success advances the player to the next substage or realm.
Breakthrough failure applies a setback without permadeath.

## Karma

Karma ranges from `-100` to `+100` and starts neutral. Moral choices in
`/adventure` shift it over time.

Karma influences:

- breakthrough success and setback tuning
- cultivation flavor
- manual pool weights for righteous, neutral, and demonic arts
- profile display and guidance

Combat stats come from realm, techniques, gear, modifiers, and combat rules.

## Combat And Techniques

Combat is turn based and shared across hunts, adventures, dungeons, and duels.
Players use four active technique slots and one passive slot. Technique load
budgets constrain how heavy a build can be for the player's realm.

Technique data is defined in `config/techniques.json` and parsed by
`src/combat/catalog.py`. Effects and passive triggers are represented as data and
interpreted by generic combat code.

See `COMBAT_DESIGN.md` for the combat system overview and
`docs/COMBAT_PROGRESSION.md` for the maintainer contract.

## PvE, Crafting, And Economy

The PvE layer is config-first:

- areas and adventure drops in `config/areas.json`
- gathering nodes in `config/gather_nodes.json`
- hunt targets in `config/hunt_targets.json`
- dungeons in `config/dungeons.json` and `config/cooperative_dungeons.json`
- items in `config/items.json`
- recipes in `config/recipes.json`
- shop listings in `config/shop.json`

Inventory, crafting, pills, equipment affixes, dungeon keys, and manual crafting
are part of the core economy. For details, see `PHASE1_PVE_DESIGN.md`.

## Social Systems

Clans are player-created guilds scoped to a Discord server. Clan commands handle
creation, joining, leaving, invites, contribution, and status.

Sects are fixed in-world orders defined in `config/sects.json`. Sect logic uses
karma, realm, invitation gates, merit, daily tasks, and sect shops. Keep sect IDs
stable because skill idea imports and reward mappings depend on them.

## Persistence

The bot uses SQLite through SQLAlchemy. `create_all()` creates missing tables on
startup. Local and Railway deployments use the same schema, with deployment
database seeding handled by scripts under `scripts/`.

Major persisted concepts include players, inventory items, learned techniques,
technique loadouts, combat sessions, active PvP matches, dungeon runs, effects,
clans, and sect state.

## Configuration Standards

- Prefer JSON config for content, rewards, tuning, and gates.
- Add loader accessors instead of reading JSON directly in command handlers.
- Preserve migration-safe defaults when adding optional fields.
- Keep external draft content out of runtime schemas.
- Add schema or behavior tests when changing required config fields.

## Test Strategy

Run the full suite before broad content or progression changes:

```powershell
py -m pytest tests -v
```

Focused checks:

- `tests/test_slash_commands_integration.py` for command wiring and views.
- `tests/test_player_facing_copy.py` for Discord-facing language.
- `tests/test_combat_engine.py` and `tests/test_combat_triggers_and_karma.py` for combat.
- `tests/test_manual_acquisition.py` for manuals, pools, and sealed drops.
- `tests/test_realms_config.py` for realm config access.

When adding a slash command, update `tests/slash_command_specs.py`.
