from __future__ import annotations

from sqlalchemy import select

from src.combat.loadout import ensure_starter_techniques
from src.models import InventoryItem, Player, PlayerTechnique
from src.player_wipe import wipe_player_character


def test_wipe_player_character_removes_record(session, player):
    ensure_starter_techniques(session, player.id)
    session.add(InventoryItem(player_id=player.id, item_id="spirit_stone_pouch", quantity=1))
    session.commit()

    player_id = player.id
    guild_id = player.guild_id
    discord_id = player.discord_id

    wipe_player_character(session, player)
    session.commit()

    assert session.get(Player, player_id) is None
    assert (
        session.execute(
            select(PlayerTechnique).where(PlayerTechnique.player_id == player_id)
        ).first()
        is None
    )
    assert (
        session.execute(
            select(Player).where(
                Player.guild_id == guild_id,
                Player.discord_id == discord_id,
            )
        ).scalar_one_or_none()
        is None
    )
