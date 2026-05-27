from __future__ import annotations

import json
from pathlib import Path

from src.library_info import _collect_pool_references


def test_hunt_elite_pools_are_wired():
    pools_path = Path(__file__).resolve().parent.parent / "config" / "manual_pools.json"
    pools = json.loads(pools_path.read_text(encoding="utf-8"))
    hunt_pools = {k for k in pools if k.startswith("hunt_") and k.endswith("_elite")}
    wired = _collect_pool_references()
    assert hunt_pools.issubset(wired), hunt_pools - wired
