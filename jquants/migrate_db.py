"""
migrate_db.py (J-Quants Stage 2) — create the jquants_statements table and add
the data_* enrichment columns to the events table.

Idempotent: checks before adding. Safe to run multiple times. Mirrors the
pattern of classifier_v2/migrate_db.py (which adds the ai_* columns).

The jquants_statements table is created from the canonical db/schema.sql, so
that file remains the single source of truth for table definitions.

Usage:
    python jquants/migrate_db.py --db data/tse_eventbase.db
    python jquants/migrate_db.py --db data/tse_eventbase.db --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Derived "beat vs. prior forecast" signal, written by stage2_financial.py.
DATA_COLUMNS = [
    ("data_direction", "TEXT"),        # positive / negative / neutral (beat / miss / in-line)
    ("data_magnitude", "TEXT"),        # large / medium / small
    ("data_surprise_pct", "REAL"),     # (actual - forecast) / |forecast|
    ("data_basis", "TEXT"),            # actual_vs_forecast | forecast_revision | dividend_*
    ("data_metric", "TEXT"),           # which metric drove it: eps | dps
    ("data_actual", "REAL"),           # the actual value used
    ("data_forecast", "REAL"),         # the forecast value compared against
    ("data_statement_id", "INTEGER"),  # FK -> jquants_statements.id (matched disclosure)
    ("data_enriched_at", "TIMESTAMP"), # set when Stage 2 processed this event
]

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def migrate(db_path: str, dry_run: bool = False) -> dict:
    if not Path(db_path).exists():
        raise RuntimeError(
            f"DB file does not exist: {db_path}. "
            "Refusing to create an empty database. Check the path."
        )

    conn = sqlite3.connect(db_path)
    try:
        tables = table_names(conn)
        if "events" not in tables:
            raise RuntimeError(
                f"DB at {db_path} has no 'events' table. "
                f"Tables present: {sorted(tables) or '(none)'}. Wrong DB file?"
            )

        existing = existing_columns(conn)
        cols_to_add = [(n, k) for n, k in DATA_COLUMNS if n not in existing]
        need_table = "jquants_statements" not in tables

        logger.info("jquants_statements present: %s", not need_table)
        logger.info("data_* columns to add: %d", len(cols_to_add))
        for n, k in cols_to_add:
            logger.info("  + %s %s", n, k)

        if dry_run:
            logger.info("DRY RUN — no changes made.")
            return {
                "added": 0,
                "to_add": [n for n, _ in cols_to_add],
                "create_table": need_table,
                "dry_run": True,
            }

        # Ensure jquants_statements (and any other missing tables) exist. All
        # statements in schema.sql are IF NOT EXISTS, so this never touches the
        # existing events/prices/financials/tickers tables or their data.
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)

        for n, k in cols_to_add:
            sql = f"ALTER TABLE events ADD COLUMN {n} {k}"
            logger.info("Executing: %s", sql)
            conn.execute(sql)
        conn.commit()

        # Verify
        after = existing_columns(conn)
        for n, _ in cols_to_add:
            if n not in after:
                raise RuntimeError(f"Column {n} still missing after ALTER!")
        if "jquants_statements" not in table_names(conn):
            raise RuntimeError("jquants_statements table missing after migration!")

        logger.info(
            "Migration complete. %d columns added; jquants_statements ensured.",
            len(cols_to_add),
        )
        return {
            "added": len(cols_to_add),
            "to_add": [n for n, _ in cols_to_add],
            "created_table": need_table,
            "dry_run": False,
        }
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(
        description="J-Quants Stage 2 schema migration "
                    "(jquants_statements table + events.data_* columns)."
    )
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
