#!/usr/bin/env python3
"""
Fetch split-adjusted daily prices from the J-Quants V2 API
(``/equities/bars/daily``) into the ``prices`` table, for every ticker that has
a J-Quants financial disclosure (the event tickers).

Requires JQUANTS_API_KEY (see .env.example). Fetch the statements first
(run_jquants.py) so the event-ticker set exists.

Examples:
    # See what would be fetched (no API calls, no writes):
    python run_jquants_prices.py --start-date 2016-06-08 --end-date 2025-12-31 --dry-run

    # Backfill (day-by-day, paced, cached under data/jquants_prices_cache):
    python run_jquants_prices.py --start-date 2016-06-08 --end-date 2025-12-31
    #   If you hit HTTP 429 rate limits, slow the pace: --pace 1.0
"""

import argparse
import logging

from config import (
    SCRAPE_START_DATE, SCRAPE_END_DATE, JQUANTS_PRICES_CACHE_DIR, DB_PATH,
)
from jquants.price_fetcher import JQuantsPriceFetcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch J-Quants daily prices into the prices table (event tickers only)."
    )
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    parser.add_argument("--cache-dir", default=JQUANTS_PRICES_CACHE_DIR,
                        help="Per-day CSV cache dir (speeds re-runs). Empty string disables.")
    parser.add_argument("--db", default=DB_PATH, help="Path to eventbase.db")
    parser.add_argument("--api-key", default=None, help="Override JQUANTS_API_KEY")
    parser.add_argument("--pace", type=float, default=0.5,
                        help="Seconds to sleep between per-day API calls (default 0.5; "
                             "raise if you hit 429 rate limits).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List the months without calling the API or writing.")
    args = parser.parse_args()

    fetcher = JQuantsPriceFetcher(db_path=args.db, api_key=args.api_key)
    logger.info("Fetching J-Quants daily prices from %s to %s (day-by-day, pace=%.2fs)",
                args.start_date, args.end_date, args.pace)
    result = fetcher.fetch_date_range(
        args.start_date, args.end_date,
        cache_dir=args.cache_dir, dry_run=args.dry_run, pace=args.pace,
    )

    if args.dry_run:
        print(f"\nDry run: {result.get('months', 0)} month(s) would be fetched day-by-day. "
              f"No API calls made, no rows written.")
    else:
        print(f"\nJ-Quants price fetch complete. Kept {result['fetched']} rows, "
              f"inserted {result['inserted']} new into prices.")
    return 0


if __name__ == "__main__":
    exit(main())
