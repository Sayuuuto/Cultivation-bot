# PvP Loadout Policy

Enforcement rules, caps, edge cases, and implementation locations for PvP loadout validation.

## Defaults

- **Challenge creation:** validate challenger loadout; reject `/duel` with explicit violation list.
- **Acceptance:** validate **both** loadouts at accept time; if opponent is illegal, accept fails with violations named.
- **Mid-match:** no loadout changes; desyncs forfeit the offending player (existing match expiry handles it).

## Limits

Defined in `config/combat_rules.json`:

| Tag | Cap |
|-----|-----|
| `legendary` | 1 |
| `control` | 2 |
| `shield` | 2 |
| `healing` | 2 |
| `survival_passive` | 1 |

Load budget per realm: `config/realms.json` → `technique_load_budget`.

## Edge Cases

| Case | Behavior |
|------|----------|
| Challenger illegal | Block `/duel` with reasons |
| Opponent illegal on accept | Accept fails; message lists opponent violations |
| Realm up during pending challenge | Re-validated on accept |
| Passive over survival cap | Offending technique names included in error |
| Sealed technique equipped | Blocked at equip; treated as not learned |

## Implementation Locations

- `list_pvp_loadout_violations()` — `src/combat/loadout.py`
- `create_duel_challenge()` — challenger pre-check
- `accept_duel_challenge()` — both players before `begin_pvp_match()`
- Player-facing copy: `config/player_guides/combat_progression.json` → `pvp_legality`
