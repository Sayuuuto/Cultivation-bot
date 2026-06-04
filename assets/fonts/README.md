# Bundled Fonts

`DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` — shipped so Pillow can render profile and technique card text consistently on Railway Linux hosts.

**If fonts are missing:** Pillow falls back to a tiny bitmap font, making card text unreadable.

**Workaround:** set `PROFILE_CARD_IMAGE=0` to use text-only Discord embeds while diagnosing font or image rendering problems.

**License:** DejaVu Fonts License — https://dejavu-fonts.github.io/License.html
