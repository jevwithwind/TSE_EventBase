#!/usr/bin/env python3
"""
Fetch J-Quants financial statement summaries (/fins/summary, V2) into the
``jquants_statements`` table — the data source for Stage 2 financial enrichment.

Requires JQUANTS_API_KEY (see .env.example). Run the schema migration first:

    python jquants/migrate_db.py --db data/tse_eventbase.db

Examples:
    # One live call to inspect the columns the API actually returns:
    python run_jquants.py --probe

    # See what would be fetched (no API calls, no writes):
    python run_jquants.py --start-date 2016-01-01 --end-date 2025-12-31 --dry-run

    # Backfill (idempotent; cached per day under data/jquants_cache):
    python run_jquants.py --start-date 2016-01-01 --end-date 2025-12-31
"""

import argparse
import logging

from config import (
    SCRAPE_START_DATE, SCRAPE_END_DATE, JQUANTS_CACHE_DIR, DB_PATH,
)
from jquants.statements_fetcher import JQuantsStatementsFetcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# A handful of key columns to preview during --probe.
_PROBE_PREVIEW_COLS = [
    "DiscDate", "Code", "DiscNo", "CurPerType", "CurFYEn",
    "EPS", "FEPS", "NP", "FNP", "DivAnn", "FDivAnn",
]


def run_probe(api_key: str | None, code: str, date: str) -> int:
    """Make ONE live call and dump the columns + a small sample, then exit.

    This grounds the field mapping against what the API actually returns for
    your plan (no guessing from docs).
    """
    from jquants.client import JQuantsClient
    cli = JQuantsClient(api_key=api_key)
    if not code and not date:
        code = "72030"  # Toyota — always has statements history
    df = cli.fin_summary(code=code, date_yyyymmdd=date)

    if df is None or df.empty:
        print("Probe returned no rows. Try a different --probe-code or --probe-date.")
        return 1

    print(f"Probe OK: {len(df)} rows x {len(df.columns)} columns "
          f"(code={code or '-'}, date={date or '-'}).")
    print("\nAll columns:")
    print(list(df.columns))

    preview = [c for c in _PROBE_PREVIEW_COLS if c in df.columns]
    if preview:
        import pandas as pd
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print("\nSample (most recent disclosures):")
            print(df[preview].tail(8).to_string(index=False))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Fetch J-Quants /fins/summary into the jquants_statements table."
    )
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    parser.add_argument("--cache-dir", default=JQUANTS_CACHE_DIR,
                        help="Per-day CSV cache dir (speeds re-runs). Empty string disables.")
    parser.add_argument("--db", default=DB_PATH, help="Path to eventbase.db")
    parser.add_argument("--api-key", default=None, help="Override JQUANTS_API_KEY")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the yearly chunks without calling the API or writing.")
    parser.add_argument("--probe", action="store_true",
                        help="Make ONE live call and dump returned columns + sample, then exit.")
    parser.add_argument("--probe-code", default="", help="Stock code for --probe (default 72030).")
    parser.add_argument("--probe-date", default="", help="Disclosure date YYYYMMDD for --probe.")
    parser.add_argument("--pace", type=float, default=0.5,
                        help="Seconds to sleep between per-day API calls (default 0.5; "
                             "raise if you hit 429 rate limits).")
    args = parser.parse_args()

    if args.probe:
        return run_probe(args.api_key, args.probe_code, args.probe_date)

    fetcher = JQuantsStatementsFetcher(db_path=args.db, api_key=args.api_key)
    logger.info("Fetching J-Quants /fins/summary from %s to %s (day-by-day, pace=%.2fs)",
                args.start_date, args.end_date, args.pace)
    result = fetcher.fetch_date_range(
        args.start_date, args.end_date,
        cache_dir=args.cache_dir, dry_run=args.dry_run, pace=args.pace,
    )

    if args.dry_run:
        print(f"\nDry run: {result.get('months', 0)} month(s) would be fetched day-by-day. "
              f"No API calls made, no rows written.")
    else:
        print(f"\nJ-Quants fetch complete. Fetched {result['fetched']} rows, "
              f"inserted {result['inserted']} new into jquants_statements.")
    return 0


if __name__ == "__main__":
    exit(main())
