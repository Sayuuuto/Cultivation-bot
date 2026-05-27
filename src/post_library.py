"""Post the sect technique library to a Discord channel.

Usage:
    py -m src.post_library
    py -m src.post_library --channel 123456789012345678

Set LIBRARY_CHANNEL_ID in .env for the default channel.
Requires Read Message History, Send Messages, Embed Links, and Pin Messages.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

import discord

from .config import get_config
from .library_info import build_library_embeds, build_library_intro_markdown


@dataclass(frozen=True)
class PostLibraryResult:
    posted: int
    deleted: int


async def clear_bot_messages(
    channel: discord.abc.Messageable,
    *,
    me: discord.ClientUser | discord.User,
) -> int:
    """Delete all messages authored by the bot in a text channel."""
    if not isinstance(channel, discord.TextChannel):
        return 0

    deleted = 0
    async for message in channel.history(limit=None):
        if message.author.id != me.id:
            continue
        try:
            if message.pinned:
                await message.unpin()
            await message.delete()
            deleted += 1
            await asyncio.sleep(0.25)
        except (discord.Forbidden, discord.NotFound):
            continue
    return deleted


async def post_library(
    channel: discord.abc.Messageable,
    *,
    pin_intro: bool = True,
    clear_existing: bool = True,
    me: discord.ClientUser | discord.User | None = None,
) -> PostLibraryResult:
    deleted = 0
    if clear_existing and me is not None:
        deleted = await clear_bot_messages(channel, me=me)

    intro = await channel.send(build_library_intro_markdown())
    if pin_intro and isinstance(channel, discord.TextChannel):
        try:
            await intro.pin()
        except discord.Forbidden:
            pass

    posted = 1
    for embed in build_library_embeds():
        await channel.send(embed=embed)
        posted += 1
        await asyncio.sleep(0.6)
    return PostLibraryResult(posted=posted, deleted=deleted)


async def _run_post(channel_id: str, *, pin_intro: bool, clear_existing: bool) -> PostLibraryResult:
    cfg = get_config()
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    result = PostLibraryResult(posted=0, deleted=0)

    @client.event
    async def on_ready():
        nonlocal result
        try:
            channel = await client.fetch_channel(int(channel_id))
            result = await post_library(
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
    parser = argparse.ArgumentParser(description="Post the technique library to Discord.")
    parser.add_argument(
        "--channel",
        help="Channel ID to post in (overrides LIBRARY_CHANNEL_ID in .env)",
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
    channel_id = (args.channel or getattr(cfg, "library_channel_id", None) or "").strip()
    if not channel_id:
        print(
            "Error: set LIBRARY_CHANNEL_ID in .env or pass --channel <id>.\n"
            "Enable Developer Mode → right-click channel → Copy Channel ID.",
            file=sys.stderr,
        )
        return 1

    await _run_post(channel_id, pin_intro=not args.no_pin, clear_existing=not args.no_clear)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
