"""Post the full cultivation tutorial to a Discord channel.

Usage:
    py -m src.post_tutorial
    py -m src.post_tutorial --channel 123456789012345678

Set TUTORIAL_CHANNEL_ID in .env for the default channel.
Requires the bot to have Send Messages and Embed Links in that channel.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import discord

from .config import get_config
from .tutorial import validate_tutorial_pages


async def post_tutorial(
    channel: discord.abc.Messageable,
    *,
    pin_intro: bool = True,
) -> int:
    from .tutorial import build_tutorial_pages

    validate_tutorial_pages()
    pages = build_tutorial_pages()

    intro = await channel.send(
        "# 📜 Cultivation Bot — Complete Tutorial\n"
        "The guide below is posted in order. Start at the top and scroll down."
    )
    if pin_intro and isinstance(channel, discord.TextChannel):
        try:
            await intro.pin()
        except discord.Forbidden:
            pass

    posted = 1
    for page in pages:
        await channel.send(embed=page)
        posted += 1
        await asyncio.sleep(0.6)
    return posted


async def _run_post(channel_id: str, *, pin_intro: bool) -> int:
    cfg = get_config()
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    result = 0

    @client.event
    async def on_ready():
        nonlocal result
        try:
            channel = await client.fetch_channel(int(channel_id))
            result = await post_tutorial(channel, pin_intro=pin_intro)
            print(f"Posted {result} message(s) to #{getattr(channel, 'name', channel_id)} ({channel_id}).")
        except discord.Forbidden:
            print(f"Missing permission to send messages in channel {channel_id}.", file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            await client.close()

    await client.start(cfg.discord_token)
    return result


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post the cultivation tutorial to Discord.")
    parser.add_argument(
        "--channel",
        help="Channel ID to post in (overrides TUTORIAL_CHANNEL_ID in .env)",
    )
    parser.add_argument(
        "--no-pin",
        action="store_true",
        help="Do not pin the intro message.",
    )
    args = parser.parse_args(argv)

    cfg = get_config()
    channel_id = (args.channel or cfg.tutorial_channel_id or "").strip()
    if not channel_id:
        print(
            "Error: set TUTORIAL_CHANNEL_ID in .env or pass --channel <id>.\n"
            "Enable Developer Mode → right-click channel → Copy Channel ID.",
            file=sys.stderr,
        )
        return 1

    await _run_post(channel_id, pin_intro=not args.no_pin)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
