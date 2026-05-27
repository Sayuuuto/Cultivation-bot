# Cultivation Discord Bot (MVP)

Serious xianxia cultivation game for Discord (Python).

## Setup

1. Create a virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Copy env template:
   - `copy .env.example .env`
4. Set `DISCORD_TOKEN` in `.env`.
5. Run the bot:
   - `python -m src.bot`
6. Sync slash commands after command changes:
   - `py -m src.sync_commands`

## Game overview

Three activity lanes:

| Lane | Commands | Pace |
|------|----------|------|
| **Cultivation** | `/cultivate`, `/breakthrough` | 15 min |
| **Resource** | `/gather`, `/hunt` (button combat) | 5 min |
| **Story** | `/adventure`, `/dungeon` | 20 min / 2 hr |

**Martial techniques:** hunt/adventure/dungeon/shop drop manuals ? /learn ? /equip-technique. Use **/techniques** for loadout, alignment tags, synergy hints, and study/equip menus. Post the full manual catalog to a channel with **/post-library** (or py -m src.post_library).

**Karma:** earned through adventure moral choices (not chosen at /start). Righteous (+30+) and Demonic (?30?) tiers bias manual pool rolls and breakthrough manuals. Combat stats are unchanged — build identity comes from techniques.

**UI:** combat shows HP bars, status badges, technique cooldowns on buttons, and emoji combat logs. `/cultivate` has ~12% rare dao events (qi surges, spirit veins, manual drops, fragments).

Most selection commands use **autocomplete** filtered to what you can actually use (manuals in bag, craftable recipes, affordable shop items, unlocked areas, etc.).

Post the server tutorial with **`/post-tutorial`** (or `py -m src.post_tutorial`) and the manual library with **`/post-library`**. Both commands clear old bot posts in the target channel before reposting.

## Notes

- First run creates the SQLite tables automatically (MVP-level).
- Slash command registration uses `GUILD_ID` if provided.
- `/start` creates a private **abode** channel (`abode-your-dao-name`) and assigns a **realm role** (starting at Mortal). The bot needs **Manage Channels** and **Manage Roles**, with its role placed above realm roles. Optionally set `ABODE_CATEGORY_ID` in `.env` for where abodes are created.

## Tests

Backend tests (game logic, inventory, cooldown/datetime helpers) use pytest with an in-memory SQLite DB  no Discord connection required.

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest tests -v
```
