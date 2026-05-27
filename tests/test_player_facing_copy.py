from __future__ import annotations

import inspect
import re

import pytest

from src import bot, guidance, tutorial

# Substrings that belong in code/docs, not player-visible strings.
FORBIDDEN_PLAYER_COPY = [
    "not at character creation",
    "not chosen at",
    "is not chosen",
    "morality is **not**",
    "karma is earned in",
    "earned in play",
    "future update",
    "not yet implemented",
    "during development",
    "we decided",
    "mvp ",
    " scaffold",
    " backlog",
    "synergy hint",
    "pairs with",
    "synergy & pairings",
]

# Scan these modules' string literals used for Discord UI / guidance.
PLAYER_COPY_MODULES = (guidance, tutorial)


def _collect_module_strings(module) -> list[str]:
    strings: list[str] = []
    for name, obj in inspect.getmembers(module):
        if name.startswith("_"):
            continue
        if isinstance(obj, str):
            strings.append(obj)
        elif callable(obj) and not inspect.isclass(obj):
            try:
                source = inspect.getsource(obj)
            except (OSError, TypeError):
                continue
            strings.extend(re.findall(r'["\']([^"\']{8,})["\']', source))
    return strings


@pytest.mark.parametrize("module", PLAYER_COPY_MODULES)
def test_player_facing_modules_avoid_dev_process_language(module):
    haystack = "\n".join(_collect_module_strings(module)).lower()
    hits = [phrase for phrase in FORBIDDEN_PLAYER_COPY if phrase in haystack]
    assert not hits, f"{module.__name__} contains dev-process phrasing: {hits}"


def test_start_command_strings_are_in_world():
    start_cmd = bot.start_cmd
    description = (start_cmd.description or "").lower()
    origin_param = start_cmd.parameters[1].description.lower()

    for phrase in FORBIDDEN_PLAYER_COPY:
        assert phrase not in description, f"/start description: {phrase}"
        assert phrase not in origin_param, f"/start origin param: {phrase}"


def test_start_guidance_hint_is_in_world():
    hint = guidance.get_next_steps("start", None, None, None, None, lambda *_: 0).lower()
    for phrase in FORBIDDEN_PLAYER_COPY:
        assert phrase not in hint, f"start guidance hint: {phrase}"
