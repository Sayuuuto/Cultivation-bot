#!/usr/bin/env python3
"""One-time migration: tag legacy gear realm and map old quality grades to forge paths."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


LEGACY_GRADE_TO_PATH = {
    "common": "internal",
    "fine": "external",
    "exalted": "crit",
}


def migrate(db_path: Path, *, dry_run: bool = False) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(player_equipment)")
        columns = {row[1] for row in cur.fetchall()}
        if "gear_realm" not in columns:
            print("gear_realm column missing — run the app once to apply schema migrations.")
            return 1

        cur.execute(
            """
            SELECT id, item_id, gear_realm, gear_grade
            FROM player_equipment
            WHERE item_id IS NOT NULL
            """
        )
        rows = cur.fetchall()
        updated = 0
        for row_id, item_id, gear_realm, gear_grade in rows:
            mapped_grade = LEGACY_GRADE_TO_PATH.get(str(gear_grade or "").lower(), gear_grade or "external")
            new_realm = 0 if gear_realm is None else gear_realm
            needs_update = gear_realm is None or str(gear_grade or "") in LEGACY_GRADE_TO_PATH
            if not needs_update:
                continue
            if dry_run:
                updated += 1
                continue
            cur.execute(
                """
                UPDATE player_equipment
                SET gear_realm = ?, gear_grade = ?
                WHERE id = ?
                """,
                (new_realm, mapped_grade, row_id),
            )
            updated += cur.rowcount
        if not dry_run:
            conn.commit()
        print(f"{'Would update' if dry_run else 'Updated'} {updated} equipment row(s).")
        return 0
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy player equipment tiers.")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("deploy/seed/cultivation_bot.sqlite3"),
        help="Path to SQLite database",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(args.db, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
