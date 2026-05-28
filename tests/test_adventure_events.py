from __future__ import annotations

import random

import pytest

from src.adventure import (
    PITY_BOOST_CAP,
    PITY_BOOST_PER_SEGMENT,
    RARE_EVENT_REWARDS,
    AdventureChoice,
    PendingAdventure,
    _apply_rare_event,
    _pick_encounter,
    _pick_rare_event,
    _pity_bonus,
    _resolve_combat_segment,
    _resolve_segment,
    _roll_drop,
    apply_adventure_combat_outcome,
    apply_adventure_choice,
    get_encounters_for_area,
    is_moral_choice_encounter,
    start_adventure_session,
)
from src.manuals import RARE_EVENT_META_KEYS
from src.combat.monsters import get_monster, load_monster_catalog
from src.combat.session import get_active_combat
from src.character import get_character_modifiers
from src.content import get_area, get_areas, load_all_content
from src.inventory import load_item_catalog
from src.models import Player
from tests.rng_helpers import (
    ScriptedRNG,
    collect_ids_over_seeds,
    randint_for_weighted_event,
    safe_adventure_segment_floats,
)


@pytest.fixture(autouse=True)
def load_content():
    import src.adventure as adventure_mod

    adventure_mod._encounters = None
    load_all_content()
    load_item_catalog()


def _all_rare_event_ids() -> set[str]:
    ids: set[str] = set()
    for area in get_areas().values():
        for event in area.rare_events:
            ids.add(event.id)
    return ids


def test_every_configured_rare_event_has_rewards():
    configured = _all_rare_event_ids()
    assert configured.issubset(set(RARE_EVENT_REWARDS.keys())), (
        configured - set(RARE_EVENT_REWARDS.keys())
    )


def test_every_encounter_has_valid_choices():
    import src.adventure as adventure_mod

    adventure_mod._encounters = None
    for area_id in get_areas():
        for encounter in get_encounters_for_area(area_id):
            assert encounter.prompt
            if encounter.encounter_type == "combat":
                assert encounter.monster_id
                continue
            assert len(encounter.choices) >= 2
            assert is_moral_choice_encounter(encounter), (
                f"{area_id}/{encounter.id} needs at least one merciful and one cruel path"
            )
            for choice in encounter.choices:
                assert choice.id
                assert choice.label
                assert choice.karma_delta != 0, (
                    f"{area_id}/{encounter.id}/{choice.id} must shift karma"
                )
                assert 0.0 <= choice.fail_chance <= 1.0


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_all_encounters_appear_over_many_starts(area_id: str):
    expected = {e.id for e in get_encounters_for_area(area_id)}

    def pick(seed_rng: random.Random) -> str:
        return _pick_encounter(seed_rng, area_id, 1).id

    seen = collect_ids_over_seeds(pick, range(300))
    assert seen == expected, f"missing encounters: {expected - seen}"


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_encounters_differ_between_seeds(area_id: str):
    """Adventure starts should not always pick the same encounter."""
    ids = [_pick_encounter(random.Random(seed), area_id, 1).id for seed in range(40)]
    assert len(set(ids)) >= 2, "expected at least two different encounters in 40 seeds"


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_all_rare_events_selected_by_weighted_roll(area_id: str):
    area = get_area(area_id)
    assert area is not None
    expected = {e.id for e in area.rare_events}

    def pick(seed_rng: random.Random) -> str:
        event = _pick_rare_event(seed_rng, area.rare_events)
        assert event is not None
        return event.id

    seen = collect_ids_over_seeds(pick, range(500))
    assert seen == expected, f"missing rare events in weighted picks: {expected - seen}"


@pytest.mark.parametrize(
    "area_id,event_id",
    [
        ("bamboo_grove", "hidden_herb_patch"),
        ("bamboo_grove", "wandering_elder"),
        ("bamboo_grove", "ancient_cache"),
        ("ashen_cliff", "ambush"),
        ("ashen_cliff", "abandoned_cart"),
        ("ashen_cliff", "ancient_cache"),
        ("moonwell_ruins", "hidden_moonwell"),
        ("moonwell_ruins", "inheritance_fragment"),
        ("moonwell_ruins", "ancient_cache"),
    ],
)
def test_each_rare_event_applies_rewards(session, player: Player, area_id: str, event_id: str):
    area = get_area(area_id)
    assert area is not None
    player.realm_index = max(player.realm_index, area.min_realm)
    session.commit()

    event = next(e for e in area.rare_events if e.id == event_id)
    drops: dict[str, int] = {}
    messages: list[str] = []
    stones_before = player.spirit_stones

    _apply_rare_event(session, player, event, area, drops, messages, rng=random.Random(1))
    session.commit()

    assert any(event.message in m for m in messages)
    rewards = RARE_EVENT_REWARDS[event_id]

    if rewards.get("effect"):
        mod = get_character_modifiers(session, player)
        assert str(rewards["effect"]) in mod.active_effects
    if "spirit_stones" in rewards:
        assert player.spirit_stones == stones_before + int(rewards["spirit_stones"])
    item_keys = [
        k
        for k in rewards
        if k not in RARE_EVENT_META_KEYS and k != "spirit_stones"
    ]
    for item_id in item_keys:
        assert drops.get(item_id, 0) >= int(rewards[item_id])
    if rewards.get("manual_pool"):
        assert any(k.startswith("manual_") for k in drops)


@pytest.mark.parametrize(
    "area_id,event_id",
    [
        ("bamboo_grove", "hidden_herb_patch"),
        ("bamboo_grove", "wandering_elder"),
        ("bamboo_grove", "ancient_cache"),
        ("ashen_cliff", "ambush"),
        ("ashen_cliff", "abandoned_cart"),
        ("ashen_cliff", "ancient_cache"),
        ("moonwell_ruins", "hidden_moonwell"),
        ("moonwell_ruins", "inheritance_fragment"),
        ("moonwell_ruins", "ancient_cache"),
    ],
)
def test_each_rare_event_triggers_during_adventure_segment(
    session, player: Player, area_id: str, event_id: str
):
    area = get_area(area_id)
    assert area is not None
    player.realm_index = max(player.realm_index, area.min_realm)
    session.commit()

    safe_choice = AdventureChoice("safe", "Play it safe", 0.15, 1.0, 0.0)
    state = {
        "drops": {},
        "messages": [],
        "rare_events": [],
        "segments_cleared": 0,
    }
    roll = randint_for_weighted_event(area.rare_events, event_id)
    rng = ScriptedRNG(
        floats=safe_adventure_segment_floats(trigger_rare=True),
        randint_queue=[1, roll],  # drop qty, then weighted rare-event pick
    )

    _, run_failed = _resolve_segment(
        session, player, area, "balanced", safe_choice, state, rng, allow_catastrophic=True
    )
    session.commit()

    assert run_failed is False
    assert event_id in state["rare_events"]


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_each_rare_event_triggers_in_live_segment_flow(session, player: Player, area_id: str):
    """Every rare event must fire at least once when the rare gate is forced open."""
    area = get_area(area_id)
    assert area is not None
    player.realm_index = max(player.realm_index, area.min_realm)
    session.commit()

    safe_choice = AdventureChoice("safe", "Play it safe", 0.15, 1.0, 0.0)
    seen: set[str] = set()

    for event in area.rare_events:
        state = {
            "drops": {},
            "messages": [],
            "rare_events": [],
            "segments_cleared": 0,
        }
        roll = randint_for_weighted_event(area.rare_events, event.id)
        rng = ScriptedRNG(
            floats=safe_adventure_segment_floats(trigger_rare=True),
            randint_queue=[1, roll],
        )
        _resolve_segment(session, player, area, "reckless", safe_choice, state, rng)
        seen.add(event.id)
        assert event.id in state["rare_events"]

    assert seen == {e.id for e in area.rare_events}


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
@pytest.mark.parametrize("encounter_index", [0, 1])
def test_each_encounter_can_start_interactive_run(session, player: Player, area_id: str, encounter_index: int):
    player.realm_index = max(player.realm_index, get_area(area_id).min_realm)
    session.commit()

    encounters = get_encounters_for_area(area_id)
    rng = ScriptedRNG(encounter_queue=[encounters[encounter_index]])
    pending, err = start_adventure_session(session, player, area_id, "balanced", rng=rng)
    session.commit()

    assert err is None
    assert pending is not None
    assert pending.prompt == encounters[encounter_index].prompt
    assert len(pending.choices) == len(encounters[encounter_index].choices)


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_each_choice_id_accepted_on_first_segment(session, player: Player, area_id: str):
    player.realm_index = max(player.realm_index, get_area(area_id).min_realm)
    session.commit()

    for encounter in get_encounters_for_area(area_id):
        for choice in encounter.choices:
            from src.adventure import abandon_adventure

            abandon_adventure(session, player.id)

            rng = ScriptedRNG(
                floats=safe_adventure_segment_floats() * 2,
                encounter_queue=[encounter, encounter],
                randint_queue=[],
            )
            pending, err = start_adventure_session(session, player, area_id, "balanced", rng=rng)
            assert err is None and pending is not None

            result, err = apply_adventure_choice(session, player, pending.active_id, choice.id, rng=rng)
            assert err is None, f"choice {choice.id} rejected on {encounter.id}"
            assert result is not None

            if isinstance(result, PendingAdventure):
                follow_up, err = apply_adventure_choice(
                    session,
                    player,
                    result.active_id,
                    result.choices[0].id,
                    rng=rng,
                )
                assert err is None
                assert follow_up is not None

            session.commit()


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_every_area_drop_item_can_roll_from_table(area_id: str):
    area = get_area(area_id)
    assert area is not None
    expected = {d.item_id for d in area.drops}

    def pick_item(seed_rng: random.Random) -> str:
        rolled = _roll_drop(seed_rng, area.drops, 1.0, 0.0, 0.0, player_realm_index=2, area_min_realm=area.min_realm)
        assert rolled is not None
        return rolled[0]

    seen = collect_ids_over_seeds(pick_item, range(600))
    assert seen == expected, f"{area_id} drops never rolled: {expected - seen}"


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_each_area_has_combat_encounter(area_id: str):
    combat = [e for e in get_encounters_for_area(area_id) if e.encounter_type == "combat"]
    assert len(combat) >= 2
    for encounter in combat:
        assert encounter.monster_id
        assert get_monster(encounter.monster_id) is not None


def test_all_monsters_referenced_in_encounters_exist():
    for area_id in get_areas():
        for encounter in get_encounters_for_area(area_id):
            if encounter.encounter_type == "combat" and encounter.monster_id:
                assert encounter.monster_id in load_monster_catalog()


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_combat_encounter_starts_active_combat(session, player: Player, area_id: str):
    player.realm_index = max(player.realm_index, get_area(area_id).min_realm)
    session.commit()
    combat_encounters = [e for e in get_encounters_for_area(area_id) if e.encounter_type == "combat"]
    rng = ScriptedRNG(encounter_queue=[combat_encounters[0]])
    pending, err = start_adventure_session(session, player, area_id, "balanced", rng=rng)
    session.commit()
    assert err is None
    assert pending is not None
    assert pending.encounter_type == "combat"
    assert pending.combat_id is not None
    assert get_active_combat(session, player.id) is not None


def test_pity_increases_rare_gate(session, player: Player):
    area = get_area("bamboo_grove")
    assert area is not None
    state = {"segments_since_rare": 4, "messages": [], "drops": {}, "rare_events": []}
    assert _pity_bonus(state) == pytest.approx(min(4 * PITY_BOOST_PER_SEGMENT, PITY_BOOST_CAP))

    safe_choice = AdventureChoice("safe", "Play it safe", 0.15, 1.0, 0.0)
    event = area.rare_events[0]
    roll = randint_for_weighted_event(area.rare_events, event.id)
    rng = ScriptedRNG(
        floats=safe_adventure_segment_floats(trigger_rare=True),
        randint_queue=[1, roll],
    )
    _resolve_segment(session, player, area, "balanced", safe_choice, state, rng)
    assert state["segments_since_rare"] == 0
    assert event.id in state["rare_events"]


def test_pity_counter_increments_without_rare(session, player: Player):
    area = get_area("bamboo_grove")
    assert area is not None
    state = {
        "drops": {},
        "messages": [],
        "rare_events": [],
        "segments_cleared": 0,
        "segments_since_rare": 2,
    }
    safe_choice = AdventureChoice("safe", "Play it safe", 0.15, 1.0, 0.0)
    rng = ScriptedRNG(floats=safe_adventure_segment_floats(trigger_rare=False))
    _resolve_segment(session, player, area, "balanced", safe_choice, state, rng)
    assert state["segments_since_rare"] == 3


def test_choice_shifts_karma_without_zero_neutral(session, player: Player):
    import src.adventure as adventure_mod

    adventure_mod._encounters = None
    encounter = next(
        e for e in get_encounters_for_area("bamboo_grove") if e.id == "injured_elder"
    )
    help_choice = next(c for c in encounter.choices if c.id == "help")
    rob_choice = next(c for c in encounter.choices if c.id == "rob")

    player.realm_index = 0
    session.commit()
    area = get_area("bamboo_grove")
    state = {"drops": {}, "messages": [], "rare_events": [], "segments_cleared": 0}
    rng = ScriptedRNG(floats=safe_adventure_segment_floats())

    before = player.karma
    _resolve_segment(session, player, area, "balanced", help_choice, state, rng)
    assert player.karma > before

    player.karma = before
    state = {"drops": {}, "messages": [], "rare_events": [], "segments_cleared": 0}
    _resolve_segment(session, player, area, "balanced", rob_choice, state, rng)
    assert player.karma < before


def test_combat_victory_shifts_karma(session, player: Player):
    area = get_area("bamboo_grove")
    state = {"drops": {}, "messages": [], "rare_events": [], "segments_cleared": 0}
    rng = ScriptedRNG(floats=safe_adventure_segment_floats(trigger_rare=False))
    before = player.karma
    _resolve_combat_segment(session, player, area, "reckless", state, rng, victory=True)
    assert player.karma < before
    assert state.get("karma_touched")


@pytest.mark.parametrize("area_id", ["bamboo_grove", "ashen_cliff", "moonwell_ruins"])
def test_combat_victory_advances_adventure(session, player: Player, area_id: str):
    player.realm_index = max(player.realm_index, get_area(area_id).min_realm)
    session.commit()
    combat_encounters = [e for e in get_encounters_for_area(area_id) if e.encounter_type == "combat"]
    rng = ScriptedRNG(
        encounter_queue=[combat_encounters[0], combat_encounters[0]],
        floats=safe_adventure_segment_floats() * 2,
        randint_queue=[],
    )
    pending, err = start_adventure_session(session, player, area_id, "balanced", rng=rng)
    assert err is None and pending is not None
    result, err = apply_adventure_combat_outcome(
        session, player, pending.active_id, victory=True, rng=rng
    )
    session.commit()
    assert err is None
    assert result is not None
