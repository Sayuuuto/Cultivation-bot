# Ops data recovery runbook

## Before destructive scripts

1. Note `DATABASE_PATH` (or default `cultivation_bot.sqlite3` in project root).
2. Copy the live DB: `copy cultivation_bot.sqlite3 cultivation_bot.backup.sqlite3`
3. Record player count: `sqlite3 cultivation_bot.sqlite3 "SELECT COUNT(*) FROM players;"`

## Restore from profile snapshot

```powershell
set DATABASE_PATH=C:\path\to\cultivation_bot.sqlite3
py scripts/restore_player_snapshot.py scripts/profiles/void_great_emperor.json --dry-run
py scripts/restore_player_snapshot.py scripts/profiles/void_great_emperor.json --discord-id YOUR_DISCORD_ID
```

Profile JSON schema version: **`profile_schema_version`: 2** (includes per-technique `rank`).

Dry-run is the default safe path — omit `--dry-run` only when applying changes.

## Compensate after rollback

```powershell
py scripts/compensate_rollback.py --discord-ids ID1,ID2 --dry-run
py scripts/compensate_rollback.py --discord-ids ID1,ID2
```

Restores realm/substage/qi, grants random learnable techniques, auto-equips actives — does not delete unrelated inventory.

## Post-restore verify

1. `/profile` — realm, qi, martial dao summary
2. `/techniques` — loadout + ranks
3. `/inventory` — sealed manual labels

## Deploy checklist

- [ ] Snapshot DB + player count checksum
- [ ] Deploy with seed mode `never`
- [ ] Post-deploy player count sanity
- [ ] Spot-check 3 discord IDs (profile, techniques, one manual)
