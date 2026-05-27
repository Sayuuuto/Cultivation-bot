import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: str | None
    database_path: str
    announce_channel_id: str | None
    tutorial_channel_id: str | None
    library_channel_id: str | None
    abode_category_id: str | None

    # Game constants (MVP defaults)
    cultivate_cooldown_seconds: int = 15 * 60
    daily_cooldown_seconds: int = 24 * 60 * 60
    pvp_cooldown_seconds: int = 2 * 60 * 60

    # Realm structure
    offline_cap_minutes: int = 120

    # PvE cooldowns
    adventure_cooldown_seconds: int = 20 * 60
    dungeon_cooldown_seconds: int = 2 * 60 * 60
    gather_cooldown_seconds: int = 5 * 60
    hunt_cooldown_seconds: int = 5 * 60


def get_config() -> Config:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing DISCORD_TOKEN in environment (.env).")

    guild_id = os.getenv("GUILD_ID", "").strip() or None
    database_path = os.getenv("DATABASE_PATH", "cultivation_bot.sqlite3").strip()
    announce_channel_id = os.getenv("ANNOUNCE_CHANNEL_ID", "").strip() or None
    tutorial_channel_id = os.getenv("TUTORIAL_CHANNEL_ID", "").strip() or None
    library_channel_id = os.getenv("LIBRARY_CHANNEL_ID", "").strip() or None
    abode_category_id = os.getenv("ABODE_CATEGORY_ID", "").strip() or None

    return Config(
        discord_token=token,
        guild_id=guild_id,
        database_path=database_path,
        announce_channel_id=announce_channel_id,
        tutorial_channel_id=tutorial_channel_id,
        library_channel_id=library_channel_id,
        abode_category_id=abode_category_id,
    )

