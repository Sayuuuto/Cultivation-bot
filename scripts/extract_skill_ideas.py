from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = Path.home() / "Downloads" / "allskills.json"
DEFAULT_OUTPUT = REPO_ROOT / "config" / "skill_idea_mapping.json"
SECTS_PATH = REPO_ROOT / "config" / "sects.json"

ALIGNMENTS = {"R": "righteous", "N": "neutral", "D": "demonic"}
TIER_REALM = {"mortal": 0, "earth": 1, "sky": 3, "heaven": 3, "nascent": 4}
SOURCE_PREFIXES = {
    "SHOP": "shop",
    "CRAFT": "craft",
    "ADV": "adventure",
    "BT": "breakthrough",
    "HUNT": "hunt",
    "DUNGEON": "dungeon",
    "SECT": "sect",
}
SECT_ALIASES = {
    "BLOOD": "blood_lotus",
    "BLOOD_LOTUS": "blood_lotus",
    "TANG": "tang",
    "WUDANG": "wudang",
    "SHAOLIN": "shaolin",
    "MOUNT_HUA": "mount_hua",
    "HUA": "mount_hua",
    "KUNLUN": "kunlun",
    "IMPERIAL": "imperial_guard",
    "IMPERIAL_GUARD": "imperial_guard",
    "SHADOW": "shadow_pavilion",
    "SHADOW_PAVILION": "shadow_pavilion",
}


def _slug(value: str) -> str:
    return value.strip().lower().replace("'", "").replace("-", "_").replace(" ", "_")


def _load_existing_sects(path: Path = SECTS_PATH) -> set[str]:
    with path.open(encoding="utf-8") as f:
        return set(json.load(f))


def normalize_source(code: str, existing_sects: set[str]) -> dict[str, str]:
    raw = code.strip()
    prefix, _, suffix = raw.partition("-")
    prefix = prefix.upper()
    suffix_key = suffix.upper().replace("-", "_").replace(" ", "_")
    taxonomy = SOURCE_PREFIXES.get(prefix, "backlog")
    result = {"raw": raw, "taxonomy": taxonomy}
    if taxonomy == "sect":
        sect_id = SECT_ALIASES.get(suffix_key, _slug(suffix))
        if sect_id in existing_sects:
            result["sect_id"] = sect_id
        else:
            result["taxonomy"] = "backlog"
            result["reason"] = "unknown_sect"
    elif taxonomy == "shop":
        result["channel"] = "direct" if suffix.upper() in {"M", "E"} else _slug(suffix or "shop")
    elif taxonomy in {"craft", "adventure", "breakthrough"}:
        result["pool"] = _slug(suffix or taxonomy)
    elif taxonomy == "hunt":
        result["target"] = suffix
    elif taxonomy == "backlog":
        result["reason"] = "unknown_source"
    return result


def normalize_skill(raw: dict[str, Any], *, slot_type: str, existing_sects: set[str]) -> dict[str, Any]:
    tier = str(raw.get("tier", "mortal")).lower()
    sources = [normalize_source(str(code), existing_sects) for code in raw.get("obtain", [])]
    return {
        "technique_id": _slug(str(raw.get("id") or raw.get("name", ""))),
        "name": str(raw.get("name", "")),
        "slot_type": slot_type,
        "category": str(raw.get("category", "passive" if slot_type == "passive" else "martial")),
        "role": str(raw.get("role", "finisher")),
        "tier": tier,
        "min_realm": TIER_REALM.get(tier, 0),
        "rarity": str(raw.get("rarity", "common")).lower(),
        "alignment": ALIGNMENTS.get(str(raw.get("alignment", "N")).upper(), "neutral"),
        "description": str(raw.get("technical_description", "")),
        "manual_item_id": f"manual_{_slug(str(raw.get('id') or raw.get('name', '')))}",
        "sources": sources,
        "backlog_reasons": sorted({src["reason"] for src in sources if src.get("taxonomy") == "backlog" and "reason" in src}),
    }


def extract_skill_ideas(input_path: Path = DEFAULT_INPUT) -> dict[str, Any]:
    with input_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    existing_sects = _load_existing_sects()
    ideas = []
    for entry in raw.get("actives", []):
        ideas.append(normalize_skill(entry, slot_type="active", existing_sects=existing_sects))
    for entry in raw.get("passives", []):
        ideas.append(normalize_skill(entry, slot_type="passive", existing_sects=existing_sects))
    taxonomy_counts: dict[str, int] = {}
    for idea in ideas:
        for source in idea["sources"]:
            taxonomy = source["taxonomy"]
            taxonomy_counts[taxonomy] = taxonomy_counts.get(taxonomy, 0) + 1
    return {
        "schema_version": "1.0",
        "source_file": str(input_path),
        "idea_count": len(ideas),
        "source_taxonomy_counts": taxonomy_counts,
        "sect_aliases": SECT_ALIASES,
        "ideas": ideas,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize draft skill ideas into repo technique taxonomy.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = extract_skill_ideas(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {payload['idea_count']} normalized skill ideas to {args.output}")


if __name__ == "__main__":
    main()
