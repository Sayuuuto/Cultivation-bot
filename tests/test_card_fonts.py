from src.ui.fonts import card_fonts_available, card_images_enabled, load_card_font


def test_bundled_card_fonts_load():
    assert card_fonts_available()
    font = load_card_font(24)
    assert font.size >= 24


def test_card_images_enabled_by_default(monkeypatch):
    monkeypatch.delenv("PROFILE_CARD_IMAGE", raising=False)
    assert card_images_enabled() is True


def test_card_images_disabled_when_env_zero(monkeypatch):
    monkeypatch.setenv("PROFILE_CARD_IMAGE", "0")
    assert card_images_enabled() is False
