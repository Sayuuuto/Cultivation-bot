"""Force-register slash commands with Discord (no long-running bot).

Usage:
    py -m src.sync_commands

Use this after adding or changing commands. Guild sync (GUILD_ID in .env) is instant;
global sync can take up to ~1 hour to appear everywhere.
"""
from __future__ import annotations

import asyncio
import sys

import discord

from .bot import bot
from .config import get_config


async def main() -> int:
    cfg = get_config()
    registered = bot.tree.get_commands()
    print(f"Loaded {len(registered)} command(s) from code:")
    for cmd in sorted(registered, key=lambda c: c.name):
        print(f"  /{cmd.name}")

    await bot.login(cfg.discord_token)
    try:
        if cfg.guild_id:
            guild = discord.Object(id=int(cfg.guild_id))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"\nSynced {len(synced)} command(s) to guild {cfg.guild_id} (instant in that server).")
        else:
            synced = await bot.tree.sync()
            print(f"\nSynced {len(synced)} command(s) globally (may take up to ~1 hour).")
        for cmd in sorted(synced, key=lambda c: c.name):
            print(f"  /{cmd.name}")
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
        await bot.close()

    if len(synced) == 0:
        print("\nWarning: Discord accepted 0 commands. Check DISCORD_TOKEN and bot code.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
