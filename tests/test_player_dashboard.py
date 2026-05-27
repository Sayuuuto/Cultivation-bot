from __future__ import annotations

from datetime import datetime, timezone

from src.config import Config
from src.player_dashboard import build_profile_embed, build_techniques_embed, format_activity_lanes
from src.content import load_all_content
from src.inventory import add_item, load_item_catalog


def test_format_activity_lanes(session, player, cfg):
    load_all_content()
    load_item_catalog()
    now = datetime.now(timezone.utc)

    def remaining_fn(now, last, seconds):
        return 0 if last is None else 900

    text = format_activity_lanes(player, cfg, now, remaining_fn, session)
    assert "Cultivate" in text
    assert "Gather" in text
    assert "Hunt" in text


def test_build_profile_embed_includes_martial_dao(session, player, cfg):
    load_all_content()
    load_item_catalog()
    from src.combat_stats import compute_combat_stats
    from src.game import REALMS, SUBSTAGES

    now = datetime.now(timezone.utc)
    combat = compute_combat_stats(player, session)
    realm_text = f"{REALMS[player.realm_index]} ({SUBSTAGES[player.substage]})"
    embed = build_profile_embed(
        player,
        session,
        cfg,
        now,
        offline_qi=0,
        combat=combat,
        realm_display=realm_text,
        remaining_fn=lambda n, l, s: 0,
    )
    field_names = {f.name for f in embed.fields}
    assert "Martial dao" in field_names
    assert "Activity lanes" in field_names


def test_build_techniques_embed_lists_manuals(session, player):
    load_all_content()
    load_item_catalog()
    add_item(session, player.id, "manual_ember_palm", 1)
    session.commit()

    embed = build_techniques_embed(session, player)
    assert "Manuals in your bag" in embed.description
    assert "Ember Palm" in embed.description
