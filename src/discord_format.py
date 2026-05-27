"""Small helpers for Discord-friendly rich text in messages and embeds."""

from __future__ import annotations


def chip(label: str) -> str:
    return f"`{label}`"


def quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def subtext(text: str) -> str:
    return f"-# {text}"
