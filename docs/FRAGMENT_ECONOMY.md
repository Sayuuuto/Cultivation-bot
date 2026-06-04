# Technique Fragment Economy

Earn/spend tuning reference for technique fragments. Not shown to players.

Player-facing spend paths are documented in the tutorial, `/item Technique Fragment`, and `config/player_guides/combat_progression.json`.

## Earn Paths (primary)

| Source | Typical Rate | Notes |
|--------|-------------|-------|
| `/cultivate` enlightenment | ~4% per roll | Single fragment |
| `/breakthrough` success | ~15% | Neutral pool companion |
| Demonic breakthrough fail | ~5% | Fail-only demonic tier |
| Duplicate manual | 2× fragment | `grant_manual_drop()` / `normalize_manual_drops()` |
| Hunt / adventure / dungeon | Area-dependent | See `config/hunt_targets.json`, `config/areas.json` |

## Spend Paths (launch minimum)

| Sink | Cost Driver | Config / Code |
|------|-------------|---------------|
| `/craft manual` | 3 fragments + blank scroll + spirit ink | `MANUAL_CRAFT_INPUTS` in `src/manuals.py` |
| `/upgrade-technique` | Category material + stones; fragments from rank 3+ | `config/technique_upgrade.json`, `src/combat/ranks.py` |
| Passive rank tempering | Uses `technique_fragment` as category material | `category_materials.passive` |

## Target Balance (realm bands 0–2)

Tune so a dedicated player can rank **at least two core techniques to rank 5** within ~7 days of intended play.

- **Mortal (0):** hunt bamboo + cultivate fragments; shop staples for scroll/ink.
- **Qi Refining (1):** elite hunts + dungeon bonus drops for `spirit_iron_shard`, `minor_beast_core`.
- **Foundation (2):** add `ember_moss`, `moonlotus`, `ancient_dust` from higher areas and cooperative dungeons.

## Telemetry

```sh
python scripts/balance_simulator.py --economy
# Appends fragment sink/source notes to reports/balance_report.json
```
