"""Reset all player activity cooldowns in the local database."""

from sqlalchemy import text

from src.db import get_engine


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        players = conn.execute(
            text(
                """
                UPDATE players SET
                    last_cultivate_at = NULL,
                    last_daily_at = NULL,
                    last_daily_streak_claimed_at = NULL,
                    last_pvp_at = NULL,
                    last_adventure_at = NULL,
                    last_dungeon_at = NULL,
                    last_gather_at = NULL,
                    last_hunt_at = NULL,
                    spirit_root_last_reroll_at = NULL,
                    sect_leave_cooldown_until = NULL
                """
            )
        )
        haste = conn.execute(
            text(
                """
                DELETE FROM player_effects
                WHERE effect_id LIKE 'haste_%' OR effect_id = 'void_pulse'
                """
            )
        )
        reminders = conn.execute(
            text(
                """
                UPDATE player_reminders
                SET ready_at = NULL, sent_at = NULL
                """
            )
        )
        conn.commit()
        count = conn.execute(text("SELECT COUNT(*) FROM players")).scalar()
        print(f"Reset cooldowns for {count} player(s).")
        print(f"Removed {haste.rowcount} haste effect row(s).")
        print(f"Cleared {reminders.rowcount} reminder timer row(s).")
        print(f"Updated {players.rowcount} player row(s).")


if __name__ == "__main__":
    main()
