# Bundled Fonts

`DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` are shipped so Pillow can render
profile and technique card text consistently on Railway Linux hosts.

If these files are missing, Pillow falls back to a tiny bitmap font and card text
becomes unreadable. Set `PROFILE_CARD_IMAGE=0` to use text-only Discord embeds
while diagnosing font or image rendering problems.

License: DejaVu Fonts License, https://dejavu-fonts.github.io/License.html
