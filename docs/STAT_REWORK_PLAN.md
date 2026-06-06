# Stat System Rework Plan

Full redesign of the stat architecture. One stat, one job. No bleed across systems.

## Problems Being Solved

1. **Three-layer stat soup** — Equipment Stats → Character Modifiers → Combat Stats creates unintelligible conversion chains
2. **Stat name incoherence** — Same concept has 3 different names (power → pvp_power → external_strength)
3. **Asymmetric defense** — Player defense absorbs 45%, opponent defense absorbs 35%. Hidden and unexplained
4. **Stat bleed** — adventure_success feeds crit, adventure_defense feeds dodge, pvp_power feeds adventure power
5. **Gear stat confusion** — "Insight" splits 60/40 on internal gear. Players can't predict what they're getting
6. **17 modifier fields** — No hierarchy, no grouping, impossible to understand at a glance
7. **Luck does everything** — crit, drops, quantity, gather, cultivation variance. Too many jobs
8. **Comprehension is dead** — Near-zero impact at early realms, meaningless display at late realms
9. **Unreadable numbers** — Stats go from 12 to 960,000,000. Meaningless at high realms

## New Architecture

### Combat Stats (7 stats, down from 8+2)

| Stat | Key | Purpose | Replaces |
|------|-----|---------|----------|
| **HP** | `hp` | Health points | `hp` (unchanged) |
| **Might** | `might` | Physical attack power | `external_strength` |
| **Qi Power** | `qi_power` | Magical/internal attack power | `internal_strength` |
| **Armor** | `armor` | Damage reduction | `defense` |
| **Speed** | `speed` | Turn order, dodge chance | `agility` |
| **Perception** | `perception` | Crit chance, status application | `spiritual_sense` + `comprehension` |
| **Resolve** | `resolve` | CC resistance, status cleansing | NEW |

**Removed:**
- `luck` → split into Fortune (non-combat) and removed from combat
- `comprehension` → merged into Perception
- `spiritual_sense` → renamed to Perception

### Proficiency Stats (3 stats, used in non-combat systems)

| Stat | Key | Purpose | Replaces |
|------|-----|---------|----------|
| **Fortune** | `fortune` | Drop rate, rare event chance, gather bonus | `luck` combat stat + `drop_luck` modifier |
| **Insight** | `insight` | Cultivation speed, breakthrough success | `breakthrough_stability` + `cultivate_qi_mult` |
| **Cunning** | `cunning` | Adventure success, dungeon rewards | `adventure_success` + `adventure_defense` |

These are derived from gear and modifiers, not standalone combat stats.

### Character Modifiers (8 fields, down from 17)

```python
@dataclass
class CharacterModifiers:
    # Cultivation
    cultivate_speed: float = 1.0        # Multiplies qi gain
    offline_efficiency: float = 1.0     # Multiplies passive qi bank

    # Breakthrough
    breakthrough_luck: float = 0.0      # Additive success bonus
    setback_resistance: float = 1.0     # Multiplies qi loss on fail

    # Combat
    damageBonus: float = 0.0            # Additive damage multiplier
    damageReduction: float = 0.0        # Additive armor multiplier

    # Adventure/Loot
    adventure_luck: float = 0.0         # Additive success + rare event bonus
    dropBonus: float = 0.0              # Additive drop rate bonus

    active_effects: list[str] = field(default_factory=list)
```

### Gear Stats (4 stats, down from 5)

| Gear Stat | Combat Mapping | Non-Combat Mapping |
|-----------|---------------|-------------------|
| **Might** | +Might (or +Qi Power on internal path) | — |
| **Warding** | +Armor | — |
| **Vitality** | +HP | — |
| **Finesse** | +Perception | +Fortune (1:1) |

### Damage Formula (simplified)

```python
# Player dealing damage:
raw = base_damage + stat * scaling_ratio
raw *= rarity_multiplier
mitigation = target.armor * 0.40
damage = max(1, raw - mitigation)
if is_crit:
    damage *= 1.5

# Opponent dealing damage (SAME FORMULA):
raw = opponent_attack * variance(0.90, 1.10)
mitigation = player.armor * 0.40
damage = max(1, raw - mitigation)
```

**Key change:** Both sides use the same 40% defense absorption. No more asymmetric formulas.

### Crit and Dodge (clean, single-stat)

```python
crit_chance = min(0.50, perception * 0.0004)
dodge_chance = min(0.40, speed * 0.0004)
```

No more `adventure_success` feeding crit. No more `adventure_defense` feeding dodge.

### Fortune System (separate from combat)

```python
fortune = gear_fortune + modifier.dropBonus
drop_chance = base_chance * (1.0 + fortune * 0.01)
rare_event_chance = base_chance * (1.0 + fortune * 0.005)
gather_bonus = 1.0 + fortune * 0.003
```

### Modifier Application Order

```python
def get_character_modifiers(session, player) -> CharacterModifiers:
    mod = CharacterModifiers()

    # 1. Origin bonuses
    apply_origin(mod, player.origin)

    # 2. Spirit root bonuses
    apply_root(mod, player.spirit_root)

    # 3. Karma effects
    apply_karma(mod, player.karma)

    # 4. Equipment affixes
    apply_affixes(mod, session, player.id)

    # 5. Active effects (pills, shrine)
    apply_effects(mod, session, player.id)

    return mod
```

### Stat Display (percentage-based)

```
Combat Stats
  HP        1,250
  Might       450  (120% of Qi Refining baseline)
  Qi Power    380  (101% of Qi Refining baseline)
  Armor       120  (100% of Qi Refining baseline)
  Speed       160  (133% of Qi Refining baseline)
  Perception  120  (100% of Qi Refining baseline)
  Resolve      80  (new stat)

  Crit  4.8%  ·  Dodge  6.4%

Proficiency
  Fortune    +15% drop bonus
  Insight    +8% cultivate speed
  Cunning    +6% adventure success
```

## Files To Change

### Config Files
- `config/realm_stats.json` — New stat keys, new baselines
- `config/techniques.json` — `scaling_stat` field updates
- `config/combat_rules.json` — Remove asymmetric defense, update status formulas
- `config/affixes.json` — New modifier key names
- `config/spirit_roots.json` — New modifier key names
- `config/origins.json` — New modifier key names
- `config/pill_effects.json` — New modifier key names
- `config/foundation.json` — New stat keys for body/meridian
- `config/equipment_forge.json` — New stat field names
- `config/drop_rarity.json` — Update to use Fortune

### Source Files
- `src/modifiers.py` — 8-field CharacterModifiers
- `src/combat_stats.py` — New stat computation, new baselines
- `src/stats.py` — New gear mapping, new display
- `src/combat/triggers.py` — Simplified damage formula
- `src/combat/engine.py` — Unified defense, new opponent formula
- `src/combat/catalog.py` — TechniqueDef scaling_stat updates
- `src/combat/loadout.py` — PvP validation updates
- `src/combat/rarity.py` — Check rarity_damage_multiplier still valid
- `src/character.py` — New modifier stacking
- `src/auto_combat.py` — New auto-combat formulas
- `src/effects.py` — New effect mapping
- `src/equipment.py` — New affix field mapping
- `src/game.py` — Cultivation/breakthrough formula updates
- `src/adventure.py` — Adventure formula updates
- `src/loot.py` — Drop formula updates
- `src/explore.py` — Explore formula updates
- `src/dungeon.py` — Dungeon formula updates
- `src/pvp_combat.py` — PvP formula updates
- `src/schemas.py` — New stat type literals
- `src/player_dashboard.py` — Display updates
- `src/roots_info.py` — Display updates
- `src/technique_info.py` — Display updates
