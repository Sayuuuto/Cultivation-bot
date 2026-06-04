"""Asset schema validation and referential integrity tests.

These tests verify that all JSON game-data files match their Pydantic schemas
and that cross-file references (item IDs, technique IDs, status IDs, etc.) are valid.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.schemas import (
    run_validation,
    validate_techniques,
    validate_monsters,
    validate_areas,
    validate_recipes,
    validate_dungeons,
    validate_shop,
    validate_gear_catalog,
    validate_hunt_targets,
    validate_gather_nodes,
    validate_equipment_forge,
    validate_affixes,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_json(name: str) -> dict:
    with (CONFIG_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


class TestSchemaValidation:
    """Every JSON config file validates against its Pydantic schema."""

    def test_all_configs_pass_schema_validation(self):
        results = run_validation(raise_on_error=False)
        failures = {k: v for k, v in results.items() if v}
        assert not failures, f"Schema validation failures:\n{failures}"

    def test_items_schema(self):
        raw = _load_json("items.json")
        for item_id, data in raw.items():
            assert "name" in data, f"{item_id} missing name"
            assert "category" in data, f"{item_id} missing category"
            assert data["category"] in (
                "material", "pill", "equipment", "manual", "key", "special", "supply"
            ), f"{item_id} invalid category '{data['category']}'"

    def test_realms_schema(self):
        raw = _load_json("realms.json")
        assert len(raw["realms"]) == 10
        for r in raw["realms"]:
            assert r["base_qi_cap"] > 0
            assert r["technique_rank_cap"] <= 10
            b = r["technique_load_budget"]
            assert b["total"] >= b["active"], f"total({b['total']}) < active({b['active']})"
            assert b["total"] >= b["passive"], f"total({b['total']}) < passive({b['passive']})"

    def test_combat_rules_schema(self):
        raw = _load_json("combat_rules.json")
        assert raw["max_turns"] >= raw["auto_finish_after_turn"]
        known_statuses = {"burn", "bleed", "poison", "stun", "fear", "seal"}
        for sid in raw["statuses"]:
            assert sid in known_statuses, f"Unknown status '{sid}'"


class TestReferentialIntegrity:
    """Cross-file references are valid."""

    def _get_items(self) -> set[str]:
        return set(_load_json("items.json").keys())

    def test_technique_manual_items_exist_in_items(self):
        raw = _load_json("techniques.json")
        items = self._get_items()
        for tid, data in raw.items():
            mid = data.get("manual_item_id")
            if mid:
                assert mid in items, f"{tid}: manual_item_id '{mid}' not in items.json"

    def test_technique_status_ids_exist(self):
        raw = _load_json("techniques.json")
        rules = _load_json("combat_rules.json")
        statuses = set(rules.get("statuses", {}))
        for tid, data in raw.items():
            sid = data.get("status_id")
            if sid and sid not in statuses:
                assert False, f"{tid}: status_id '{sid}' not in combat_rules.json statuses"

    def test_monster_drops_exist_in_items(self):
        raw = _load_json("monsters.json")
        items = self._get_items()
        for mid, data in raw.items():
            for drop in data.get("drops", []):
                assert drop["item_id"] in items, f"{mid}: drop '{drop['item_id']}' not in items.json"

    def test_monster_traits_are_known(self):
        raw = _load_json("monsters.json")
        known = {"bleed_immune", "seal_on_hit", "high_stun_chance", "cleanse_every_3_turns"}
        for mid, data in raw.items():
            for trait in data.get("traits", []):
                assert trait in known, f"{mid}: unknown trait '{trait}'"

    def test_area_drops_exist_in_items(self):
        raw = _load_json("areas.json")
        items = self._get_items()
        for aid, data in raw.items():
            for drop in data.get("drops", []):
                assert drop["item_id"] in items, f"{aid}: drop '{drop['item_id']}' not in items.json"

    def test_recipe_inputs_and_outputs_exist(self):
        raw = _load_json("recipes.json")
        items = self._get_items()
        for rid, data in raw.items():
            for input_id in data.get("inputs", {}):
                assert input_id in items, f"{rid}: input '{input_id}' not in items.json"
            output = data.get("output_item_id")
            assert output in items, f"{rid}: output '{output}' not in items.json"

    def test_dungeon_drops_exist_in_items(self):
        raw = _load_json("dungeons.json")
        items = self._get_items()
        for did, data in raw.items():
            for drop in data.get("guaranteed_drops", []) + data.get("bonus_drops", []):
                assert drop["item_id"] in items, f"{did}: drop '{drop['item_id']}' not in items.json"
            assert data["key_item_id"] in items, f"{did}: key '{data['key_item_id']}' not in items.json"

    def test_shop_listings_exist_in_items(self):
        raw = _load_json("shop.json")
        items = self._get_items()
        for lid, data in raw.items():
            iid = data.get("item_id")
            if iid and iid not in items:
                assert False, f"{lid}: item_id '{iid}' not in items.json"

    def test_forge_items_exist(self):
        raw = _load_json("equipment_forge.json")
        items = self._get_items()
        for sid, data in raw.items():
            assert data["item_id"] in items, f"{sid}: item_id '{data['item_id']}' not in items.json"
            for input_id in data.get("inputs", {}):
                assert input_id in items, f"{sid}: input '{input_id}' not in items.json"

    def test_gear_catalog_items_exist(self):
        raw = _load_json("gear_catalog.json")
        items = self._get_items()
        for i, entry in enumerate(raw):
            assert entry["item_id"] in items, f"gear_catalog.json[{i}]: item_id '{entry['item_id']}' not in items.json"


class TestTechniqueNormalization:
    """Technique data is consistently structured."""

    def test_all_active_techniques_have_effects(self):
        raw = _load_json("techniques.json")
        for tid, data in raw.items():
            if data.get("slot_type") == "active":
                assert "effects" in data and len(data["effects"]) > 0, \
                    f"{tid}: active technique missing effects array"

    def test_no_legacy_passive_fields(self):
        raw = _load_json("techniques.json")
        legacy = {"passive_on_bleed", "passive_burn_bonus", "passive_crit_bonus"}
        for tid, data in raw.items():
            for field in legacy:
                assert field not in data, f"{tid}: legacy field '{field}' still present"

    def test_all_passive_techniques_have_passive_triggers(self):
        raw = _load_json("techniques.json")
        for tid, data in raw.items():
            if data.get("slot_type") == "passive":
                assert "passive_triggers" in data and len(data["passive_triggers"]) > 0, \
                    f"{tid}: passive technique missing passive_triggers"

    def test_no_duplicate_manual_item_ids(self):
        raw = _load_json("techniques.json")
        manuals = {}
        for tid, data in raw.items():
            mid = data.get("manual_item_id")
            if mid:
                assert mid not in manuals, f"manual_item_id '{mid}' used by both '{manuals[mid]}' and '{tid}'"
                manuals[mid] = tid

    def test_technique_rarities_are_valid(self):
        raw = _load_json("techniques.json")
        valid = {"common", "uncommon", "rare", "legendary"}
        for tid, data in raw.items():
            r = data.get("rarity", "common")
            assert r in valid, f"{tid}: invalid rarity '{r}'"
