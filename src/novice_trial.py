from __future__ import annotations

import json
import random
from pathlib import Path

from sqlalchemy.orm import Session

from .adventure import AdventureChoice, AdventureEncounter
from .inventory import add_item
from .manuals import FRAGMENT_ITEM_ID, roll_manual_pool_reward
from .models import Player

TRIAL_COMPLETE_STEP = 6
NOVICE_MORTAL_EARLY_CAP = 60
NOVICE_CULTIVATE_BOOST_STEPS = 3
NOVICE_CULTIVATE_BOOST_MULT = 1.5

ORIGIN_GIFTS_PATH = Path(__file__).resolve().parent.parent / "config" / "origin_starter_gifts.json"
_gifts_cache: dict | None = None

TRIAL_STEPS: list[tuple[str, str]] = [
    ("daily", "Claim **`/daily`** stipend"),
    ("cultivate", "Cultivate qi once (`/cultivate` or profile button)"),
    ("hunt", "Win a **`/hunt`** in Whispering Bamboo Grove"),
    ("learn", "Study your origin manual with **`/learn`**"),
    ("adventure", "Complete your first **`/adventure`** (sage's trial)"),
    ("breakthrough", "Attempt **`/breakthrough`** when qi is full"),
]

SAGE_TRIAL_ENCOUNTER = AdventureEncounter(
    id="sage_trial_first",
    prompt=(
        "A white-robed **Sage of the Bamboo Path** blocks the trail, staff planted between worlds.\n\n"
        "*\"Young daoist — your heart will define your dao. The sect watches. How do you answer?\"*"
    ),
    encounter_type="choice",
    choices=(
        AdventureChoice(
            "bow",
            "Bow and ask for righteous guidance",
            0.12,
            1.0,
            0.0,
            karma_delta=8,
            manual_pool="righteous_elder",
            manual_chance=0.85,
        ),
        AdventureChoice(
            "demand",
            "Demand he share forbidden secrets",
            -0.05,
            1.15,
            0.0,
            karma_delta=-10,
            manual_pool="demonic_elder",
            manual_chance=0.85,
            spirit_stones=12,
        ),
        AdventureChoice(
            "observe",
            "Observe silently and learn",
            0.08,
            1.0,
            0.0,
            karma_delta=3,
            manual_pool="neutral_wanderer",
            manual_chance=0.85,
        ),
    ),
)

NOVICE_SEGMENT2_ENCOUNTER = AdventureEncounter(
    id="novice_bamboo_closing",
    prompt=(
        "The sage's words still echo as a **Spirit Hare** darts across the trail — "
        "a gentle final test of your budding art."
    ),
    encounter_type="choice",
    choices=(
        AdventureChoice(
            "strike",
            "Strike it down before it can flee",
            0.12,
            1.1,
            0.0,
            karma_delta=-6,
        ),
        AdventureChoice(
            "track",
            "Track it at a distance without harming it",
            0.08,
            1.0,
            0.0,
            karma_delta=5,
        ),
        AdventureChoice(
            "meditate",
            "Sit in qi resonance and let it pass",
            0.10,
            0.95,
            0.0,
            karma_delta=8,
        ),
    ),
)


def _load_origin_gifts() -> dict:
    global _gifts_cache
    if _gifts_cache is None:
        with ORIGIN_GIFTS_PATH.open(encoding="utf-8") as f:
            _gifts_cache = json.load(f)
    return _gifts_cache


def invalidate_origin_gifts_cache() -> None:
    global _gifts_cache
    _gifts_cache = None


def _normalize_origin_key(origin: str) -> str:
    return origin.replace("\u2019", "'").replace("'", "'").strip()


def get_origin_starter_gift(origin: str) -> dict | None:
    gifts = _load_origin_gifts()
    if origin in gifts:
        return gifts[origin]
    normalized = _normalize_origin_key(origin)
    for key, value in gifts.items():
        if _normalize_origin_key(key) == normalized:
            return value
    return None


def _trial_step(player: Player) -> int:
    value = getattr(player, "novice_trial_step", None)
    if value is None:
        return 0
    return int(value)


def trial_complete(player: Player) -> bool:
    return _trial_step(player) >= TRIAL_COMPLETE_STEP


def novice_breakthrough_pace(player: Player) -> bool:
    return not trial_complete(player) and player.realm_index == 0 and player.substage == 0


def heal_stuck_novice_adventure(player: Player) -> bool:
    """
    Older runs incremented adventures_completed on failed first attempts,
    which blocks the sage trial. Reset when the trial still expects that journey.
    """
    if trial_complete(player):
        return False
    step = _trial_step(player)
    if step != 4:
        return False
    if int(getattr(player, "adventures_completed", 0) or 0) <= 0:
        return False
    player.adventures_completed = 0
    return True


def requires_sage_trial(player: Player) -> bool:
    """True while the Outer Disciple Trial still needs the scripted sage journey."""
    if trial_complete(player):
        return False
    step = _trial_step(player)
    if step == 4:
        return True
    return step < 4 and int(getattr(player, "adventures_completed", 0) or 0) == 0


def is_first_adventure(player: Player) -> bool:
    return requires_sage_trial(player)


def trial_step_label(player: Player) -> str | None:
    if trial_complete(player):
        return None
    step = _trial_step(player)
    if step >= len(TRIAL_STEPS):
        return None
    _, label = TRIAL_STEPS[step]
    return label


def format_trial_progress(player: Player) -> str | None:
    if trial_complete(player):
        return None
    step = int(getattr(player, "novice_trial_step", 0))
    total = len(TRIAL_STEPS)
    current = trial_step_label(player) or "Continue your dao"
    return f"**Outer Disciple Trial** — step **{min(step + 1, total)}/{total}**\n▸ {current}"


def apply_origin_starter_gifts(session: Session, player: Player) -> list[str]:
    gift = get_origin_starter_gift(player.origin)
    if gift is None:
        return []

    messages: list[str] = []
    player.spirit_stones += int(gift.get("spirit_stones", 0))
    player.qi += int(gift.get("starting_qi", 0))

    for item_id, qty in (gift.get("items") or {}).items():
        add_item(session, player.id, item_id, int(qty))

    manual_id = gift.get("manual_item_id")
    if manual_id:
        add_item(session, player.id, str(manual_id), 1)

    ceremony = gift.get("ceremony")
    if ceremony:
        messages.append(f"🎋 {ceremony}")
    stat_note = gift.get("stat_note")
    if stat_note:
        messages.append(f"_{stat_note}_")
    if manual_id:
        from .inventory import get_item_name

        messages.append(f"📜 **{get_item_name(str(manual_id))}** rests in your storage ring.")

    return messages


def apply_novice_cultivate_boost(player: Player, qi_gain: int) -> int:
    count = int(getattr(player, "novice_cultivates", 0))
    if count < NOVICE_CULTIVATE_BOOST_STEPS and novice_breakthrough_pace(player):
        return max(0, int(qi_gain * NOVICE_CULTIVATE_BOOST_MULT))
    return qi_gain


def should_force_first_cultivate_event(player: Player) -> bool:
    return (
        not trial_complete(player)
        and int(getattr(player, "novice_trial_step", 0)) <= 1
        and int(getattr(player, "novice_cultivates", 0)) == 0
    )


def apply_first_hunt_bonus(session: Session, player: Player, drops: dict[str, int]) -> str | None:
    if trial_complete(player) or int(getattr(player, "novice_trial_step", 0)) != 2:
        return None
    if drops.get(FRAGMENT_ITEM_ID, 0) >= 1:
        return None
    drops[FRAGMENT_ITEM_ID] = drops.get(FRAGMENT_ITEM_ID, 0) + 1
    add_item(session, player.id, FRAGMENT_ITEM_ID, 1)
    return "Trial reward — a **Technique Fragment** shakes loose from the beast."


def on_daily_claimed(player: Player) -> list[str]:
    if trial_complete(player) or int(getattr(player, "novice_trial_step", 0)) != 0:
        return []
    player.novice_trial_step = 1
    player.spirit_stones += 5
    return [
        "🎋 **Outer Disciple Trial — Step 1 complete.** "
        "The sect records your stipend (+5 bonus spirit stones)."
    ]


def on_cultivated(player: Player) -> list[str]:
    player.novice_cultivates = int(getattr(player, "novice_cultivates", 0)) + 1
    if trial_complete(player) or int(getattr(player, "novice_trial_step", 0)) != 1:
        return []
    player.novice_trial_step = 2
    return [
        "🎋 **Outer Disciple Trial — Step 2 complete.** "
        "Your meridians stir — try **`/hunt`** in the Bamboo Grove."
    ]


def on_hunt_victory(player: Player) -> list[str]:
    if trial_complete(player) or int(getattr(player, "novice_trial_step", 0)) != 2:
        return []
    player.novice_trial_step = 3
    return [
        "🎋 **Outer Disciple Trial — Step 3 complete.** "
        "Study your origin manual with **`/learn`**, then **`/equip-technique`**."
    ]


def on_technique_learned(session: Session, player: Player, technique_id: str) -> list[str]:
    if trial_complete(player) or technique_id == "basic_strike":
        return []
    step = int(getattr(player, "novice_trial_step", 0))
    if step >= 4:
        return []
    from .combat.loadout import get_learned_technique_ids

    learned = get_learned_technique_ids(session, player.id)
    if len(learned) < 2:
        return []
    player.novice_trial_step = 4
    return [
        "🎋 **Outer Disciple Trial — Step 4 complete.** "
        "Your first true art is ready — begin your **`/adventure`** (the sage awaits)."
    ]


def on_adventure_completed(session: Session, player: Player, *, segments_cleared: int) -> tuple[list[str], bool]:
    """Returns messages and whether to waive adventure cooldown."""
    from .adventure import SEGMENTS_PER_RUN

    messages: list[str] = []
    waive_cd = False
    was_first = int(getattr(player, "adventures_completed", 0) or 0) == 0
    finished_run = segments_cleared >= SEGMENTS_PER_RUN

    if finished_run:
        player.adventures_completed = int(getattr(player, "adventures_completed", 0) or 0) + 1

    if was_first and finished_run:
        waive_cd = True
        messages.append(
            "☯️ **First journey complete** — the sect grants respite; "
            "your adventure cooldown is waived this once."
        )
        if not trial_complete(player) and _trial_step(player) == 4:
            player.novice_trial_step = 5
            messages.append(
                "🎋 **Outer Disciple Trial — Step 5 complete.** "
                "Fill your qi and attempt **`/breakthrough`**."
            )
    elif was_first and segments_cleared > 0:
        messages.append(
            "_Your first journey is unfinished — the sage's trial still awaits. "
            "Use **`/adventure continue`** or start fresh in the Bamboo Grove._"
        )
    return messages, waive_cd


def on_breakthrough_success(session: Session, player: Player, rng: random.Random) -> list[str]:
    if trial_complete(player) or int(getattr(player, "novice_trial_step", 0)) != 5:
        return []
    player.novice_trial_step = TRIAL_COMPLETE_STEP
    drops: dict[str, int] = {}
    msgs: list[str] = [
        "🎋 **Outer Disciple Trial complete!** "
        "You leave mortality's first gate — the sect acknowledges your dao."
    ]
    player.spirit_stones += 15
    msgs.append("💎 **+15** spirit stones from the sect vault.")
    note = roll_manual_pool_reward(
        session,
        player.id,
        "neutral_wanderer",
        rng,
        drops,
        chance=1.0,
    )
    for item_id, qty in drops.items():
        add_item(session, player.id, item_id, qty)
    if note:
        msgs.append(note)
    elif not drops:
        add_item(session, player.id, FRAGMENT_ITEM_ID, 2)
        msgs.append("📜 **2 Technique Fragments** are awarded for your dedication.")
    return msgs


def pick_novice_encounter(segment: int) -> AdventureEncounter | None:
    if segment == 1:
        return SAGE_TRIAL_ENCOUNTER
    if segment == 2:
        return NOVICE_SEGMENT2_ENCOUNTER
    return None


def novice_adventure_success_floor(state: dict) -> float:
    if state.get("novice_adventure"):
        return 0.92
    return 0.0
