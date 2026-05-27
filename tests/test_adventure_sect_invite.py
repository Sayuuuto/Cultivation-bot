from __future__ import annotations

import random

from src.adventure import AdventureChoice, _apply_choice_rewards
from src.game_sects import has_sect_invitation, load_game_sects
from src.karma import KARMA_DEMONIC_THRESHOLD


def test_adventure_choice_applies_sect_invitation(session, player):
    load_game_sects()
    player.karma = KARMA_DEMONIC_THRESHOLD
    player.realm_index = 1
    session.commit()

    choice = AdventureChoice(
        id="accept",
        label="Accept the dark offer",
        success_bonus=0.0,
        drop_mult=1.0,
        fail_chance=0.0,
        sect_invitation="shadow_pavilion",
    )
    state: dict = {"messages": [], "drops": {}}
    _apply_choice_rewards(session, player, choice, state, random.Random(1))
    session.flush()

    assert has_sect_invitation(session, player.id, "shadow_pavilion")
    assert any("invitation" in m.lower() for m in state["messages"])
