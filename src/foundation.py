from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .drop_sources import format_missing_materials_message
from .inventory import get_item_name, get_item_quantity, remove_item
from .models import Player

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "foundation.json"

_foundation_cfg: dict | None = None


def _load_cfg() -> dict:
    global _foundation_cfg
    if _foundation_cfg is None:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            _foundation_cfg = json.load(f)
    return _foundation_cfg


def invalidate_foundation_cache() -> None:
    global _foundation_cfg
    _foundation_cfg = None


def _parse_bonus_json(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if int(v) > 0}


def _save_bonus_json(bonuses: dict[str, int]) -> str:
    cleaned = {k: v for k, v in bonuses.items() if v > 0}
    return json.dumps(cleaned, sort_keys=True)


def get_body_bonuses(player: Player) -> dict[str, int]:
    return _parse_bonus_json(getattr(player, "foundation_body_json", None))


def get_meridian_bonuses(player: Player) -> dict[str, int]:
    return _parse_bonus_json(getattr(player, "foundation_meridian_json", None))


def _body_cap(stat: str, realm_index: int) -> int:
    cfg = _load_cfg()
    base = int(cfg["body_caps_base"].get(stat, 0))
    per = int(cfg["body_cap_per_realm"].get(stat, 0))
    return base + max(0, realm_index) * per


def _meridian_cap(stat: str, realm_index: int) -> int:
    cfg = _load_cfg()
    base = int(cfg["meridian_caps_base"].get(stat, 0))
    per = int(cfg["meridian_cap_per_realm"].get(stat, 0))
    return base + max(0, realm_index) * per


def body_stat_choices() -> list[str]:
    return list(_load_cfg()["body_stats"].keys())


def meridian_stat_choices() -> list[str]:
    return list(_load_cfg()["meridian_stats"].keys())


def body_stat_label(stat: str) -> str:
    return str(_load_cfg()["body_stats"].get(stat, {}).get("label", stat.replace("_", " ").title()))


def meridian_stat_label(stat: str) -> str:
    return str(_load_cfg()["meridian_stats"].get(stat, {}).get("label", stat.replace("_", " ").title()))


def apply_foundation_bonuses(player: Player, stats: dict[str, int]) -> None:
    cfg = _load_cfg()
    body = get_body_bonuses(player)
    meridian = get_meridian_bonuses(player)

    for stat, stacks in body.items():
        if stat == "hp":
            hp_per = int(cfg["body_stats"].get("hp", {}).get("hp_per_stack", 8))
            stats["hp"] = stats.get("hp", 0) + stacks * hp_per
        elif stat in stats:
            stats[stat] = stats.get(stat, 0) + stacks

    for stat, stacks in meridian.items():
        if stat == "hp":
            hp_per = int(cfg["meridian_stats"].get("hp", {}).get("hp_per_stack", 5))
            stats["hp"] = stats.get("hp", 0) + stacks * hp_per
        elif stat in stats:
            stats[stat] = stats.get(stat, 0) + stacks


@dataclass(frozen=True)
class FoundationActionResult:
    success: bool
    message: str


def grant_meridian_points(player: Player, amount: int = 1) -> str:
    if amount <= 0:
        return ""
    player.meridian_points = int(getattr(player, "meridian_points", 0)) + amount
    if amount == 1:
        return "🌀 **+1 meridian point** — channel it with **`/meridian`**."
    return f"🌀 **+{amount} meridian points** — channel them with **`/meridian`**."


def grant_body_temper_charges(player: Player, amount: int = 1) -> str:
    if amount <= 0:
        return ""
    player.body_temper_charges = int(getattr(player, "body_temper_charges", 0)) + amount
    if amount == 1:
        return "💪 **+1 body temper charge** — refine flesh with **`/temper`** (no materials)."
    return f"💪 **+{amount} body temper charges** — use **`/temper`** without materials."


def _pick_lesser_temper_stat(player: Player, rng: random.Random) -> str | None:
    cfg = _load_cfg()
    bonuses = get_body_bonuses(player)
    realm = max(0, player.realm_index)
    for stat in cfg.get("lesser_temper_priority", body_stat_choices()):
        if bonuses.get(stat, 0) < _body_cap(stat, realm):
            return stat
    return None


def apply_lesser_body_temper(player: Player, rng: random.Random | None = None) -> FoundationActionResult:
    rng = rng or random.Random()
    stat = _pick_lesser_temper_stat(player, rng)
    if stat is None:
        return FoundationActionResult(
            False,
            "Your flesh is tempered to the limit at this realm — breakthrough raises the ceiling.",
        )
    return temper_body(player, stat, use_charge=True)


def temper_body(
    player: Player,
    stat: str,
    *,
    session: Session | None = None,
    player_id: int | None = None,
    use_charge: bool = False,
) -> FoundationActionResult:
    cfg = _load_cfg()
    stat = stat.lower().strip()
    body_defs = cfg.get("body_stats", {})
    if stat not in body_defs:
        choices = ", ".join(body_defs.keys())
        return FoundationActionResult(False, f"Choose a path to temper: {choices}.")

    realm = max(0, player.realm_index)
    bonuses = get_body_bonuses(player)
    current = bonuses.get(stat, 0)
    cap = _body_cap(stat, realm)
    if current >= cap:
        return FoundationActionResult(
            False,
            f"**{body_stat_label(stat)}** is tempered to your realm's limit ({current}/{cap}). "
            "Break through to raise the ceiling.",
        )

    if use_charge:
        charges = int(getattr(player, "body_temper_charges", 0))
        if charges <= 0:
            return FoundationActionResult(
                False,
                "You have no essence charges. Claim **`/daily`**, succeed at **`/breakthrough`**, "
                "or spend hunt materials with **`/temper`**.",
            )
        player.body_temper_charges = charges - 1
    else:
        if session is None or player_id is None:
            return FoundationActionResult(False, "Materials could not be verified.")
        inputs: dict[str, int] = dict(body_defs[stat].get("inputs", {}))
        short = any(get_item_quantity(session, player_id, item_id) < qty for item_id, qty in inputs.items())
        if short:
            return FoundationActionResult(
                False,
                format_missing_materials_message(session, player_id, inputs, action="temper your body"),
            )
        for item_id, qty in inputs.items():
            if not remove_item(session, player_id, item_id, qty):
                return FoundationActionResult(False, "Materials slipped away mid-tempering. Try again.")

    bonuses[stat] = current + 1
    player.foundation_body_json = _save_bonus_json(bonuses)
    label = body_stat_label(stat)
    via = "refined essence" if use_charge else "demon cores and herbs"
    return FoundationActionResult(
        True,
        f"Your body hardens — **{label}** +1 ({bonuses[stat]}/{cap}) via {via}.",
    )


def spend_meridian_point(player: Player, stat: str) -> FoundationActionResult:
    cfg = _load_cfg()
    stat = stat.lower().strip()
    meridian_defs = cfg.get("meridian_stats", {})
    if stat not in meridian_defs:
        choices = ", ".join(meridian_defs.keys())
        return FoundationActionResult(False, f"Open a channel toward: {choices}.")

    points = int(getattr(player, "meridian_points", 0))
    cost = int(meridian_defs[stat].get("cost", 1))
    if points < cost:
        return FoundationActionResult(
            False,
            f"You need **{cost}** meridian point(s) (you hold **{points}**). "
            "Earn more through **`/cultivate`**, **`/gather`**, and dao events.",
        )

    realm = max(0, player.realm_index)
    bonuses = get_meridian_bonuses(player)
    current = bonuses.get(stat, 0)
    cap = _meridian_cap(stat, realm)
    if current >= cap:
        return FoundationActionResult(
            False,
            f"**{meridian_stat_label(stat)}** meridians are fully opened ({current}/{cap}) at this realm.",
        )

    player.meridian_points = points - cost
    bonuses[stat] = current + 1
    player.foundation_meridian_json = _save_bonus_json(bonuses)
    label = meridian_stat_label(stat)
    return FoundationActionResult(
        True,
        f"A hidden channel opens — **{label}** +1 ({bonuses[stat]}/{cap}). "
        f"**{player.meridian_points}** meridian point(s) remain.",
    )


def roll_gather_meridian_insight(
    player: Player,
    comprehension: int,
    rng: random.Random,
) -> str | None:
    cfg = _load_cfg()
    chance = float(cfg.get("gather_meridian_base_chance", 0.07))
    chance += comprehension * float(cfg.get("gather_meridian_per_comprehension", 0.0015))
    if rng.random() >= min(0.35, chance):
        return None
    return grant_meridian_points(player, 1)


def roll_cultivate_meridian_insight(player: Player, rng: random.Random) -> str | None:
    cfg = _load_cfg()
    chance = float(cfg.get("cultivate_meridian_chance", 0.04))
    if rng.random() >= chance:
        return None
    return grant_meridian_points(player, 1)


def format_foundation_summary(player: Player) -> str:
    body = get_body_bonuses(player)
    meridian = get_meridian_bonuses(player)
    realm = max(0, player.realm_index)
    lines = ["**Foundation**"]

    if body:
        bits = []
        for stat in body_stat_choices():
            stacks = body.get(stat, 0)
            if stacks:
                bits.append(f"{body_stat_label(stat)} {stacks}/{_body_cap(stat, realm)}")
        if bits:
            lines.append("Body tempering — " + " · ".join(bits))
    else:
        lines.append("Body tempering — none yet (`/temper` with hunt materials)")

    points = int(getattr(player, "meridian_points", 0))
    charges = int(getattr(player, "body_temper_charges", 0))
    if meridian:
        bits = []
        for stat in meridian_stat_choices():
            stacks = meridian.get(stat, 0)
            if stacks:
                bits.append(f"{meridian_stat_label(stat)} {stacks}/{_meridian_cap(stat, realm)}")
        if bits:
            lines.append("Meridians — " + " · ".join(bits))
    else:
        lines.append("Meridians — channels closed (`/meridian` to spend points)")
    lines.append(f"Meridian points **{points}** · Essence charges **{charges}**")
    return "\n".join(lines)
