# Cultivation Discord Bot

Serious xianxia cultivation game for Discord, built in Python with `discord.py`,
SQLAlchemy, SQLite, JSON content files, and pytest coverage for the game logic.

## Quick Start

1. Create and activate a virtual environment.
2. Install runtime dependencies:
   `pip install -r requirements.txt`
3. Copy the environment template:
   `copy .env.example .env`
4. Set `DISCORD_TOKEN` in `.env`.
5. Run the bot:
   `python -m src.bot`
6. Sync slash commands after command changes:
   `py -m src.sync_commands`

Install test dependencies with `py -m pip install -r requirements-dev.txt`.

## Game Loops

The bot is organized around several repeatable activity lanes:

- Cultivation: `/daily`, `/cultivate`, `/breakthrough`
- Resources: `/gather`, `/hunt`
- Story and bosses: `/adventure`, `/adventure-continue`, `/dungeon`
- Builds: `/techniques` (loadout, library, unlock, equip, upgrade), `/craft manual`
- Economy and crafting: `/inventory`, `/item`, `/shop`, `/recipes`, `/craft pill`, `/craft key`, `/forge`
- Competition: `/duel`, `/leaderboard`
- Social progression: `/clan`, `/sect-list`, `/sect`, `/sect-join`, `/sect-task`, `/sect-shop`

Most selection commands use autocomplete filtered to the player's current state:
manuals in the bag, craftable recipes, affordable shop items, unlocked areas,
equipped gear, and available sect rewards.

## Combat And Progression

Combat remains a cooldown and turn based loop. Build identity comes from martial
techniques, manuals, realm gates, karma-weighted rewards, sect membership, and
equipment modifiers.

Techniques live in `config/techniques.json` and are loaded through
`src/combat/catalog.py`. Each technique can define active effects, passive
triggers, load cost, tags, rank effects, source metadata, and a manual item.
The runtime parses these into reusable effect definitions instead of adding a
new Python branch for each art.

Combat tuning and rollout gates live in `config/combat_rules.json` and
`src/combat/rules.py`. Realm load budgets and technique rank caps live in
`config/realms.json` and `src/realms.py`.

For the full maintainer contract, see `docs/COMBAT_PROGRESSION.md`.

## Content And Data

Game content is data-first:

- `config/items.json` defines inventory items, manuals, materials, pills, and rewards.
- `config/areas.json`, `config/gather_nodes.json`, and `config/hunt_targets.json` define exploration zones.
- `config/dungeons.json` and `config/cooperative_dungeons.json` define dungeon rewards and encounters.
- `config/manual_pools.json`, `config/shop.json`, and `config/sect_shops.json` define manual acquisition routes.
- `config/sects.json` and `config/sect_tasks.json` define fixed martial sects, gates, daily tasks, and merit rewards.
- `config/skill_idea_mapping.json` is a review artifact generated from draft skill ideas, not a runtime schema.

Use `py -m scripts.extract_skill_ideas` to refresh the skill idea mapping from
`%USERPROFILE%\Downloads\allskills.json` when reviewing new technique concepts.
Map sect-linked ideas to existing IDs from `config/sects.json`.

## Player-Facing Copy

Discord embeds, command descriptions, tutorials, guidance hints, error messages,
and autocomplete labels should stay in-world and action-oriented. Put design
rationale in maintainer docs or comments, not in strings shown to players.

Avoid process language such as `MVP`, `scaffold`, `backlog`, `future update`, or
phrases that explain removed flows. State what is true and what the player can
do next.

## Posting Guides

Post the server tutorial with:

```powershell
py -m src.post_tutorial
```

Post the manual library with:

```powershell
py -m src.post_library
```

Admins can also use `/post-tutorial` and `/post-library`. Both commands clear old
bot posts in the target channel before reposting.

## Deployment

The Railway deployment uses Railpack and a persistent SQLite volume.

- `railpack.json` sets the start command and `DATABASE_PATH=/data/cultivation_bot.sqlite3`.
- `railway.json` expects a volume mounted at `/data`.
- `RAILWAY_VOLUME_MOUNT_PATH` is honored when `DATABASE_PATH` is unset.

One-time Railway setup:

1. Link the repo and deploy.
2. Add a service volume mounted at `/data`.
3. Set `DISCORD_TOKEN`, `GUILD_ID`, and any channel/category IDs from `.env.example`.
4. Seed or upload the SQLite database if you want existing player data in production.

Player data is not committed by default. `.gitignore` excludes `*.sqlite3`.

Seed through Git:

```powershell
.\scripts\publish_database_seed.ps1
git add deploy/seed/cultivation_bot.sqlite3
git commit -m "Add database seed for Railway"
git push
```

On startup, the bot copies `deploy/seed/cultivation_bot.sqlite3` to
`DATABASE_PATH` when `DATABASE_SEED_MODE=if_empty` and the target database has no
players. To overwrite a deployment once, set `DATABASE_SEED_MODE=always`,
redeploy, confirm the result, then return the variable to `if_empty`.

Upload without committing the database:

```powershell
railway login
cd c:\Users\Adnan\Documents\CursorProjects
railway link
.\scripts\upload_database_to_railway.ps1
```

Stop the bot first, upload, then redeploy.

## Discord Permissions

The bot needs the standard slash-command permissions plus:

- Manage Channels: create private abode, dungeon, and arena channels.
- Manage Roles: assign realm roles after `/start` and breakthroughs.
- A bot role placed above realm roles.

Optional environment variables control where private channels and feeds appear:
`ABODE_CATEGORY_ID`, `DUNGEON_CATEGORY_ID`, `ARENA_CATEGORY_ID`,
`PVP_RESULTS_CHANNEL_ID`, `TUTORIAL_CHANNEL_ID`, and `LIBRARY_CHANNEL_ID`.

## Tests

Backend tests use pytest with an in-memory SQLite database and do not require a
Discord connection. Slash-command integration tests call registered command
callbacks with mock interactions and validate views, `custom_id` uniqueness, and
workflows such as `/hunt` combat buttons. Multi-step **player scenarios**
(`tests/test_player_scenarios.py`) chain commands the way someone uses the bot in
an abode channel — e.g. hunt → `/techniques` PNG hub → every hub button → back.

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest tests -v
py -m pytest tests/test_player_scenarios.py -v
py -m pytest tests/test_hunt_full_fight_integration.py -v
py -m pytest tests/test_dungeon_full_fight_integration.py -v
py -m pytest tests/test_adventure_full_flow_integration.py -v
py -m pytest tests/test_cultivate_integration.py -v
py -m pytest tests/test_slash_commands_integration.py -v
```

When adding a slash command, add a matching spec in `tests/slash_command_specs.py`.
When changing player-facing strings, run `py -m pytest tests/test_player_facing_copy.py -v`.
