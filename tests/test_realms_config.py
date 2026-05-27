from __future__ import annotations

from src.realms import (
    REALMS,
    SUBSTAGES,
    get_realm_name,
    invalidate_realms_cache,
    qi_cap,
    realm_count,
)


def test_realms_loaded_from_config():
    invalidate_realms_cache()
    assert realm_count() == 10
    assert REALMS[0] == "Mortal"
    assert REALMS[-1] == "Immortal Monarch"
    assert SUBSTAGES == ["early", "mid", "late"]
    assert get_realm_name(99) == "Immortal Monarch"


def test_qi_cap_uses_config():
    assert qi_cap(0, 0) == 100
    assert qi_cap(0, 1) == 150
    assert qi_cap(0, 2) == 220
    assert qi_cap(1, 0) == 250
