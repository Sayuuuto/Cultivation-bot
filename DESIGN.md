# Cultivation Discord Bot (MVP) - Design

## High-level goal
Ship ASAP a serious xianxia-themed cultivation game bot with casual pacing: players cultivate toward immortality, with early PvP elements and a minimal sect (world) system.

## MVP scope (what players can do)
1. `/start` to create a cultivator
2. `/profile` to view realm, Qi, spirit stones, stamina, and streak
3. `/cultivate` plus a “Cultivate” button from profile
4. Stamina/energy regenerates passively; cultivate consumes stamina
5. Realm progression via `/breakthrough` plus a button on profile
6. `/daily` once per UTC day for spirit stones (with streak bonuses)
7. `/leaderboard` (server-only) for realm/Qi
8. Early PvP: `/duel` with basic matchmaking rules + a cooldown
9. Minimal **clan** system (player guilds):
   - `/clan-create`
   - `/clan-join`
   - `/clan-leave`
   - `/clan` to view clan info
10. **Martial sects** (fixed in-world factions): `/sect-list`, `/sect-join`, `/sect-leave`, `/sect`

## Non-goals for MVP
- No shop in MVP.
- No crafting/inventory system.
- No complex multi-stage PvP combat simulation.
- No paid mechanics (pay-to-cultivate faster) in MVP; reserved as a future extension.

## Tone & flavor
- Serious xianxia tone, second-person narrative voice (“You…”, “Your…”).
- “Real terms” (qi, dantian, realm names as standard novel tiers).
- Dry wit is allowed sparingly.
- All-ages safe.

## Monetization plan (explicitly deferred)
- MVP is free: no payments, no shop.
- Future: pay-to-cultivate faster via additional cooldown reduction or reward multipliers.

## Design decisions (your chosen items)
- Player fantasy: cultivate to be immortal.
- Server target: friends-sized but scalable.
- Growth focus: personal progression + clans + martial sects.
- PvP: early PvP elements; expanded later.
- Narrative voice: second person.
- Realm tiers: standard novel tiers (see `REALMS` below).
- Fail states: setbacks only (no deaths/permadeath).
- Moral alignment: **karma** (−100…+100), earned through adventure choices — not chosen at `/start`.
- Spirit root/aptitude: rerollable.
- Character reset: allowed.
- Inactive players: frozen forever (no offline progression beyond partial cap).
- Public stats: yes.
- Explicit `/start`.
- `/cultivate` via both command and button.
- Cultivate cooldown: 15 minutes.
- Daily reset: UTC.
- Offline progress: partial cap.
- Session target: ~15 minutes/day for typical play.
- Catch-up: streak bonuses.
- Energy/stamina separate from cooldown.
- Action limits per day: primarily driven by cooldown (not hard daily action caps).
- Failure feel: forgiving.
- Breakthrough pacing: every few days (emergent from Qi caps + stamina/cooldowns).
- Max realms at launch: 10 named realms.
- Sub-stages: early/mid/late within each realm.

## Defaults for everything else (MVP)
- Language: English only, with i18n structure from day one (i18n keys; English strings initially).
- Bot commands: Slash commands + buttons (discord.py app_commands).
- Persistence: SQLite (MVP) via SQLAlchemy.
- Migrations: `create_all()` on startup for MVP (no Alembic yet).
- Realm/Qi model:
  - Each realm contains 3 sub-stages: early/mid/late.
  - When Qi reaches the realm+substage cap, breakthrough attempts may advance.
  - Success/failure:
    - Success advances to next sub-stage; after late -> next realm early.
    - Failure applies a setback (Qi loss) without death.
- Offline partial progress:
  - When a player performs an action after being inactive, they gain some Qi based on elapsed time, capped to a small amount.
  - Offline Qi never exceeds what they could reasonably get from multiple normal cultivates.
- Energy/stamina:
  - Energy regenerates passively over time.
  - Cultivation consumes energy, influencing Qi gain multiplier.

## Realm configuration (10 named realms)
Realms (index 0..9):
1. Mortal
2. Qi Refining
3. Foundation Establishment
4. Core Formation
5. Nascent Soul
6. Spirit Severing
7. Void Refinement
8. Immortal Ascension
9. Heavenly Transcendence
10. Immortal Monarch

Sub-stages: `early`, `mid`, `late`.

## Karma (affects breakthrough modestly)
- Earned through **`/adventure`** moral choices (−100…+100), not chosen at `/start`.
- High karma (Righteous tier):
  - Slightly higher breakthrough success, smaller setback.
- Low karma (Demonic tier):
  - Slightly higher breakthrough success, larger setback (more risk/reward).
- Neutral karma (~0):
  - Baseline success and setback.

## Stamina and offline defaults (MVP constants)
- Stamina max: 100
- Stamina regen: 10 per hour (passive, computed on next interaction)
- Stamina cost per cultivate: 8
- Cultivate energy multiplier: 0.7..1.3 based on current stamina fraction
- Offline Qi:
  - Gains up to `OFFLINE_CAP_MINUTES` worth of Qi (default 120 minutes).
  - Offline Qi rate is proportional to the player’s current realm.

## PvP defaults (early)
- PvP model: Duel with auto-resolve (no complex combat).
- Preconditions:
  - Both players exist, are not in cooldown, and are not the same user.
  - Optional: disallow duels during breakthrough attempt if desired later.
- Strength:
  - Strength is computed from realm index + sub-stage + Qi ratio.
- Stakes:
  - Forgiving outcome: reward/loss is primarily spirit stones and a small Qi transfer.
- PvP cooldown: separate from cultivate cooldown.

## Clan system defaults (MVP)
- A **clan** is a player-created guild scoped to one Discord server.
- Players can create/join/leave a clan.
- Clan stats are derived from membership and accumulated contribution.
- Contribution increases when members cultivate (percentage of Qi gain).

## Martial sect system (scaffold)
- **Sects** are fixed in-world orders (Wudang, Shaolin, etc.) in `config/sects.json`.
- Join requirements: karma tier, realm, and invitations for secret sects.
- Sect merit, daily tasks, and sect shop scaffolded for upcoming phases.

## i18n approach
- Use message keys internally (e.g. `msg.profile.title`).
- English strings only in MVP but structure is ready for future locales.

---

## Phase 1 PvE & Crafting (implementation checklist)

Detailed design: see `PHASE1_PVE_DESIGN.md`. High-level implementation steps:

1. **Inventory & items**
   - Add `inventory_items` table in models.
   - Implement inventory service in a new module (e.g. `src/inventory.py`).
   - Add `/inventory` command in `bot.py`.

2. **Config for areas, items, recipes, dungeons**
   - Create config files for:
     - Areas (drop tables, rare events).
     - Items (materials, pills, keys, affix stones).
     - Pill/Key recipes.
   - Add a loader module (e.g. `src/content.py`) to read and cache configs.

3. **Adventure command**
   - Implement `/adventure area:<name> stance:<cautious|balanced|reckless>`.
   - Resolve 2 segments, using config-driven tables from `PHASE1_PVE_DESIGN.md`.
   - Award materials into `inventory_items`.

4. **Crafting**
   - Implement a generic `craft_item(player, recipe_id, amount)` in a new `crafting` module.
   - Wire `/craft pill:<name>` and `/craft key dungeon:blackwind` to recipes.
   - Handle success/failure, side-effects (pills), and byproduct (`Pill Ash`).

5. **Dungeon**
   - Implement `/dungeon start name:blackwind mode:solo`.
   - Consume `Blackwind Key`, simulate 3 steps + boss, and add rewards.
   - Record results in `dungeon_runs`.

6. **Equipment & affixes**
   - Add `player_equipment` and a minimal `equipment` service.
   - Implement `/equip` and `/loadout` commands.
   - Integrate affix effects into existing game logic (power/defense/luck).

7. **Rare events**
   - Implement rare event rolls inside adventure resolution.
   - Use effects defined in `PHASE1_PVE_DESIGN.md` (herb patch, cache, elder, shrine, inheritance).


