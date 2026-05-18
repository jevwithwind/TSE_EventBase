"""
migrate_db.py — Add new AI-classifier columns to the events table.

Idempotent: checks for existing columns before adding. Safe to run multiple times.

Usage:
    python migrate_db.py --db data/eventbase.db
    python migrate_db.py --db data/eventbase.db --dry-run    # show planned changes only
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("ai_event_type", "TEXT"),
    ("ai_event_subtype", "TEXT"),
    ("ai_direction", "TEXT"),
    ("ai_magnitude", "TEXT"),
    ("ai_confidence", "TEXT"),
    ("ai_headline_en", "TEXT"),
    ("ai_summary", "TEXT"),
    ("classification_failed_at", "TEXT"),
    ("classification_error", "TEXT"),
]


def existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}


def migrate(db_path: str, dry_run: bool = False) -> dict:
    from pathlib import Path
    if not Path(db_path).exists():
        raise RuntimeError(
            f"DB file does not exist: {db_path}. "
            "Refusing to create an empty database. "
            "Check the path — common gotcha is filename (e.g., 'tse_eventbase.db' vs 'eventbase.db')."
        )

    conn = sqlite3.connect(db_path)
    try:
        # Verify events table exists before doing anything else
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "events" not in tables:
            raise RuntimeError(
                f"DB at {db_path} has no 'events' table. "
                f"Tables present: {sorted(tables) or '(none)'}. "
                "Wrong DB file?"
            )

        existing = existing_columns(conn)
        to_add = [(name, kind) for name, kind in NEW_COLUMNS if name not in existing]

        logger.info("Existing event columns: %d", len(existing))
        logger.info("Required new columns: %d", len(NEW_COLUMNS))
        logger.info("To add: %d", len(to_add))
        for name, kind in to_add:
            logger.info("  + %s %s", name, kind)

        if dry_run:
            logger.info("DRY RUN — no changes made.")
            return {"added": 0, "to_add": [n for n, _ in to_add], "dry_run": True}

        if not to_add:
            logger.info("Nothing to add. Schema is already up to date.")
            return {"added": 0, "to_add": [], "dry_run": False}

        for name, kind in to_add:
            sql = f"ALTER TABLE events ADD COLUMN {name} {kind}"
            logger.info("Executing: %s", sql)
            conn.execute(sql)
        conn.commit()

        # Verify
        after = existing_columns(conn)
        for name, _ in to_add:
            if name not in after:
                raise RuntimeError(f"Column {name} still missing after ALTER!")
        logger.info("Migration complete. %d columns added.", len(to_add))
        return {"added": len(to_add), "to_add": [n for n, _ in to_add], "dry_run": False}
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description="Add AI classifier columns to events table.")
    p.add_argument("--db", required=True, help="Path to eventbase.db SQLite file")
    p.add_argument("--dry-run", action="store_true", help="Show planned changes only")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        result = migrate(args.db, dry_run=args.dry_run)
        logger.info("Result: %s", result)
        return 0
    except Exception as e:
        logger.exception("Migration failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())