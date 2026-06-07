from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .inventory import add_item
from .models import Player
from .novice_trial import TRIAL_STEPS, trial_complete

STORY_DIR = Path(__file__).resolve().parent.parent / "config" / "story"
PENDING_TTL_SECONDS = 1800

_npc_cache: dict | None = None
_paths_cache: dict | None = None
_chapter_caches: dict[int, dict] = {}

TRIAL_COMMANDS = ("daily", "cultivate", "hunt", "learn", "equip", "adventure", "breakthrough")

TRIAL_STEP_TO_NODE: dict[int, str] = {
    0: "wait_daily",
    1: "wait_cultivate",
    2: "wait_hunt",
    3: "wait_learn",
    4: "wait_equip",
    5: "wait_adventure",
    6: "wait_breakthrough",
}

COMMAND_TO_TRIAL_INDEX: dict[str, int] = {
    "daily": 0,
    "cultivate": 1,
    "hunt": 2,
    "learn": 3,
    "equip": 4,
    "adventure": 5,
    "breakthrough": 6,
}

SLASH_COMMAND_ALIASES: dict[str, str] = {
    "adventure-continue": "adventure",
    "adventure-abandon": "adventure",
}

TRIAL_EXEMPT_COMMANDS = frozenset(
    {
        "story",
        "start",
        "reset",
        "post-tutorial",
        "post-library",
    }
)


@dataclass
class PendingStoryCreation:
    guild_id: str
    discord_id: str
    node_id: str = "awakening"
    dao_name: str = ""
    gender: str = ""
    story_path: str = ""
    abode_channel_id: str = ""
    story_message_id: str = ""
    updated_at: float = field(default_factory=time.time)


_pending_creations: dict[tuple[str, str], PendingStoryCreation] = {}


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_npc_config() -> dict:
    global _npc_cache
    if _npc_cache is None:
        _npc_cache = _load_json(STORY_DIR / "npc.json")
    return _npc_cache


def get_paths_config() -> dict:
    global _paths_cache
    if _paths_cache is None:
        _paths_cache = _load_json(STORY_DIR / "paths.json")
    return _paths_cache


def get_chapter_config(chapter: int) -> dict:
    if chapter not in _chapter_caches:
        path = STORY_DIR / f"chapter{chapter}.json"
        _chapter_caches[chapter] = _load_json(path)
    return _chapter_caches[chapter]


def invalidate_story_cache() -> None:
    global _npc_cache, _paths_cache
    _npc_cache = None
    _paths_cache = None
    _chapter_caches.clear()


def get_elder_name() -> str:
    return str(get_npc_config().get("elder", {}).get("name", "Elder Yunjian"))


def get_embed_color() -> tuple[int, int, int]:
    elder = get_npc_config().get("elder", {})
    rgb = elder.get("embed_color", [88, 101, 242])
    if isinstance(rgb, list) and len(rgb) == 3:
        return int(rgb[0]), int(rgb[1]), int(rgb[2])
    return 88, 101, 242


def list_story_paths() -> list[tuple[str, str, str]]:
    paths = get_paths_config()
    out: list[tuple[str, str, str]] = []
    for path_id, data in paths.items():
        out.append((path_id, str(data.get("name", path_id)), str(data.get("blurb", ""))))
    return out


def get_path_def(path_id: str) -> dict | None:
    return get_paths_config().get(path_id)


def get_node(chapter: int, node_id: str) -> dict | None:
    cfg = get_chapter_config(chapter)
    nodes = cfg.get("nodes") or {}
    return nodes.get(node_id)


def chapter_start_node(chapter: int) -> str:
    cfg = get_chapter_config(chapter)
    return str(cfg.get("start_node", "awakening"))


def validate_dao_name(name: str) -> str | None:
    cleaned = name.strip()
    if len(cleaned) < 2:
        return "Your name must be at least two characters."
    if len(cleaned) > 32:
        return "Your name cannot exceed thirty-two characters."
    if not re.match(r"^[\w\s\-'.]+$", cleaned, re.UNICODE):
        return "Use letters, spaces, hyphens, or apostrophes only."
    return None


def normalize_dao_name(name: str) -> str:
    return name.strip()


NAME_SUGGESTIONS: tuple[str, ...] = (
    "Lin Feiyu",
    "Chen Wu",
    "Zhao Ming",
    "Shen Li",
    "Han Yue",
    "Gu Wei",
    "Mei Ling",
    "Xu Feng",
    "Jade Rain",
    "Ash Walker",
    "River Song",
    "Starfall",
    "Iron Veil",
    "Silent Lotus",
    "Broken Seal",
    "White Crane",
)


def pick_name_suggestions(discord_id: str, count: int = 3) -> list[str]:
    """Stable random name fragments for this player (same picks if they restart the step)."""
    rng = random.Random(int(discord_id) % (2**32))
    pool = list(NAME_SUGGESTIONS)
    rng.shuffle(pool)
    return pool[: max(1, min(count, len(pool)))]


def get_pending(guild_id: str, discord_id: str) -> PendingStoryCreation | None:
    key = (guild_id, discord_id)
    pending = _pending_creations.get(key)
    if pending is None:
        return None
    if time.time() - pending.updated_at > PENDING_TTL_SECONDS:
        _pending_creations.pop(key, None)
        return None
    return pending


def start_pending(guild_id: str, discord_id: str) -> PendingStoryCreation:
    cfg = get_chapter_config(1)
    start = str(cfg.get("start_node", "awakening"))
    pending = PendingStoryCreation(guild_id=guild_id, discord_id=discord_id, node_id=start)
    _pending_creations[(guild_id, discord_id)] = pending
    return pending


def clear_pending(guild_id: str, discord_id: str) -> None:
    _pending_creations.pop((guild_id, discord_id), None)


def touch_pending(pending: PendingStoryCreation) -> None:
    pending.updated_at = time.time()


def advance_pending(pending: PendingStoryCreation, node_id: str) -> None:
    pending.node_id = node_id
    touch_pending(pending)


def player_story_step(player: Player) -> str:
    step = getattr(player, "story_step", "") or ""
    if step:
        return step
    if trial_complete(player):
        return "complete"
    trial_step = int(getattr(player, "novice_trial_step", 0) or 0)
    return TRIAL_STEP_TO_NODE.get(trial_step, "wait_daily")


def player_story_chapter(player: Player) -> int:
    chapter = int(getattr(player, "story_chapter", 1) or 1)
    return max(1, chapter)


def set_player_story_step(player: Player, node_id: str) -> None:
    player.story_step = node_id


def story_complete(player: Player) -> bool:
    return player_story_step(player) in ("complete", "chapter1_complete")


def elder_trial_active(player: Player) -> bool:
    """True while the Outer Disciple trial is still in progress."""
    return not trial_complete(player)


def should_show_command_guidance(player: Player | None) -> bool:
    if player is None:
        return True
    return not elder_trial_active(player)


def explicit_story_step(player: Player) -> str | None:
    """Persisted story node only — never inferred from trial step."""
    step = getattr(player, "story_step", "") or ""
    return step or None


def current_awaited_command(player: Player) -> str | None:
    if not elder_trial_active(player):
        return None
    if player_story_chapter(player) != 1:
        return None
    story_step = explicit_story_step(player)
    if not story_step:
        return None
    node = get_node(1, story_step)
    if node is None:
        return None
    return node_await_command(node)


def should_apply_trial_activity_cooldown(player: Player, activity: str) -> bool:
    """During the Elder trial, only record activity cooldowns after the matching step completes."""
    if not elder_trial_active(player):
        return True
    idx = COMMAND_TO_TRIAL_INDEX.get(activity)
    if idx is None:
        return False
    return int(getattr(player, "novice_trial_step", 0) or 0) > idx


def elder_trial_allows_activity(player: Player, activity: str) -> bool:
    if not elder_trial_active(player):
        return True
    awaited = current_awaited_command(player)
    if awaited is None:
        return False
    return awaited == activity


def check_elder_trial_command(player: Player, slash_name: str) -> str | None:
    """Return an in-world rejection message, or None if the command is allowed."""
    if not elder_trial_active(player):
        return None
    if slash_name in TRIAL_EXEMPT_COMMANDS:
        return None

    awaited = current_awaited_command(player)
    elder = get_elder_name()

    if awaited is None:
        return (
            f"**{elder}** awaits your answer in your **abode**. "
            f"Open **`/story`** and speak with them there."
        )

    trial_cmd = SLASH_COMMAND_ALIASES.get(slash_name, slash_name)
    if slash_name == "techniques":
        if awaited in ("learn", "equip"):
            trial_cmd = awaited
        elif trial_cmd == slash_name:
            trial_cmd = "learn"
    if trial_cmd == awaited:
        return None

    if slash_name == "profile" and awaited in ("cultivate", "breakthrough"):
        return None

    idx = COMMAND_TO_TRIAL_INDEX.get(awaited)
    if idx is None:
        return (
            f"**{elder}** has not sent you to do that yet. "
            f"Return to your **abode** — **`/story`**."
        )
    _, label = TRIAL_STEPS[idx]
    return (
        f"**{elder}** has not sent you to do that yet.\n"
        f"▸ {label}\n"
        f"Return to your **abode** if you have lost their thread — **`/story`**."
    )


def story_allows_sect_join(player: Player) -> tuple[bool, str]:
    step = player_story_step(player)
    if step in ("complete", "sect_unlock"):
        return True, ""
    return (
        False,
        "The Elder has not yet released you to walk among the sects. Continue **`/story`**.",
    )


def story_command_available(player: Player) -> tuple[bool, str]:
    chapter = player_story_chapter(player)
    if chapter < 2:
        return True, ""
    if player.realm_index < 1:
        return (
            False,
            "Finish the Elder's trial and break through to **Qi Refining** — "
            "then return to your **abode** for chapter two.",
        )
    return True, ""


def unlock_chapter_two(player: Player) -> list[str]:
    """Called when player first reaches Qi Refining."""
    if player.realm_index < 1:
        return []
    if player_story_chapter(player) >= 2:
        return []
    player.story_chapter = 2
    if player.story_step in ("complete", "sect_unlock"):
        return []
    player.story_step = chapter_start_node(2)
    elder = get_elder_name()
    return [
        f"📖 **{elder}** — your qi has refined. Chapter two continues in your **abode**."
    ]


def apply_path_bonuses(session: Session, player: Player) -> list[str]:
    path_id = getattr(player, "story_path", "") or ""
    path_def = get_path_def(path_id)
    if path_def is None:
        return []

    messages: list[str] = []
    player.spirit_stones += int(path_def.get("spirit_stones", 0))
    player.qi += int(path_def.get("starting_qi", 0))
    for item_id, qty in (path_def.get("items") or {}).items():
        add_item(session, player.id, item_id, int(qty))

    manual_id = path_def.get("manual_item_id")
    if manual_id:
        add_item(session, player.id, str(manual_id), 1)
        from .inventory import get_item_name

        messages.append(f"📜 **{get_item_name(str(manual_id))}** rests in your storage ring.")

    ceremony = path_def.get("ceremony")
    if ceremony:
        messages.append(f"🎋 {ceremony}")
    stat_note = path_def.get("stat_note")
    if stat_note:
        messages.append(f"_{stat_note}_")
    return messages


def get_path_flags(player: Player) -> dict[str, float]:
    path_id = getattr(player, "story_path", "") or ""
    path_def = get_path_def(path_id)
    if path_def is None:
        return {}
    flags = path_def.get("flags") or {}
    return {str(k): float(v) for k, v in flags.items()}


def render_node_text(
    node: dict,
    *,
    dao_name: str = "",
    spirit_root: str = "",
    origin: str = "",
    path_name: str = "",
) -> str:
    text = str(node.get("text", ""))
    replacements = {
        "{dao_name}": dao_name,
        "{spirit_root}": spirit_root,
        "{origin}": origin,
        "{path_name}": path_name,
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def node_has_input(node: dict) -> str | None:
    return node.get("input")


def node_choices(node: dict) -> list[dict]:
    choices = node.get("choices") or []
    return list(choices)[:3]


def node_await_command(node: dict) -> str | None:
    cmd = node.get("await_command")
    return str(cmd) if cmd else None


def resolve_next_node(node: dict) -> str | None:
    nxt = node.get("next")
    return str(nxt) if nxt else None


def advance_player_node(player: Player, node_id: str) -> None:
    player.story_step = node_id


def on_story_command_completed(player: Player, command: str) -> tuple[list[str], str | None]:
    """Advance story when an awaited game command completes.

    Returns (messages, next_node) — next_node is the story node the story
    advanced to, or None if no advancement occurred.
    """
    if story_complete(player) and player_story_chapter(player) == 1:
        return [], None
    chapter = player_story_chapter(player)
    if chapter != 1:
        return [], None

    step = player_story_step(player)
    node = get_node(1, step)
    if node is None:
        return [], None

    awaited = node_await_command(node)
    if awaited != command:
        return [], None

    next_id = resolve_next_node(node)
    if not next_id:
        return [], None

    player.story_step = next_id
    return [], next_id


def on_chapter2_node_reached(player: Player, node_id: str) -> None:
    player.story_step = node_id


def format_story_progress(player: Player) -> str | None:
    chapter = player_story_chapter(player)
    step = player_story_step(player)

    if chapter >= 2 and step == "complete":
        return None
    if chapter == 1 and step in ("complete", "chapter1_complete"):
        if player.realm_index < 1:
            return (
                f"**{get_elder_name()}'s Trial** — complete\n"
                "▸ Break through to **Qi Refining** — the Elder continues in your **abode**."
            )
        return None

    if step in ("complete",):
        return None

    node = get_node(chapter, step)
    if node is None:
        trial_step = int(getattr(player, "novice_trial_step", 0) or 0)
        if trial_step < len(TRIAL_STEPS):
            _, label = TRIAL_STEPS[trial_step]
            return f"**{get_elder_name()}'s Trial** — step **{trial_step + 1}/{len(TRIAL_STEPS)}**\n▸ {label}"
        return None

    awaited = node_await_command(node)
    if awaited:
        idx = COMMAND_TO_TRIAL_INDEX.get(awaited)
        if idx is not None:
            _, label = TRIAL_STEPS[idx]
            return f"**{get_elder_name()}'s Trial** — step **{idx + 1}/{len(TRIAL_STEPS)}**\n▸ {label}"

    return f"**{get_elder_name()}** — continue in your **abode**."


def initial_story_state_for_new_player() -> tuple[int, str]:
    return 1, "trial_intro"


def finalize_creation_state(player: Player) -> None:
    player.story_chapter = 1
    player.story_step = "trial_intro"
    player.novice_trial_step = 0


def migrate_story_step_from_trial(player: Player) -> None:
    if getattr(player, "story_step", ""):
        return
    if trial_complete(player):
        player.story_step = "complete"
        return
    trial_step = int(getattr(player, "novice_trial_step", 0) or 0)
    player.story_step = TRIAL_STEP_TO_NODE.get(trial_step, "wait_daily")


def build_story_embed_fields(
    player: Player | None,
    node: dict,
    *,
    dao_name: str = "",
    spirit_root: str = "",
    origin: str = "",
    path_name: str = "",
) -> dict[str, Any]:
    speaker = str(node.get("speaker", get_elder_name()))
    description = render_node_text(
        node,
        dao_name=dao_name,
        spirit_root=spirit_root,
        origin=origin,
        path_name=path_name,
    )
    awaited = node_await_command(node)
    footer = None
    if awaited:
        footer = "Complete the marked command to continue your story."
    return {
        "title": speaker,
        "description": description,
        "footer": footer,
    }
