# World Bosses & Limited Events — Design Plan

## Goal

Weekly shared-world boss fights and a config-driven rotating event system.

## World Boss (Weekly)

### Mechanics

- **Schedule**: Every Sunday 00:00 UTC, 7-day window
- **Shared HP pool**: All server players contribute damage
- **Attack limit**: 1 attack per player per 15 minutes
- **Resolution**: Boss defeated or window ends → tiered loot by contribution %

### Data Model

```python
class WorldBoss(Base):
    id, guild_id, boss_id, max_hp, current_hp, starts_at, ends_at, defeated

class WorldBossContribution(Base):
    id, boss_id, player_id, damage_total, last_attack_at
```

### Player Flow

1. `/world-boss` — shows boss art, HP bar, your contribution, time remaining
2. `/world-boss attack` — single technique strike (uses combat stats, no full fight UI)
3. Results embed at end with loot tiers (requires achievement system for badges)

### Config (`config/world_bosses.json`)

```json
{
  "bosses": [
    {
      "id": "thunder_serpent",
      "name": "Thunder Serpent of the Eastern Sea",
      "max_hp": 5000000,
      "loot_tiers": [
        {"min_contribution_pct": 0.01, "rewards": ["spirit_stones:50"]},
        {"min_contribution_pct": 0.10, "rewards": ["manual_pool:rare"]}
      ]
    }
  ]
}
```

## Limited-Time Events

### Data Model

```python
class GameEvent(Base):
    id, event_id, guild_id, starts_at, ends_at, active
```

### Rotation

- `config/events.json` defines event templates (double gather, bonus hunt manuals, sect merit ×2)
- `@tasks.loop` in `bot.py` transitions events at UTC boundaries
- Optional: Discord Scheduled Events API for visibility

### Commands

- `/events` — active and upcoming events
- Event modifiers hook into gather/hunt/adventure reward functions via `get_active_event_modifiers(guild_id)`

## Dependencies

- Phase 4 achievements for contribution tier badges
- Stable combat stats for boss damage calculation

## Out of Scope (initial ship)

- Cross-server bosses
- Real-time boss fight UI (use strike command first)
