# Combat, Techniques, And Karma Design

Data-driven turn-based combat shared by hunts, adventures, dungeons, and PvP. The system grows by adding JSON definitions and generic effect handlers, not one-off branches for individual arts.

For the lower-level maintainer contract see `docs/COMBAT_PROGRESSION.md`.

## Activity Lanes And Rewards

| Lane | Primary Rewards |
|------|----------------|
| `/cultivate`, `/breakthrough` | Qi, realm progress |
| `/gather`, `/hunt` | Herbs, ore, cores, beast parts, manuals |
| `/adventure`, `/dungeon` | Moral shifts, bosses, rare drops |
| `/techniques`, `/learn`, `/equip-technique` | Build management |
| `/duel` | Arena combat (loadout legality checked first) |

Each lane has distinct primary rewards so short cooldown loops don't dominate all progression.

## Combat Stats

From realm, root, cultivation, equipment, effects, and modifiers (`config/realm_stats.json`):

HP, internal strength (qi techniques), external strength (physical), agility (speed/dodge), spiritual sense (crit/detection), defense (DR), comprehension (gathering/learning), luck (rare drops/crit).

Realm names, qi caps, load budgets, rank caps: `config/realms.json`.

## Technique Runtime

`config/techniques.json` → `src/combat/catalog.py` → `TechniqueDef` dataclass.

Active effects: `effects[]` — objects with `trigger`, `type`, and type-specific params.
Passive triggers: `passive_triggers[]` — objects with `event`, `type`, and type-specific params.

Runtime effect objects: `src/combat/effect_defs.py`.

### Event Families

`on_use`, `on_hit`, `on_crit`, `on_status_applied`, `on_turn_start`, `on_turn_end`, `on_hp_threshold`, `on_cc_received`, `on_fatal`.

### Effect Families

Damage, multi-hit, status application, heal, lifesteal, shield, cleanse, dodge, execute, reflect, cooldown adjustment, conditional bonuses.

**Catalog fallbacks** (`src/combat/catalog.py`) still parse older passive fields (e.g., `passive_crit_bonus`) into trigger definitions for backward compatibility. Use `passive_triggers` for all new content.

## Status Effects

Metadata: `config/combat_rules.json`, loaded by `src/combat/rules.py`.

| Status | Role |
|--------|------|
| bleed | Stackable physical DoT; lifesteal/bleed payoffs |
| burn | DoT with spread and fire payoff hooks |
| poison | Long attrition, anti-heal |
| stun | Hard turn cancel |
| seal | DR/control pressure |
| fear | Chance to skip turns |

Tags: `dot`, `control`, `cleanseable`, `anti_heal`. Control statuses define diminishing-return metadata.

## Loadout And PvP

Four active + one passive slot. Load budgets limit build weight by realm (`config/realms.json`).

Key module: `src/combat/loadout.py` — learned technique lookup, starter grants, equip validation, realm budget, PvP legality, rank lookup.

PvP caps (`config/combat_rules.json`): legendary=1, control=2, shield=2, healing=2, survival_passive=1.

## Technique Rarity

| Rarity | Acquisition |
|--------|-------------|
| common | Early shops, common pools, craft |
| uncommon | Gambles, moral pools, sects, upgraded craft |
| rare | Breakthroughs, rare events, dungeons, elite hunts |
| legendary | Strict reward paths |

Rarity caps per source: `src/combat/rarity.py`. Manual pools: `config/manual_pools.json`. Shop/sect routes: `config/shop.json`, `config/sect_shops.json`.

## Build Archetypes

- Sword/bleed: apply bleed → sustain/finishers
- Fire/burn: apply burn → amplify → cash out
- Body/control: shield, stun, seal, Basic Strike pressure
- Soul/attrition: poison, soul techniques, long fights
- Utility/cleanse: remove statuses, dodge, shield, survive burst
- Critical tempo: stack crit, reflect, consecutive-hit payoffs

Use `synergy_hint`, `role`, `category`, `tags`, and effect primitives to make identities visible in `/techniques` and maintainable in config.

## Combat Flow

```
Engage → Discord combat buttons → Player action → Resolve effects/triggers
→ Opponent action + status ticks → Check finished? → Loop or reward
```

Combat sessions persist until victory, defeat, flee, finish, expiry, or duel completion. Views show HP bars, statuses, technique cooldowns, Basic Strike, flee, finish.

## Karma

Range `-100` to `+100`, starts neutral. Adventure choices shift via `karma_delta` in `config/adventure_encounters.json`.

Affects: breakthrough odds, cultivation flavor, manual pool weights (`manual_weight_multiplier()` in `src/karma.py`), profile tier.

## Manual Acquisition (spread across activities)

- Cultivate/breakthrough rewards
- Adventure moral pools and rare events
- Hunt/dungeon drops
- Shop listings and manual gambles
- Craft/manual fragment routes
- Sect shops and progression

Duplicate known manuals → technique fragments. Manuals above player's realm → sealed (when `sealed_manuals` flag enabled); open when realm requirement met.

## Skill Ideas

`scripts/extract_skill_ideas.py` converts draft ideas (`allskills.json`) → `config/skill_idea_mapping.json` for review. Output is a review artifact — don't import draft schema into runtime.

## Tests

```sh
python -m pytest tests/test_combat_engine.py -v
python -m pytest tests/test_combat_triggers_and_karma.py -v
python -m pytest tests/test_pvp_combat.py -v
python -m pytest tests/test_manual_acquisition.py -v
```

Add coverage when changing: effect order, status behavior, manual weights, sealed manual behavior, load budgets, PvP caps.
