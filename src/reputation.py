from __future__ import annotations

REPUTATION_MIN = -100
REPUTATION_MAX = 100


def clamp_reputation(value: int) -> int:
    return max(REPUTATION_MIN, min(REPUTATION_MAX, int(value)))


def reputation_tier(reputation: int) -> str:
    rep = clamp_reputation(reputation)
    if rep >= 40:
        return "renowned"
    if rep >= 15:
        return "respected"
    if rep <= -40:
        return "notorious"
    if rep <= -15:
        return "distrusted"
    return "unknown"


def reputation_tier_label(reputation: int) -> str:
    labels = {
        "renowned": "Renowned",
        "respected": "Respected",
        "unknown": "Unknown",
        "distrusted": "Distrusted",
        "notorious": "Notorious",
    }
    return labels.get(reputation_tier(reputation), "Unknown")


def reputation_display(reputation: int) -> str:
    return f"{reputation_tier_label(reputation)} ({clamp_reputation(reputation)})"
