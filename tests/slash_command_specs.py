"""Parameters for slash-command integration smoke tests."""
from __future__ import annotations

from dataclasses import dataclass, field

from discord import app_commands

from src.bot import ORIGIN_CHOICES
from src.game import ORIGINS


@dataclass(frozen=True)
class SlashCommandSpec:
    """Qualified command name: 'hunt' or 'craft pill'."""

    name: str
    kwargs: dict = field(default_factory=dict)
    requires_player: bool = True
    skip: bool = False
    skip_reason: str = ""
    expect_not_started: bool = False
    validate_view: bool = False
    # Substrings that must NOT appear (use when testing the success path).
    forbidden_in_text: tuple[str, ...] = ()


def _origin_choice() -> app_commands.Choice[str]:
    return ORIGIN_CHOICES[0] if ORIGIN_CHOICES else app_commands.Choice(name=ORIGINS[0], value=ORIGINS[0])


# Hand-maintained valid invocations. Discovery test ensures coverage.
SLASH_COMMAND_SPECS: list[SlashCommandSpec] = [
    SlashCommandSpec("help", requires_player=False),
    SlashCommandSpec("areas"),
    SlashCommandSpec("roots"),
    SlashCommandSpec("recipes"),
    SlashCommandSpec("sect-list"),
    SlashCommandSpec("leaderboard"),
    SlashCommandSpec("cooldown"),
    SlashCommandSpec("profile", validate_view=True),
    SlashCommandSpec("inventory"),
    SlashCommandSpec("techniques", validate_view=True),
    SlashCommandSpec("stats"),
    SlashCommandSpec("gear"),
    SlashCommandSpec("loadout"),
    SlashCommandSpec("shop", kwargs={"item": "qi_gathering_pill", "quantity": 1}),
    SlashCommandSpec("cultivate"),
    SlashCommandSpec("daily"),
    SlashCommandSpec("breakthrough"),
    SlashCommandSpec("gather", kwargs={"area": "mortal_grove"}),
    SlashCommandSpec("hunt", kwargs={"area": "mortal_grove"}, validate_view=True),
    SlashCommandSpec("adventure", validate_view=True),
    SlashCommandSpec("adventure-continue"),
    SlashCommandSpec("adventure-abandon"),
    SlashCommandSpec("dungeon", kwargs={"dungeon": "mortal_catacomb"}),
    SlashCommandSpec("dungeon-cancel"),
    SlashCommandSpec("use", kwargs={"item": "qi_gathering_pill"}),
    SlashCommandSpec("forge", kwargs={"slot": "weapon"}),
    SlashCommandSpec("temper", kwargs={"stat": "external_strength"}),
    SlashCommandSpec("meridian", kwargs={"stat": "external_strength"}),
    SlashCommandSpec("craft pill", kwargs={"recipe": "qi_gathering_pill", "amount": 1}),
    SlashCommandSpec("craft key", kwargs={"recipe": "blackwind_key"}),
    SlashCommandSpec("craft manual", forbidden_in_text=("materials", "fragments")),
    SlashCommandSpec("equip", kwargs={"gear": "1"}, skip=True, skip_reason="needs stash gear id"),
    SlashCommandSpec("recycle", kwargs={"gear": "1"}, skip=True, skip_reason="needs stash gear id"),
    SlashCommandSpec("unequip", kwargs={"slot": app_commands.Choice(name="Weapon", value="weapon")}),
    SlashCommandSpec("affix", kwargs={"gear": "1"}, skip=True, skip_reason="needs gear id"),
    SlashCommandSpec("item", kwargs={"name": "qi_gathering_pill"}),
    SlashCommandSpec("remind", kwargs={"action": app_commands.Choice(name="Status", value="status")}),
    SlashCommandSpec("reroll_root", forbidden_in_text=("spirit stone",)),
    SlashCommandSpec("reset", kwargs={"confirm": False}),
    SlashCommandSpec(
        "start",
        kwargs={"dao_name": "IntegrationDao", "origin": _origin_choice()},
        requires_player=False,
        expect_not_started=True,
        forbidden_in_text=("already begun",),
    ),
    SlashCommandSpec("clan"),
    SlashCommandSpec("clan-invites"),
    SlashCommandSpec("sect"),
    SlashCommandSpec("sect-task"),
    SlashCommandSpec("sect-shop"),
    SlashCommandSpec("sect-join", kwargs={"sect": "wudang"}, forbidden_in_text=("already",)),
    SlashCommandSpec("sect-leave", forbidden_in_text=("not a member", "not in")),
    SlashCommandSpec("sect-buy", kwargs={"item": "manual_swift_slash"}, forbidden_in_text=("merit",)),
    SlashCommandSpec(
        "duel",
        kwargs={"opponent": None},  # filled in test
        skip=True,
        skip_reason="needs second member mock",
    ),
    SlashCommandSpec("clan-create", kwargs={"name": "IntegrationClan"}, forbidden_in_text=("already",)),
    SlashCommandSpec("clan-join", kwargs={"name": "NoSuchClan"}, forbidden_in_text=("not found",)),
    SlashCommandSpec("clan-leave"),
    SlashCommandSpec("clan-invite", skip=True, skip_reason="needs member target"),
    SlashCommandSpec("clan-invite-only", kwargs={"enabled": True}),
    SlashCommandSpec("post-tutorial", skip=True, skip_reason="admin channel post"),
    SlashCommandSpec("post-library", skip=True, skip_reason="admin channel post"),
]

SPECS_BY_NAME: dict[str, SlashCommandSpec] = {spec.name: spec for spec in SLASH_COMMAND_SPECS}
