# Interactive Dungeon Redesign — Design Plan

## Goal

Replace one-click solo dungeon RNG with room-by-room progression and difficulty modes.

## Current State

- **Solo** `src/dungeon.py`: segment RNG, not exposed as Discord command
- **Coop** `/dungeon`: party invites, turn-based combat via `dungeon_combat.py`
- Config: `config/dungeons.json` (drop tables only), `config/cooperative_dungeons.json`

## Target: Solo Interactive Dungeons

Each dungeon run:

1. **Entry** — consume key, pick difficulty
2. **Rooms** (3–6) — choice: safe path / risky path / rest
3. **Encounters** — reuse expedition encounter resolver
4. **Boss room** — full turn-based combat (existing engine)

### Room schema (`config/dungeons.json`)

```json
"misty_cavern": {
  "rooms": [
    {"id": "fork", "choices": [
      {"label": "Left tunnel", "next": "spiders", "risk": "high"},
      {"label": "Right tunnel", "next": "cache", "risk": "low"}
    ]},
    {"id": "spiders", "encounter": "cave_spiders"},
    {"id": "boss", "encounter": "cavern_lord", "boss": true}
  ]
}
```

## Difficulty Modes

| Mode | Enemy stat scale | Loot scale | Requirements |
|------|------------------|------------|--------------|
| Normal | 1.0× | 1.0× | Solo |
| Hard | 1.35× | 1.5× | Solo |
| Nightmare | 1.75× | 2.0× | Party required |

## Implementation Order

1. Expose solo dungeon via `/dungeon-solo` (or extend `/dungeon` with mode param)
2. Room state machine in `dungeon.py` using expedition encounter hooks
3. Boss fights via `create_combat_state` + `CombatView`
4. Difficulty scaling on monster templates
5. Align loot tables per difficulty

## Dependencies

- Unified expedition encounter system (`EXPEDITION_DESIGN.md`)
- Achievement hooks for first clear / nightmare clear
