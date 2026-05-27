from src.ui.fonts import card_fonts_available, load_card_font


def test_bundled_card_fonts_load():
    assert card_fonts_available()
    font = load_card_font(24)
    assert font.size >= 24
