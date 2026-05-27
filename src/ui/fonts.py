from __future__ import annotations

import logging
import os
from pathlib import Path

from PIL import ImageFont

logger = logging.getLogger(__name__)

_BUNDLED_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

# Order: bundled (works on Railway) → Linux → Windows → macOS
_FONT_SEARCH: tuple[tuple[str, ...], ...] = (
    (
        str(_BUNDLED_DIR / "DejaVuSans.ttf"),
        str(_BUNDLED_DIR / "DejaVuSans-Bold.ttf"),
        str(_BUNDLED_DIR / "LiberationSans-Regular.ttf"),
        str(_BUNDLED_DIR / "LiberationSans-Bold.ttf"),
        str(_BUNDLED_DIR / "Arial.ttf"),
        str(_BUNDLED_DIR / "Arial-Bold.ttf"),
    ),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    (
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeui.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeuib.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf"),
    ),
)

_REGULAR_PATH: str | None = None
_BOLD_PATH: str | None = None
_checked_paths = False


def _resolve_font_paths() -> tuple[str | None, str | None]:
    global _REGULAR_PATH, _BOLD_PATH, _checked_paths
    if _checked_paths:
        return _REGULAR_PATH, _BOLD_PATH

    _checked_paths = True
    for group in _FONT_SEARCH:
        regular = group[0] if len(group) > 0 else None
        bold = group[1] if len(group) > 1 else None
        if regular and os.path.isfile(regular):
            try:
                ImageFont.truetype(regular, 16)
                _REGULAR_PATH = regular
                if bold and os.path.isfile(bold):
                    ImageFont.truetype(bold, 16)
                    _BOLD_PATH = bold
                else:
                    _BOLD_PATH = regular
                logger.info("Card fonts: regular=%s bold=%s", regular, _BOLD_PATH)
                return _REGULAR_PATH, _BOLD_PATH
            except OSError:
                continue

    logger.warning(
        "No TrueType fonts found for profile/skill cards; bundled path=%s exists=%s",
        _BUNDLED_DIR,
        _BUNDLED_DIR.is_dir(),
    )
    return None, None


def card_fonts_available() -> bool:
    regular, _ = _resolve_font_paths()
    return regular is not None


def card_images_enabled() -> bool:
    """Profile / techniques PNG cards default on; set PROFILE_CARD_IMAGE=0 to disable."""
    raw = os.getenv("PROFILE_CARD_IMAGE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def load_card_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a scalable font for PIL cards. Never use bitmap default on production."""
    regular, bold_path = _resolve_font_paths()
    path = bold_path if bold else regular
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    if regular and bold:
        try:
            return ImageFont.truetype(regular, size)
        except OSError:
            pass
    raise RuntimeError(
        "No TrueType font available for card rendering. "
        "Add assets/fonts/DejaVuSans.ttf or set PROFILE_CARD_IMAGE=0."
    )
