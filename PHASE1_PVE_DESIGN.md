# Phase 1 PvE & Crafting Design

This document specifies the first PvE + crafting expansion that fits the current bot architecture.

## Summary

Add:
- Solo adventures in themed areas with drops and rare events.
- A single key-gated dungeon (solo enabled, party-ready).
- Basic inventory + items.
- Pills crafted from drops (with side effects).
- Simple equipment affixes that influence PvE and breakthroughs.

All data should be driven from config (JSON/YAML) where possible, with SQLite using generic tables like `inventory_items`, `recipes`, and `dungeon_runs`.

---

## Areas (adventure content)

### Area: Whispering Bamboo Grove (Easy)
- **Theme**: Beasts and herbs, soft entry point.
- **Recommended**: Mortal → Qi Refining.
- **Primary drops**:
  - `Green Dew Herb`
  - `Bamboo Resin`
  - `Minor Beast Core`
- **Rare events**:
  - Hidden Herb Patch (extra herbs).
  - Wandering Elder (small buff for next cultivate).

### Area: Ashen Cliff Pass (Medium)
- **Theme**: Bandits, fire remnants, harsher terrain.
- **Recommended**: Qi Refining → Foundation.
- **Primary drops**:
  - `Ember Moss`
  - `Spirit Iron Shard`
  - `Bandit Token`
- **Rare events**:
  - Ambush (risk of temporary debuff, higher rewards).
  - Abandoned Cart (key materials / affix stone chance).

### Area: Moonwell Ruins (Hard)
- **Theme**: Ancient constructs, moonlight and forgotten pools.
- **Recommended**: Foundation+.
- **Primary drops**:
  - `Moonlotus`
  - `Ancient Dust`
  - `Refined Beast Core`
- **Rare events**:
  - Hidden Moonwell (rare pill ingredients).
  - Inheritance Fragment (unlock or discount rare recipes).

### Adventure parameters (Phase 1)
- Command: `/adventure area:<name> stance:<cautious|balanced|reckless>`.
- Cooldown: 20 minutes between adventures.
- Segments per run: 2 encounter rolls (3 later).
- Stances:
  - `cautious`: +success chance, −drop quantity.
  - `balanced`: baseline.
  - `reckless`: +drop quantity, +failure/penalty chance.

---

## Dungeon: Blackwind Cavern (Phase 1)

- **Access**: Requires `Blackwind Key`.
- **Mode**: Solo supported; party hooks ready (party composition handled later).
- **Structure**:
  - 3 encounter steps (from a small table).
  - 1 boss check with win/lose outcome.
- **Cooldown**: 2 hours per player between completions.
- **Base rewards**:
  - Guaranteed:
    - 1× high-tier material (e.g. `Refined Beast Core` or `Ancient Dust`).
  - Chance:
    - 1× `Affix Stone`.
    - Unlock or duplicate of a pill recipe.

### Dungeon key

**Blackwind Key** (crafted):
- Recipe: `3 × Spirit Iron Shard` + `2 × Ancient Dust` + `1 × Minor Beast Core`.
- Consumed on dungeon start (regardless of fail/success in Phase 1).

---

## Items (Phase 1)

### Materials
- `Green Dew Herb`
- `Bamboo Resin`
- `Minor Beast Core`
- `Ember Moss`
- `Spirit Iron Shard`
- `Bandit Token`
- `Moonlotus`
- `Ancient Dust`
- `Refined Beast Core`

### Derived / system items
- `Blackwind Key` (dungeon access).
- `Affix Stone` (applies a random affix to one equipment piece).
- `Pill Ash` (byproduct of failed pill crafting; low-value filler).

---

## Pills (Phase 1)

Crafted via `/craft pill:<name> amount:<n>`, consuming materials from inventory.

1. **Qi Gathering Pill**
   - Effect: Next 3 `/cultivate` actions grant +X% qi (tuned, e.g. +30%).
   - Side effect: −Y% breakthrough chance for 30 minutes.
   - Purpose: Farm qi faster at the cost of breakthrough comfort.

2. **Tempering Pill**
   - Effect: +Defense / survivability in adventures and dungeons for 1 run.
   - Side effect: Stamina regeneration −Z% for 30 minutes (slightly slower loop).
   - Purpose: Help undergeared builds survive higher areas.

3. **Clarity Pill**
   - Effect: +small flat breakthrough success chance on the next attempt.
   - Side effect: No spirit stones drop from the next `/cultivate`.
   - Purpose: “Push breakthrough now” button.

4. **Swiftwind Pill**
   - Effect: Higher success chance for adventure checks (fewer failed segments).
   - Side effect: Slightly reduced drop quantity per segment.
   - Purpose: Safer farming when pushing into a harder area.

5. **Blood Ember Pill**
   - Effect: +offensive power in dungeons for 1 run (higher damage scaling).
   - Side effect: If the dungeon is failed, apply an extra qi loss penalty.
   - Purpose: Glass-cannon tool for confident players.

6. **Moonwell Tonic**
   - Effect: +rare event chance for next solo adventure.
   - Side effect: Reduced effective combat power for that run.
   - Purpose: Target farming for rare materials/recipes.

Recipes should live in config, mapping required items → output pill(s), plus success/failure odds and byproduct (ash).

---

## Equipment & Affixes (Phase 1)

### Slots
- Weapon
- Armor
- Accessory
- Talisman

### Affixes

Numeric and single-purpose for MVP:

- `Keen`:
  - +Power (affects PvE damage / duel strength).
- `Guarding`:
  - +Defense (reduces effective damage in adventures/dungeons).
- `Flowing`:
  - Better stamina efficiency (consume slightly less or regen slightly more).
- `Fortunate`:
  - Slightly higher item drop chance / rarity in adventures.
- `Steady`:
  - Increased breakthrough stability (less qi loss on fail).
- `Ravenous`:
  - Increased dungeon damage but slightly higher penalties from failure.

Affixes are applied via `Affix Stone` (and/or rare drops from dungeons).

---

## Rare Events (Phase 1)

### Trigger model
- Each adventure segment has a base rare-event chance, e.g. 8%.
- Area and pills can modify it:
  - `Moonwell Ruins`: +2%.
  - `Moonwell Tonic`: +X%.

### Example rare events

- **Hidden Herb Patch**:
  - Extra herbs from area’s herb pool.
- **Ancient Cache**:
  - Small chance to drop `Affix Stone` or key material.
- **Wandering Elder**:
  - Temporary buff (e.g. +qi gain for the rest of the day or +breakthrough success for one attempt).
- **Cursed Shrine**:
  - Player chooses:
    - Accept boon (small power buff) + add a temporary debuff.
    - Decline with no effect.
- **Inheritance Fragment** (low chance in `Moonwell Ruins`):
  - Unlocks a rare pill recipe or grants a one-time high-value consumable.

Implementation: rare event selection should be driven by tables per area.

---

## Commands (Phase 1)

Add the following commands (names can be refined but should be clear):

1. `/adventure area:<name> stance:<cautious|balanced|reckless>`
   - Resolves 2 segments, awards items + xp-esque rewards.

2. `/inventory`
   - Shows items (materials, pills, keys, affix stones) and quantities.

3. `/craft pill:<name> amount:<n>`
   - Consumes materials, applies craft success/failure logic.

4. `/craft key dungeon:blackwind`
   - Crafts `Blackwind Key` from its recipe if the player has materials.

5. `/dungeon start name:blackwind mode:<solo>`
   - Checks key, difficulty, and starts a run.

6. `/equip slot:<weapon|armor|accessory|talisman> item:<id-or-name>`
   - Phase 1: simple stat application; no UI for comparing gear yet.

7. `/loadout`
   - Shows current stats including affix effects and core derived numbers (power, defense, luck, etc. when implemented).

---

## Data Model Additions (high-level)

At minimum:

- `inventory_items`
  - `id`, `player_id`, `item_id`, `quantity`
- `recipes`
  - `id`, `recipe_type` (pill/key/other), `output_item_id`, `output_quantity`, `inputs_json`, `success_chance`, `byproduct_item_id`
- `player_equipment`
  - `id`, `player_id`, `slot`, `item_id`, `affix_id`
- `dungeon_runs`
  - `id`, `leader_player_id`, `dungeon_id`, `mode`, `outcome`, `rewards_json`, `created_at`
- `adventure_runs`
  - `id`, `player_id`, `area_id`, `stance`, `outcome`, `rewards_json`, `created_at`

Areas, dungeons, items, and recipes can be described in JSON files loaded on startup.

