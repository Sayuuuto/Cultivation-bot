# Unified Expedition System — Design Plan

## Goal

Merge `/adventure` and `/explore` into a single **Expedition** activity that combines route branching, vitality tracking, stances, affinity, and rare findings.

## Current State

- `/adventure`: 2-segment runs, stances, karma, `rare_events` in `config/areas.json`
- `/explore`: 10–15 step runs, vitality, affinity, manual/qi burst rewards, 24h cooldown

## Target Experience

| Aspect | Target |
|--------|--------|
| Length | 5–10 steps per run |
| Vitality | Run-wide pool (from explore) |
| Branching | Route tags + stance modifiers (from adventure) |
| Affinity | Spirit root + `story_path` (fixed in explore.py) |
| Rare findings | Per-step `rare_findings` in `config/areas.json` |
| Cooldown | Single expedition cooldown (24h or configurable) |

## Schema: `rare_findings`

```json
"bamboo_grove": {
  "rare_findings": [
    {"id": "ancient_scroll", "name": "Ancient Scroll", "chance": 0.02, "type": "manual"},
    {"id": "spirit_herb", "name": "Spirit Herb", "chance": 0.03, "type": "material"}
  ]
}
```

Roll after each successful step. Origin `dropBonus` and path `manual_bias`/`pill_bias` modify pools.

## Path / Origin Impact

- **Path** (`story_path`): filters encounter pool tags in `adventure_encounters.json`
- **Origin**: `dropBonus` / `adventure_luck` modify rare finding chance
- Wire `get_path_flags()` and `manual_bias`/`pill_bias` from `explore_areas.json`

## Migration Path

1. Add `rare_findings` to `config/areas.json` and roll logic in `adventure.py`
2. Extend adventure to 5–10 steps with vitality from explore
3. Deprecate `/explore` command — redirect to `/adventure` with alias message
4. Remove duplicate `ExploreSession` after data migration

## Commands (final)

- `/adventure` — start expedition (replaces explore for long runs)
- `/adventure-continue` — resume paused run
- `/adventure-abandon` — quit with confirmation (done)

## Out of Scope (this doc)

- Dungeon room system (see `DUNGEON_DESIGN.md`)
- World boss integration
