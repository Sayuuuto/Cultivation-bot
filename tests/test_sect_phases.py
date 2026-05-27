from __future__ import annotations

from src.clans import can_join_clan, create_clan_invitation, set_clan_invite_only
from src.game_sects import (
    award_sect_merit,
    buy_from_sect_shop,
    ensure_daily_sect_task,
    grant_sect_invitation,
    has_sect_invitation,
    join_game_sect,
    on_sect_activity,
    record_sect_task_progress,
    try_grant_sect_invitation_from_adventure,
)
from src.karma import KARMA_DEMONIC_THRESHOLD, KARMA_RIGHTEOUS_THRESHOLD
from src.models import Clan


def test_clan_invitation_allows_join(session, player):
    clan = Clan(
        guild_id=player.guild_id,
        name="Invite Clan",
        created_by_discord_id="999",
        member_count=1,
        invite_only=True,
    )
    session.add(clan)
    session.flush()

    ok, _ = can_join_clan(session, player, clan)
    assert not ok

    create_clan_invitation(
        session,
        clan=clan,
        invitee_discord_id=player.discord_id,
        invited_by_discord_id="999",
    )
    session.flush()
    ok, _ = can_join_clan(session, player, clan)
    assert ok


def test_clan_invite_only_toggle(session, player):
    clan = Clan(
        guild_id=player.guild_id,
        name="Policy Clan",
        created_by_discord_id=player.discord_id,
        member_count=1,
    )
    session.add(clan)
    session.flush()

    msg = set_clan_invite_only(session, clan, True)
    assert "invitation" in msg.lower()
    assert clan.invite_only is True


def test_sect_merit_from_cultivate_activity(session, player):
    player.karma = 0
    join_game_sect(session, player, "wudang")
    player.sect_merit = 0
    session.commit()

    msgs = on_sect_activity(session, player, "cultivate")
    assert player.sect_merit > 0
    assert any("merit" in m.lower() for m in msgs)


def test_daily_sect_task_cultivate_progress(session, player):
    player.karma = 0
    join_game_sect(session, player, "wudang")
    session.commit()

    task = ensure_daily_sect_task(session, player)
    assert task is not None
    assert task.task_type == "cultivate"

    msgs = record_sect_task_progress(session, player, "cultivate")
    session.commit()
    assert player.sect_daily_task_progress == 1
    assert msgs


def test_adventure_grants_secret_sect_invitation(session, player):
    player.karma = KARMA_DEMONIC_THRESHOLD
    player.realm_index = 1
    session.commit()

    msg = try_grant_sect_invitation_from_adventure(
        session, player, "shadow_pavilion", source="adventure"
    )
    assert msg is not None
    assert has_sect_invitation(session, player.id, "shadow_pavilion")
    assert grant_sect_invitation(session, player.id, "shadow_pavilion") is False


def test_imperial_invitation_requires_righteous_karma(session, player):
    player.karma = KARMA_DEMONIC_THRESHOLD
    player.realm_index = 3
    session.commit()

    msg = try_grant_sect_invitation_from_adventure(session, player, "imperial_guard")
    assert msg is None


def test_sect_shop_buy_manual(session, player):
    player.karma = 0
    join_game_sect(session, player, "wudang")
    player.sect_merit = 200
    session.commit()

    ok, msg = buy_from_sect_shop(session, player, "manual_qi_barrier")
    assert ok is True
    assert "qi barrier" in msg.lower() or "Qi Barrier" in msg
    assert player.sect_merit == 120


def test_sect_shop_rejects_insufficient_merit(session, player):
    player.karma = 0
    join_game_sect(session, player, "wudang")
    player.sect_merit = 10
    session.commit()

    ok, msg = buy_from_sect_shop(session, player, "manual_mountain_guard")
    assert not ok
    assert "merit" in msg.lower()


def test_wolf_sect_task_counts_hound_and_wolf(session, player):
    from src.hunt import beast_matches_sect_tag

    assert beast_matches_sect_tag("mist_fang_wolf", "wolf")
    assert beast_matches_sect_tag("mist_hound", "wolf")
    assert beast_matches_sect_tag("mire_hound", "wolf")
    assert not beast_matches_sect_tag("spirit_hare", "wolf")


def test_hunt_sect_task_progress_and_mismatch_hint(session, player):
    from src.game_sects import ensure_daily_sect_task, format_sect_task_status, join_game_sect

    player.karma = 0
    player.realm_index = 1
    join_game_sect(session, player, "wudang")
    session.commit()

    task = ensure_daily_sect_task(session, player)
    assert task is not None
    assert task.task_type == "hunt"
    assert task.beast_tag == "wolf"

    status_text = format_sect_task_status(player)
    assert "Mist Fang Wolf" in status_text or "Mist Hound" in status_text
    assert "/hunt" in status_text

    bad = record_sect_task_progress(session, player, "hunt", beast_id="spirit_hare")
    assert player.sect_daily_task_progress == 0
    assert bad and "unchanged" in bad[0].lower()

    good = record_sect_task_progress(session, player, "hunt", beast_id="mist_hound")
    session.commit()
    assert player.sect_daily_task_progress == 1
    assert good and "progress" in good[0].lower()


def test_award_sect_merit_only_when_member(session, player):
    player.game_sect_id = None
    assert award_sect_merit(player, 50) == 0
    assert player.sect_merit == 0
