"""Post the full cultivation tutorial to a Discord channel.

Usage:
    py -m src.post_tutorial
    py -m src.post_tutorial --channel 123456789012345678

Set TUTORIAL_CHANNEL_ID in .env for the default channel.
Requires Read Message History, Send Messages, Embed Links, and Pin Messages.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

import discord

from .config import get_config
from .post_library import clear_bot_messages
from .tutorial import build_tutorial_intro_markdown, build_tutorial_pages, validate_tutorial_pages


@dataclass(frozen=True)
class PostTutorialResult:
    posted: int
    deleted: int


async def post_tutorial(
    channel: discord.abc.Messageable,
    *,
    pin_intro: bool = True,
    clear_existing: bool = True,
    me: discord.ClientUser | discord.User | None = None,
) -> PostTutorialResult:
    validate_tutorial_pages()
    pages = build_tutorial_pages()

    deleted = 0
    if clear_existing and me is not None:
        deleted = await clear_bot_messages(channel, me=me)

    intro = await channel.send(build_tutorial_intro_markdown())
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
    return PostTutorialResult(posted=posted, deleted=deleted)


async def _run_post(channel_id: str, *, pin_intro: bool, clear_existing: bool) -> PostTutorialResult:
    cfg = get_config()
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    result = PostTutorialResult(posted=0, deleted=0)

    @client.event
    async def on_ready():
        nonlocal result
        try:
            channel = await client.fetch_channel(int(channel_id))
            result = await post_tutorial(
                channel,
                pin_intro=pin_intro,
                clear_existing=clear_existing,
                me=client.user,
            )
            print(
                f"Cleared {result.deleted} old message(s) and posted {result.posted} new message(s) "
                f"to #{getattr(channel, 'name', channel_id)} ({channel_id})."
            )
        except discord.Forbidden:
            print(f"Missing permission to manage messages in channel {channel_id}.", file=sys.stderr)
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
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not delete the bot's previous messages in the channel before posting.",
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

    await _run_post(channel_id, pin_intro=not args.no_pin, clear_existing=not args.no_clear)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
