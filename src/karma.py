from __future__ import annotations

KARMA_MIN = -100
KARMA_MAX = 100
KARMA_RIGHTEOUS_THRESHOLD = 30
KARMA_DEMONIC_THRESHOLD = -30


def clamp_karma(value: int) -> int:
    return max(KARMA_MIN, min(KARMA_MAX, int(value)))


def karma_tier(karma: int) -> str:
    karma = clamp_karma(karma)
    if karma >= KARMA_RIGHTEOUS_THRESHOLD:
        return "righteous"
    if karma <= KARMA_DEMONIC_THRESHOLD:
        return "demonic"
    return "neutral"


def karma_tier_label(karma: int) -> str:
    tier = karma_tier(karma)
    sign = f"+{karma}" if karma > 0 else str(karma)
    if tier == "righteous":
        return f"Righteous ({sign})"
    if tier == "demonic":
        return f"Demonic ({sign})"
    return f"Neutral ({sign})"


def karma_breakthrough_modifiers(karma: int) -> tuple[float, float]:
    """Returns (success_bonus, fail_setback_multiplier)."""
    karma = clamp_karma(karma)
    success_bonus = min(0.05, max(-0.04, karma * 0.0004))
    setback_mult = 1.0 + min(0.25, max(-0.15, karma * -0.001))
    return success_bonus, setback_mult


def karma_cultivation_text(karma: int) -> str:
    tier = karma_tier(karma)
    if tier == "righteous":
        return "You draw the spirit with a clean will."
    if tier == "demonic":
        return "You pull the spirit with restraint only where necessary."
    return "You draw the qi as if it were always yours."


def karma_breakthrough_setback_text(karma: int) -> str:
    tier = karma_tier(karma)
    if tier == "righteous":
        return "Heaven favors the steadfast — your setback is softened."
    if tier == "demonic":
        return "The path of demons is unforgiving — qi scatters violently."
    return "The dao tests your resolve."


def karma_from_legacy_moral_path(moral_path: str) -> int:
    path = (moral_path or "neutral").lower()
    if path == "righteous":
        return 40
    if path == "demonic":
        return -40
    return 0


def manual_weight_multiplier(karma: int, alignment: str) -> float:
    tier = karma_tier(karma)
    alignment = (alignment or "neutral").lower()
    if alignment == "neutral":
        return 1.1
    if alignment == "righteous" and tier == "righteous":
        return 1.8
    if alignment == "demonic" and tier == "demonic":
        return 1.8
    if alignment == "righteous" and tier == "demonic":
        return 0.6
    if alignment == "demonic" and tier == "righteous":
        return 0.6
    return 1.0
