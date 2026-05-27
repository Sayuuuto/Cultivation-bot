"""Force-register slash commands with Discord (no long-running bot).

Usage:
    py -m src.sync_commands

Use this after adding or changing commands. Guild sync (GUILD_ID in .env) is instant;
global sync can take up to ~1 hour to appear everywhere.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Quiet discord HTTP dumps before bot.py configures logging (defaults to DEBUG).
os.environ.setdefault("LOG_LEVEL", "WARNING")

import discord

from .bot import bot
from .config import get_config

SYNC_TIMEOUT_SECONDS = 90


def _quiet_discord_loggers() -> None:
    for name in ("discord", "discord.client", "discord.http", "discord.gateway"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def _shutdown_bot() -> None:
    """Close HTTP session without waiting on a gateway that was never opened."""
    try:
        await asyncio.wait_for(bot.close(), timeout=10.0)
    except asyncio.TimeoutError:
        http = getattr(bot, "http", None)
        if http is not None:
            await http.close()
        print("Warning: bot.close() timed out; HTTP session closed anyway.", file=sys.stderr)


async def main() -> int:
    _quiet_discord_loggers()
    cfg = get_config()

    print("Loading slash commands from code…", flush=True)
    registered = bot.tree.get_commands()
    print(f"Found {len(registered)} command(s).", flush=True)

    print("Logging in to Discord…", flush=True)
    await bot.login(cfg.discord_token)

    synced: list = []
    try:
        print("Uploading command definitions (this can take 10–30s)…", flush=True)
        if cfg.guild_id:
            guild = discord.Object(id=int(cfg.guild_id))
            bot.tree.copy_global_to(guild=guild)
            synced = await asyncio.wait_for(
                bot.tree.sync(guild=guild),
                timeout=SYNC_TIMEOUT_SECONDS,
            )
            print(
                f"\nSynced {len(synced)} command(s) to guild {cfg.guild_id} "
                "(should appear in that server immediately).",
                flush=True,
            )
        else:
            synced = await asyncio.wait_for(
                bot.tree.sync(),
                timeout=SYNC_TIMEOUT_SECONDS,
            )
            print(
                f"\nSynced {len(synced)} command(s) globally "
                "(may take up to ~1 hour to propagate everywhere).",
                flush=True,
            )
        for cmd in sorted(synced, key=lambda c: c.name):
            print(f"  /{cmd.name}", flush=True)
    except asyncio.TimeoutError:
        print(
            f"\nSync timed out after {SYNC_TIMEOUT_SECONDS}s. "
            "Check network/firewall or try again; stop the main bot if it is running.",
            file=sys.stderr,
        )
        return 1
    except discord.Forbidden:
        print(
            "\nGuild sync failed (403 Forbidden).\n"
            "Fix: re-invite the bot with the applications.commands scope, or verify GUILD_ID "
            "is your server's numeric ID (Developer Mode → right-click server → Copy Server ID).",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"\nSync failed: {exc!r}", file=sys.stderr)
        return 1
    finally:
        print("Closing connection…", flush=True)
        await _shutdown_bot()

    if len(synced) == 0:
        print("\nWarning: Discord accepted 0 commands. Check DISCORD_TOKEN and bot code.", file=sys.stderr)
        return 1
    print("\nDone.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
