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

## Notes
- First run creates the SQLite tables automatically (MVP-level).
- Slash command registration uses `GUILD_ID` if provided.

## Tests

Backend tests (game logic, inventory, cooldown/datetime helpers) use pytest with an in-memory SQLite DB — no Discord connection required.

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest tests -v
```

