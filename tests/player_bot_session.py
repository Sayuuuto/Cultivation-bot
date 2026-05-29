"""
Drive the real bot command tree + UI views the way a player does in Discord.

Each method runs the same async callbacks production uses; the harness enforces
discord.py message rules (no mixed embed/embeds, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import discord
from discord import app_commands
from sqlalchemy.orm import Session

from src.models import Player
from tests.discord_command_harness import (
    CapturedInteractionPayload,
    assert_discord_view_valid,
    assert_response_ok,
    click_view_button,
    click_view_button_label,
    enable_production_card_ui,
    hub_view_from_captured,
    invoke_slash,
    make_mock_interaction,
    run_async,
    select_view_option,
    view_from_capture,
)


@dataclass
class PlayerBotSession:
    """Stateful test client: one player, fresh interaction per action."""

    tree: app_commands.CommandTree
    client: Any
    db: Session
    player: Player
    interaction: Any = field(default=None, repr=False)
    last: CapturedInteractionPayload | None = field(default=None, repr=False)

    @classmethod
    def open(
        cls,
        *,
        tree: app_commands.CommandTree,
        client: Any,
        db: Session,
        player: Player,
        production_ui: bool,
        monkeypatch: Any,
    ) -> PlayerBotSession:
        if production_ui:
            enable_production_card_ui(monkeypatch)
        session = cls(
            tree=tree,
            client=client,
            db=db,
            player=player,
            interaction=make_mock_interaction(client=client),
        )
        return session

    def fresh_interaction(self) -> Any:
        self.interaction = make_mock_interaction(client=self.client)
        return self.interaction

    def slash(self, qualified_name: str, /, **kwargs: Any) -> PlayerBotSession:
        self.fresh_interaction()
        self.last = run_async(
            invoke_slash(self.tree, qualified_name, self.interaction, **kwargs)
        )
        assert_response_ok(self.last, context=f"/{qualified_name}")
        view = view_from_capture(self.last)
        if view is not None:
            assert_discord_view_valid(view, context=f"/{qualified_name}")
        return self

    def click(self, label: str) -> PlayerBotSession:
        view = self.require_view()
        self.last = run_async(
            click_view_button_label(view, self.interaction, label=label)
        )
        return self

    def click_custom_id(self, contains: str) -> PlayerBotSession:
        view = self.require_view()
        self.last = run_async(
            click_view_button(view, self.interaction, custom_id_contains=contains)
        )
        return self

    def select(self, option_value: str, *, select_index: int = 0) -> PlayerBotSession:
        view = self.require_view()
        self.last = run_async(
            select_view_option(
                view,
                self.interaction,
                option_value=option_value,
                select_index=select_index,
            )
        )
        return self

    def back_to_combat_skills(self) -> PlayerBotSession:
        return self.click("← Combat Skills")

    def require_view(self) -> discord.ui.View:
        assert self.last is not None, "No prior command or click"
        view = view_from_capture(self.last)
        assert view is not None, (
            f"No view on last response (edit={self.last.edit is not None}, "
            f"followups={len(self.last.followup_messages)})"
        )
        return view

    def hunt_fight_to_end(
        self,
        *,
        technique_id: str = "basic_strike",
        max_turns: int = 30,
    ) -> "HuntFightAudit":
        """Full /hunt button combat until victory/defeat; requires hunt_fight_harness fixtures."""
        from tests.hunt_fight_harness import (
            HuntFightAudit,
            assert_log_delta_invariants,
            finalize_fight_audit,
            last_probed_combat_state,
            load_hunt_combat_state,
            PYTEST_TRAINING_BEAST,
        )

        self.slash("hunt", area="bamboo_grove")
        assert self.last is not None
        assert PYTEST_TRAINING_BEAST.name in self.last.text

        state = load_hunt_combat_state(self.db, self.player.id)
        assert state is not None, "expected ActiveCombat after /hunt"
        assert state.player_shield == 0, "fight should start with no shield pool"
        assert state.opponent_name == PYTEST_TRAINING_BEAST.name

        audit = HuntFightAudit(
            turns_played=0,
            finished=False,
            victory=False,
            fled=False,
            opponent_name=state.opponent_name,
            final_player_hp=state.player.hp,
            final_opponent_hp=state.opponent.hp,
            max_player_shield_seen=0,
            shield_grant_seen=False,
            opponent_hit_phases=0,
            player_strike_phases=0,
        )

        for turn in range(max_turns):
            if state.finished:
                break
            log_before = list(state.log)
            view = self.require_view()
            self.fresh_interaction()
            self.last = run_async(
                click_view_button(
                    view,
                    self.interaction,
                    custom_id_contains=technique_id,
                )
            )
            self.db.commit()

            probed = last_probed_combat_state()
            if probed is not None:
                state = probed
            else:
                loaded = load_hunt_combat_state(self.db, self.player.id)
                if loaded is not None:
                    state = loaded

            new_lines = state.log[len(log_before) :]
            assert_log_delta_invariants(
                state=state,
                log_before=log_before,
                new_lines=new_lines,
                turn_index=turn,
                audit=audit,
            )
            audit.turns_played = turn + 1

            if state.finished:
                break

        assert state.finished or last_probed_combat_state() is not None
        if last_probed_combat_state() is not None:
            state = last_probed_combat_state()
        finalize_fight_audit(state, audit)
        return audit

    def hunt_until_settled(self, *, max_turns: int = 20) -> PlayerBotSession:
        """Strike in hunt combat until victory, flee, or turn limit."""
        turns = 0
        while turns < max_turns:
            view = self.require_view()
            assert_discord_view_valid(view, context=f"hunt turn {turns}")
            self.fresh_interaction()
            try:
                self.last = run_async(
                    click_view_button(
                        view, self.interaction, custom_id_contains="basic_strike"
                    )
                )
            except AssertionError:
                self.last = run_async(
                    click_view_button(view, self.interaction, custom_id_contains=":flee")
                )
                return self
            if self.last.edit and self.last.edit.get("view") is None:
                return self
            text = self.last.text.lower()
            if "defeated" in text or "flees" in text or "fled" in text:
                return self
            turns += 1
        return self

    def open_techniques_hub(self) -> PlayerBotSession:
        return self.slash("techniques")

    def assert_techniques_png_hub(self) -> PlayerBotSession:
        assert self.last is not None
        assert self.last.files, "Expected combat skills PNG on /techniques"
        hub = hub_view_from_captured(self.last)
        assert hub is not None
        return self

    def assert_text_contains(self, *substrings: str) -> PlayerBotSession:
        assert self.last is not None
        text = self.last.text.lower()
        for part in substrings:
            assert part.lower() in text, f"Expected {part!r} in: {self.last.text[:400]}"
        return self

    def reload_player(self) -> Player:
        from sqlalchemy import select

        from src.models import Player

        row = self.db.execute(
            select(Player).where(Player.id == self.player.id)
        ).scalar_one()
        self.player = row
        return row

    def dungeon_fight_to_end(
        self,
        *,
        dungeon_id: str = "pytest_training_chamber",
        max_turns: int = 40,
    ) -> "DungeonFightAudit":
        """Full solo /dungeon: slash, technique buttons, target pick, until run completes."""
        from src.dungeon_discord import DungeonCombatView
        from src.dungeon_party import find_party_for_player
        from src.models import ActiveDungeonParty
        from tests.discord_command_harness import (
            assert_response_ok,
            click_view_button_label,
            click_view_button_label_contains,
        )
        from tests.dungeon_fight_harness import (
            DungeonFightAudit,
            assert_dungeon_log_delta,
            finalize_dungeon_audit,
            last_dungeon_combat_view,
            last_probed_dungeon_state,
            load_dungeon_combat_state,
            PYTEST_DUNGEON_ID,
        )

        assert dungeon_id == PYTEST_DUNGEON_ID
        self.slash("dungeon", dungeon=dungeon_id)
        assert self.last is not None
        assert self.last.deferred, "solo /dungeon should defer then launch"

        party = find_party_for_player(
            self.db, str(self.player.guild_id), str(self.player.discord_id)
        )
        assert party is not None, "expected in_combat party after /dungeon"
        state = load_dungeon_combat_state(self.db, party.id)
        assert state is not None

        audit = DungeonFightAudit(room_label=state.room_label)
        view = last_dungeon_combat_view()
        assert view is not None, "dungeon combat card should expose DungeonCombatView"

        for turn in range(max_turns):
            if state.finished and state.run_complete:
                break
            log_before = list(state.log)
            view = last_dungeon_combat_view() or view
            assert view is not None, f"turn {turn}: missing combat view"
            self.fresh_interaction()

            labels = [getattr(c, "label", "") or "" for c in view.children]
            if any("🎯" in label for label in labels):
                target_label = next(label for label in labels if "🎯" in label)
                self.last = run_async(
                    click_view_button_label(view, self.interaction, label=target_label)
                )
            else:
                assert isinstance(view, DungeonCombatView), type(view)
                self.last = run_async(
                    click_view_button_label_contains(
                        view, self.interaction, substring="Basic Strike"
                    )
                )

            assert_response_ok(self.last, context=f"dungeon turn {turn}")
            self.db.commit()

            probed = last_probed_dungeon_state()
            if probed is not None:
                state = probed
            else:
                reloaded = load_dungeon_combat_state(self.db, party.id)
                if reloaded is not None:
                    state = reloaded

            new_lines = state.log[len(log_before) :]
            assert_dungeon_log_delta(
                state=state,
                new_lines=new_lines,
                turn_index=turn,
                audit=audit,
            )
            audit.turns_played = turn + 1

            refreshed = last_dungeon_combat_view()
            if refreshed is not None:
                view = refreshed

            party = self.db.get(ActiveDungeonParty, party.id)
            if party is not None and party.status == "completed":
                state.run_complete = True
                state.finished = True
                state.victory = True
                break

        finalize_dungeon_audit(state, audit)
        assert find_party_for_player(
            self.db, str(self.player.guild_id), str(self.player.discord_id)
        ) is None
        return audit

    def adventure_run_to_end(
        self,
        *,
        area: str = "bamboo_grove",
        stance: str = "balanced",
        max_steps: int = 24,
    ) -> "AdventureFlowAudit":
        """Walk /adventure choice buttons (and combat if encountered) until the run finishes."""
        from src.adventure import get_active_adventure
        from src.bot import AdventureChoiceView, CombatView
        from tests.adventure_flow_harness import AdventureFlowAudit, assert_no_forbidden_copy
        from tests.discord_command_harness import click_view_button, view_from_capture
        from tests.hunt_fight_harness import last_probed_combat_state, load_hunt_combat_state

        audit = AdventureFlowAudit()
        self.slash("adventure")
        assert self.last is not None
        assert_no_forbidden_copy(self.last.text, context="/adventure")

        for step in range(max_steps):
            active = get_active_adventure(self.db, self.player.id)
            if active is None:
                audit.completed = True
                break

            view = view_from_capture(self.last) if self.last else None
            if view is None:
                break

            self.fresh_interaction()
            if isinstance(view, AdventureChoiceView):
                choice_btn = next(
                    (
                        c
                        for c in view.children
                        if getattr(c, "custom_id", "") and "adv:" in c.custom_id
                    ),
                    None,
                )
                assert choice_btn is not None, "expected adventure choice button"
                self.last = run_async(click_view_button(view, self.interaction, custom_id_contains="adv:"))
                audit.choice_steps += 1
            elif isinstance(view, CombatView):
                combat_state = load_hunt_combat_state(self.db, self.player.id)
                while combat_state is not None and not combat_state.finished:
                    view = self.require_view()
                    self.fresh_interaction()
                    self.last = run_async(
                        click_view_button(
                            view, self.interaction, custom_id_contains="basic_strike"
                        )
                    )
                    self.db.commit()
                    probed = last_probed_combat_state()
                    if probed is not None:
                        combat_state = probed
                    else:
                        combat_state = load_hunt_combat_state(self.db, self.player.id)
                    audit.combat_turns += 1
                    if audit.combat_turns >= max_steps:
                        break
                continue
            else:
                break

            assert_response_ok(self.last, context=f"adventure step {step}")
            assert_no_forbidden_copy(self.last.text, context=f"adventure step {step}")
            self.db.commit()

        active = get_active_adventure(self.db, self.player.id)
        if active is None:
            audit.completed = True
        if self.last is not None:
            audit.final_text = self.last.text
        return audit

    def cultivate_once(self) -> PlayerBotSession:
        """Run /cultivate and assert a successful qi response."""
        qi_before = self.player.qi
        self.slash("cultivate")
        assert self.last is not None
        text = self.last.text.lower()
        assert "wait" not in text and "not ready" not in text, self.last.text[:300]
        self.reload_player()
        assert self.player.qi >= qi_before, "cultivate should not reduce qi"
        assert self.player.last_cultivate_at is not None
        return self

    def cultivate_expect_cooldown(self) -> PlayerBotSession:
        self.slash("cultivate")
        assert self.last is not None
        text = self.last.text.lower()
        assert "wait" in text or "not ready" in text, self.last.text[:300]
        return self
