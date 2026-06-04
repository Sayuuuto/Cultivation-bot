# Cultivation Discord Bot

Serious xianxia cultivation game for Discord — Python, discord.py, SQLAlchemy, SQLite, JSON content files, pytest.

## Quick Start

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set DISCORD_TOKEN
python -m src.bot
```

Windows: `py -m venv .venv` / `.venv\Scripts\activate` / `copy .env.example .env`

Sync slash commands after command changes: `python -m src.sync_commands`

Test dependencies: `pip install -r requirements-dev.txt`

## Project Layout

```
src/
  bot.py                   # Bot entry point, Cog registration, event handlers
  discord_ui/              # Discord adapters (no domain logic)
    commands/              #   11 app-command Cogs
    views/                 #   4 view builders (combat, adventure, duel, cultivate)
    helpers.py             #   Shared helper functions (~700 lines)
    autocomplete.py        #   Autocomplete functions (~350 lines)
  combat/                  # Domain: combat engine, techniques, loadouts
    catalog.py             #   Technique JSON parser → TechniqueDef
    triggers.py            #   Passive trigger resolver
    effects.py             #   Status state, ticks, cleanse
    loadout.py             #   Equip, budgets, PvP legality
    rules.py               #   Combat rules loader
    effect_defs.py         #   Effect/trigger dataclasses
  config/                  # JSON content files (see DESIGN.md §Content Files)
  tests/                   # pytest suite (in-memory SQLite, no Discord needed)
docs/                      # Maintainer guides
assets/                    # Fonts for card images
scripts/                   # Utility scripts
```

## Game Commands

| Category | Commands |
|----------|----------|
| Character | `/start`, `/profile`, `/reset` |
| Cultivation | `/daily`, `/cultivate`, `/breakthrough` |
| Resources | `/gather`, `/hunt` |
| Story | `/adventure`, `/adventure-continue`, `/dungeon` |
| Builds | `/techniques`, `/technique`, `/learn`, `/equip-technique`, `/craft manual` |
| Economy | `/inventory`, `/item`, `/shop`, `/recipes`, `/craft pill`, `/craft key`, `/forge` |
| PvP | `/duel`, `/leaderboard` |
| Social | `/clan`, `/sect-list`, `/sect`, `/sect-join`, `/sect-task`, `/sect-shop` |
| Admin | `/post-tutorial`, `/post-library` |

Autocomplete filters every selection to the player's current state (owned manuals, craftable recipes, affordable shop items, etc.).

## Deployment (Railway)

- `railpack.json` sets start command; `DATABASE_PATH=/data/cultivation_bot.sqlite3`
- `railway.json` expects a volume at `/data`
- `RAILWAY_VOLUME_MOUNT_PATH` honored when `DATABASE_PATH` unset
- Player data `.gitignore`d (`*.sqlite3`)

### Seeding

Set `DATABASE_SEED_MODE=if_empty` (or `always`). On startup the bot copies `deploy/seed/cultivation_bot.sqlite3` to `DATABASE_PATH` when the target has no players.

**Seed through Git:**
```sh
./scripts/publish_database_seed.ps1
git add deploy/seed/cultivation_bot.sqlite3
git commit -m "Add database seed for Railway"
git push
```

**Upload without committing:**
```sh
railway login && railway link
./scripts/upload_database_to_railway.ps1
```
Stop the bot first, upload, then redeploy.

## Discord Permissions

Standard slash-command permissions plus:
- **Manage Channels** — private abode, dungeon, arena channels
- **Manage Roles** — realm roles after `/start` and breakthroughs
- Bot role must sit above realm roles

Optional env vars: `ABODE_CATEGORY_ID`, `DUNGEON_CATEGORY_ID`, `ARENA_CATEGORY_ID`, `PVP_RESULTS_CHANNEL_ID`, `TUTORIAL_CHANNEL_ID`, `LIBRARY_CHANNEL_ID`.

## Player-Facing Copy

Discord embeds, command descriptions, tutorials, guidance hints, error messages, and autocomplete labels should stay **in-world** and **action-oriented**. Put design rationale in maintainer docs or comments, not in strings shown to players.

Avoid process language: `MVP`, `scaffold`, `backlog`, `future update`, or phrases that explain removed flows. State what is true and what the player can do next.

Tested by: `python -m pytest tests/test_player_facing_copy.py -v`

## Posting Guides

Post the server tutorial: `python -m src.post_tutorial`
Post the manual library: `python -m src.post_library`

Admins can also use `/post-tutorial` and `/post-library`. Both clear old bot posts in the target channel before reposting.

## Testing

```sh
python -m pytest tests -v
python -m pytest tests/test_player_scenarios.py -v
python -m pytest tests/test_slash_commands_integration.py -v
```

See `DESIGN.md §Test Strategy` for the focused test matrix.

## Design Docs

| Doc | Covers |
|-----|--------|
| `DESIGN.md` | Product goals, progression, karma, social, config standards |
| `COMBAT_DESIGN.md` | Combat system, techniques, statuses, loadouts, karma |
| `PHASE1_PVE_DESIGN.md` | PvE, crafting, inventory, equipment, dungeons |
| `docs/COMBAT_PROGRESSION.md` | Maintainer contract, technique schema, feature flags, validation |
| `docs/FRAGMENT_ECONOMY.md` | Fragment earn/spend tuning, balance targets |
| `docs/PVP_LOADOUT_POLICY.md` | PvP caps, edge cases, implementation locations |
| `docs/OPS_DATA_RECOVERY.md` | Backup/restore runbook, rollback compensation |
