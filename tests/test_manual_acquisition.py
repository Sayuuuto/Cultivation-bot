from __future__ import annotations

import json
import random

import pytest

from src.adventure import _apply_rare_event
from src.combat.catalog import load_technique_catalog
from src.combat.learn import learn_technique_from_manual
from src.combat.loadout import get_learned_technique_ids, learn_technique
from src.content import RareEventDef, get_area, load_all_content
from src.dungeon import run_dungeon
from src.hunt import run_hunt
from src.inventory import add_item, get_item_quantity, load_item_catalog
from src.game import cultivate
from src.manuals import (
    FRAGMENT_ITEM_ID,
    MANUAL_CRAFT_INPUTS,
    craft_manual_from_fragments,
    grant_manual_drop,
    had_weekly_dungeon_manual,
    load_manual_pools,
    normalize_manual_drops,
    pick_manual_from_pool,
    roll_shop_unidentified_manual,
)
from src.models import DungeonRun
from src.shop import buy_from_shop, load_shop_catalog


@pytest.fixture(autouse=True)
def load_content():
    load_all_content()
    load_item_catalog()
    load_manual_pools()


def test_manual_pools_load():
    pools = load_manual_pools()
    assert "elder_mortal" in pools
    assert "dungeon_earth" in pools
    assert len(pools["elder_mortal"]) >= 5


def test_pick_manual_from_pool(session, player):
    manual_id = pick_manual_from_pool(
        "elder_mortal", random.Random(42), session=session, player_id=player.id
    )
    assert manual_id is not None
    assert manual_id.startswith("manual_")


def test_grant_manual_converts_duplicate_to_fragments(session, player):
    learn_technique(session, player.id, "ember_palm")
    session.commit()

    drops: dict[str, int] = {}
    msg = grant_manual_drop(session, player.id, "manual_ember_palm", drops)
    assert FRAGMENT_ITEM_ID in drops
    assert drops[FRAGMENT_ITEM_ID] == 2
    assert "crumbles" in msg.lower()


def test_normalize_manual_drops(session, player):
    learn_technique(session, player.id, "swift_slash")
    session.commit()

    normalized = normalize_manual_drops(
        session, player.id, {"manual_swift_slash": 1, "minor_beast_core": 2}
    )
    assert normalized["minor_beast_core"] == 2
    assert normalized[FRAGMENT_ITEM_ID] == 2
    assert "manual_swift_slash" not in normalized


def test_hunt_can_drop_fragments_and_manuals(session, player):
    player.realm_index = 2
    player.substage = 2
    session.commit()

    found_fragment = False
    found_manual = False
    for seed in range(500):
        res = run_hunt(session, player, "bamboo_grove", rng=random.Random(seed))
        if not res.success:
            continue
        if FRAGMENT_ITEM_ID in res.drops:
            found_fragment = True
        if any(k.startswith("manual_") for k in res.drops):
            found_manual = True
        if found_fragment and found_manual:
            break

    assert found_fragment
    assert found_manual


def test_wandering_elder_grants_effect_and_manual(session, player):
    area = get_area("bamboo_grove")
    assert area is not None
    event = RareEventDef(id="wandering_elder", weight=1, message="An elder nods.")
    drops: dict[str, int] = {}
    messages: list[str] = []

    _apply_rare_event(session, player, event, area, drops, messages, rng=random.Random(1))
    session.commit()

    from src.character import get_character_modifiers

    mod = get_character_modifiers(session, player)
    assert "qi_gathering" in mod.active_effects
    assert any(k.startswith("manual_") for k in drops)


def test_inheritance_fragment_grants_pill_and_manual(session, player):
    area = get_area("moonwell_ruins")
    assert area is not None
    player.realm_index = 2
    session.commit()

    event = RareEventDef(id="inheritance_fragment", weight=1, message="A fragment pulses.")
    drops: dict[str, int] = {}
    messages: list[str] = []

    _apply_rare_event(session, player, event, area, drops, messages, rng=random.Random(3))
    session.commit()

    assert drops.get("root_reforging_pill", 0) >= 1
    assert any(k.startswith("manual_") for k in drops)


def test_craft_manual_requires_materials(session, player):
    add_item(session, player.id, "technique_fragment", 3)
    session.commit()
    res = craft_manual_from_fragments(session, player, rng=random.Random(1))
    assert res.success is False
    assert "bind a technique manual" in res.message.lower()
    assert "blank scroll" in res.message.lower()


def test_craft_manual_binds_manual(session, player):
    for item_id, qty in MANUAL_CRAFT_INPUTS.items():
        add_item(session, player.id, item_id, qty)
    session.commit()

    res = craft_manual_from_fragments(session, player, rng=random.Random(5))
    session.commit()
    assert res.success is True
    assert len(res.crafted) == 1
    manual_id = next(iter(res.crafted))
    assert manual_id.startswith("manual_")
    assert get_item_quantity(session, player.id, manual_id) == 1


def test_learn_from_manual_end_to_end(session, player):
    add_item(session, player.id, "manual_ember_palm", 1)
    session.commit()

    ok, msg = learn_technique_from_manual(session, player.id, "manual_ember_palm")
    session.commit()
    assert ok is True
    assert "ember palm" in msg.lower()
    assert "ember_palm" in get_learned_technique_ids(session, player.id)
    assert get_item_quantity(session, player.id, "manual_ember_palm") == 0


def test_shop_starter_manual(session, player):
    player.spirit_stones = 100
    session.commit()

    ok, _ = buy_from_shop(
        session, player, "shop_manual_swift_slash", 1, rng=random.Random(1)
    )
    session.commit()
    assert ok is True
    assert get_item_quantity(session, player.id, "manual_swift_slash") == 1
    assert player.spirit_stones == 55


def test_shop_unidentified_scroll_grants_manual(session, player):
    player.spirit_stones = 200
    session.commit()

    ok, _ = buy_from_shop(
        session, player, "shop_unidentified_scroll", 1, rng=random.Random(9)
    )
    session.commit()
    assert ok is True
    manual_ids = [
        tech.manual_item_id
        for tech in load_technique_catalog().values()
        if tech.manual_item_id
    ]
    manual_qty = sum(get_item_quantity(session, player.id, item_id) for item_id in manual_ids)
    assert manual_qty >= 1


def test_roll_shop_unidentified_manual(session, player):
    manual_id, message = roll_shop_unidentified_manual(session, player, random.Random(2))
    session.commit()
    assert manual_id is not None
    assert manual_id.startswith("manual_")
    assert get_item_quantity(session, player.id, manual_id) == 1
    assert "scroll" in message.lower() or "obtained" in message.lower()


def test_weekly_dungeon_manual_once_per_week(session, player):
    player.realm_index = 1
    add_item(session, player.id, "blackwind_key", 100)
    session.commit()

    winning_seed = None
    result = None
    for seed in range(500):
        add_item(session, player.id, "blackwind_key", 1)
        session.commit()
        result = run_dungeon(session, player, "blackwind", rng=random.Random(seed))
        session.commit()
        if result.success:
            winning_seed = seed
            break

    assert winning_seed is not None, "could not find a dungeon boss win seed"
    assert any(k.startswith("manual_") for k in result.drops)

    add_item(session, player.id, "blackwind_key", 1)
    session.commit()
    run_dungeon(session, player, "blackwind", rng=random.Random(winning_seed))
    session.commit()

    weekly_flags = 0
    for run in session.query(DungeonRun).filter_by(player_id=player.id, dungeon_id="blackwind").all():
        payload = json.loads(run.rewards_json)
        if payload.get("weekly_manual"):
            weekly_flags += 1
    assert weekly_flags == 1
    assert had_weekly_dungeon_manual(session, player.id, "blackwind")


def test_cultivate_events_can_drop_fragment(session, player, cfg):
    found = False
    for seed in range(2000):
        res = cultivate(
            player,
            None,
            cfg,
            rng=random.Random(seed),
            session=session,
            player_id=player.id,
        )
        drops = res.bonus_drops or {}
        if res.event_id == "scripture_whisper" or drops.get("technique_fragment"):
            found = True
            break
    assert found
