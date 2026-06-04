"""Indirection tests: extracted src/discord_ui/* modules work independently of bot.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.discord_ui.autocomplete import all_areas_autocomplete, filter_options


class TestPureHelpers:
    """Pure functions from src/discord_ui/helpers.py — no mocking needed."""

    def test_to_utc_naive(self):
        from src.discord_ui.helpers import to_utc
        dt = datetime(2025, 6, 1, 12, 0, 0)
        result = to_utc(dt)
        assert result.tzinfo is not None
        assert result.tzinfo.utcoffset(result) == timedelta(0)

    def test_to_utc_aware(self):
        from src.discord_ui.helpers import to_utc
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert to_utc(dt) is dt

    def test_realm_display_mortal(self):
        from src.discord_ui.helpers import realm_display
        result = realm_display(0, 0)
        assert "Mortal" in result
        assert "early" in result

    def test_realm_display_bounds(self):
        from src.discord_ui.helpers import realm_display
        result = realm_display(999, 999)
        assert "late" in result

    def test_time_left_none(self):
        from src.discord_ui.helpers import time_left
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert time_left(now, None) == timedelta()

    def test_time_left_past(self):
        from src.discord_ui.helpers import time_left
        now = datetime(2025, 1, 2, tzinfo=timezone.utc)
        past = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert time_left(now, past) == timedelta(days=1)

    def test_time_left_future_returns_zero(self):
        """time_left returns 0 when 'past' is actually in the future."""
        from src.discord_ui.helpers import time_left
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        future = datetime(2025, 1, 2, tzinfo=timezone.utc)
        assert time_left(now, future) == timedelta()

    def test_format_seconds_zero(self):
        from src.discord_ui.helpers import format_seconds
        assert format_seconds(0) == "0s"

    def test_format_seconds_hours_drops_seconds(self):
        from src.discord_ui.helpers import format_seconds
        assert format_seconds(3661) == "1h 1m"

    def test_format_seconds_minutes_with_seconds(self):
        from src.discord_ui.helpers import format_seconds
        assert format_seconds(61) == "1m 1s"

    def test_format_seconds_just_seconds(self):
        from src.discord_ui.helpers import format_seconds
        assert format_seconds(42) == "42s"

    def test_cooldown_remaining_none(self):
        from src.discord_ui.helpers import cooldown_remaining
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert cooldown_remaining(now, None, 3600) == 0

    def test_cooldown_remaining_expired(self):
        from src.discord_ui.helpers import cooldown_remaining
        now = datetime(2025, 1, 2, tzinfo=timezone.utc)
        last = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert cooldown_remaining(now, last, 3600) == 0

    def test_cooldown_remaining_active(self):
        from src.discord_ui.helpers import cooldown_remaining
        now = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        last = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert cooldown_remaining(now, last, 3600) == 0
        assert cooldown_remaining(now, last, 7200) > 0

    def test_rng_for_returns_random(self):
        from src.discord_ui.helpers import rng_for
        r = rng_for("guild1", "user1", salt="test")
        val = r.random()
        assert 0.0 <= val <= 1.0

    def test_rng_for_different_salt(self):
        from src.discord_ui.helpers import rng_for
        r1 = rng_for("guild1", "user1", salt="a")
        r2 = rng_for("guild1", "user1", salt="b")
        assert r1.random() != r2.random()

    def test_ensure_loaded_player(self):
        from src.discord_ui.helpers import ensure_loaded_player
        player = MagicMock()
        ensure_loaded_player(player)


class TestAutocompleteHelpers:
    """Pure helpers from src/discord_ui/autocomplete.py — no mocking needed."""

    def test_filter_options_no_query(self):
        opts = [("v1", "label1"), ("v2", "label2"), ("v3", "label3")]
        result = filter_options(opts, "")
        assert len(result) == 3

    def test_filter_options_with_query(self):
        opts = [("apple", "Apple Pie"), ("banana", "Banana Split"), ("cherry", "Cherry Tart")]
        result = filter_options(opts, "apple")
        assert result == [("apple", "Apple Pie")]

    def test_filter_options_matches_value_or_label(self):
        opts = [("HELLO", "Hello World"), ("goodbye", "Goodbye World")]
        result = filter_options(opts, "hello")
        assert len(result) == 1

    def test_filter_options_truncates(self):
        opts = [(str(i), f"label{i}") for i in range(50)]
        result = filter_options(opts, "")
        assert len(result) == 25

    @pytest.mark.asyncio
    async def test_all_areas_autocomplete_returns_list(self):
        result = await all_areas_autocomplete(MagicMock(), "")
        assert isinstance(result, list)
        if result:
            from discord import app_commands
            assert isinstance(result[0], app_commands.Choice)


class TestViewConstruction:
    """View classes construct without error — pure UI construction."""

    def test_abandon_stuck_combat_view(self):
        from src.discord_ui.views import AbandonStuckCombatView
        view = AbandonStuckCombatView(owner_discord_id="1", guild_id="2")
        assert len(view.children) == 1

    def test_adventure_choice_view(self):
        from src.discord_ui.views import AdventureChoiceView
        choices = MagicMock()
        choices.__iter__ = lambda _: iter([])
        choices.__len__ = lambda _: 0
        view = AdventureChoiceView(owner_discord_id="1", guild_id="2", active_id=3, choices=choices)
        assert len(view.children) == 0

    def test_duel_challenge_view(self):
        from src.discord_ui.views import DuelChallengeView
        view = DuelChallengeView(challenge_id=1, guild_id="2", challenger_discord_id="3", opponent_discord_id="4")
        assert len(view.children) == 2

    def test_cultivation_buttons(self):
        from src.discord_ui.views import CultivationButtons
        view = CultivationButtons(owner_discord_id="1", cfg=MagicMock(), rng=MagicMock())
        assert len(view.children) == 0

    def test_cultivate_view(self):
        from src.discord_ui.views import CultivateView
        view = CultivateView(owner_discord_id="1", cfg=MagicMock(), rng=MagicMock())
        assert len(view.children) == 2

    def test_breakthrough_commit_view(self):
        from src.discord_ui.views import BreakthroughCommitView
        view = BreakthroughCommitView(
            owner_discord_id="1", guild_id="2", cfg=MagicMock(), rng=MagicMock(), bt_preview=MagicMock()
        )
        assert len(view.children) == 2
