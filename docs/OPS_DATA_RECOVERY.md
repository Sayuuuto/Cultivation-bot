# Ops Data Recovery

Backup, restore, and rollback procedures for the production SQLite database.

## Before Destructive Scripts

1. Note `DATABASE_PATH` (default: `cultivation_bot.sqlite3` in project root).
2. Copy: `cp cultivation_bot.sqlite3 cultivation_bot.backup.sqlite3`
3. Record player count: `sqlite3 cultivation_bot.sqlite3 "SELECT COUNT(*) FROM players;"`

## Restore From Profile Snapshot

Applies a saved player profile JSON (schema version **`profile_schema_version`: 2**, includes per-technique `rank`).

```sh
python scripts/restore_player_snapshot.py scripts/profiles/void_great_emperor.json --dry-run
python scripts/restore_player_snapshot.py scripts/profiles/void_great_emperor.json --discord-id YOUR_DISCORD_ID
```

Dry-run is the default safe path — omit `--dry-run` only when applying.

## Compensate After Rollback

```sh
python scripts/compensate_rollback.py --discord-ids ID1,ID2 --dry-run
python scripts/compensate_rollback.py --discord-ids ID1,ID2
```

Restores realm/substage/qi, grants random learnable techniques, auto-equips actives. Does not delete unrelated inventory.

## Post-Restore Verification

1. `/profile` — realm, qi, martial dao summary
2. `/techniques` — loadout + ranks
3. `/inventory` — sealed manual labels

## Deploy Checklist

- [ ] Snapshot DB + player count checksum
- [ ] Deploy with seed mode `never`
- [ ] Post-deploy player count sanity check
- [ ] Spot-check 3 Discord IDs (profile, techniques, one manual)
