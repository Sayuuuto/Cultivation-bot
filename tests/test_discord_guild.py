from __future__ import annotations

from src.discord_guild import abode_channel_slug, realm_role_name
from src.guidance import get_abode_welcome_intro
from src.realms import get_realm_names


def test_abode_channel_slug_uses_dao_name():
    assert abode_channel_slug("Cloud Walker") == "abode-cloud-walker"


def test_abode_channel_slug_strips_invalid_characters():
    assert abode_channel_slug("Li *Mei*!!!") == "abode-li-mei"


def test_abode_channel_slug_fallback_when_empty():
    assert abode_channel_slug("   ") == "abode-cultivator"


def test_abode_channel_slug_respects_max_length():
    long_name = "a" * 120
    slug = abode_channel_slug(long_name, max_len=100)
    assert slug.startswith("abode-")
    assert len(slug) <= 100


def test_realm_role_name_matches_config():
    assert realm_role_name(0) == "Mortal"
    assert realm_role_name(1) == "Qi Refining"
    assert set(get_realm_names()) == {
        "Mortal",
        "Qi Refining",
        "Foundation Establishment",
        "Core Formation",
        "Nascent Soul",
        "Spirit Severing",
        "Void Refinement",
        "Immortal Ascension",
        "Heavenly Transcendence",
        "Immortal Monarch",
    }


def test_abode_welcome_intro_is_in_world():
    text = get_abode_welcome_intro("Cloud Walker").lower()
    assert "cloud walker" in text
    for phrase in ("mvp", "scaffold", "legacy", "not chosen", "not at"):
        assert phrase not in text
    assert "/daily" in text
    assert "/profile" in text
