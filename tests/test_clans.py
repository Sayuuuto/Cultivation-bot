from __future__ import annotations

from src.clans import get_clan_by_name
from src.models import Clan, Player


def test_create_and_join_clan(session, player):
    clan = Clan(
        guild_id=player.guild_id,
        name="Iron Lotus",
        created_by_discord_id=player.discord_id,
        clan_qi_contributed=0,
        member_count=1,
    )
    session.add(clan)
    session.flush()

    player.clan_id = clan.id
    player.clan_role = "founder"
    session.commit()

    loaded = get_clan_by_name(session, player.guild_id, "Iron Lotus")
    assert loaded is not None
    assert loaded.name == "Iron Lotus"
    assert player.clan_id == loaded.id


def test_clan_qi_contribution_on_cultivate(session, player):
    import random

    from src.config import get_config
    from src.game import cultivate

    clan = Clan(
        guild_id=player.guild_id,
        name="Test Clan",
        created_by_discord_id=player.discord_id,
        member_count=1,
    )
    session.add(clan)
    session.flush()
    player.clan_id = clan.id
    player.qi = 0
    player.realm_index = 10
    session.commit()

    cfg = get_config()
    result = cultivate(player, clan, cfg, rng=random.Random(42))
    assert result.qi_gain > 0
    assert player.clan_contribution_qi_total > 0
    assert clan.clan_qi_contributed > 0
