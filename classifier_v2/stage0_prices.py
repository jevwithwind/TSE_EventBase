"""
Stage 0: Overnight return classification (data-driven sentiment).

Classifies events using market reaction — the next-trading-day price change
after each disclosure — as a data-driven sentiment signal.  Events with
available price data get direction/magnitude/confidence from returns;
events without price data stay unclassified for the LLM (Stage 1).

Architecture (three-stage thesis pipeline):
  Stage 0 (this):  market-implied sentiment from overnight returns
  Stage 1:         LLM headline classification for remaining events
  Stage 2 (future): EDINET XBRL fundamental data for ground-truth

Thresholds (adjusted for Japanese large-cap daily volatility ~1.5%):
  > +2.0%  → positive / large
  > +0.5%  → positive / medium
  ±0.5%    → neutral / small
  < -0.5%  → negative / medium
  < -2.0%  → negative / large

Usage:
    python stage0_prices.py --db ../data/tse_eventbase.db --dry-run
    python stage0_prices.py --db ../data/tse_eventbase.db --in-scope-only
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------- Thresholds ----------

LARGE_THRESHOLD = 0.02   # ±2%
MEDIUM_THRESHOLD = 0.005  # ±0.5%


def classify_from_return(ret: float) -> tuple[str, str, str]:
    """Map overnight return to (direction, magnitude, confidence)."""
    abs_ret = abs(ret)

    if abs_ret >= LARGE_THRESHOLD:
        magnitude = "large"
    elif abs_ret >= MEDIUM_THRESHOLD:
        magnitude = "medium"
    else:
        magnitude = "small"

    if ret >= MEDIUM_THRESHOLD:
        direction = "positive"
    elif ret <= -MEDIUM_THRESHOLD:
        direction = "negative"
    else:
        direction = "neutral"

    confidence = "medium" if abs_ret >= MEDIUM_THRESHOLD else "low"
    return direction, magnitude, confidence


# ---------- Price helpers ----------

def _find_closest_date(
    target_date: str,
    available_dates: list[str],
    max_lookback: int = 5,
) -> str | None:
    """Find the nearest available date ≤ target_date within max_lookback days."""
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    for offset in range(max_lookback + 1):
        candidate = (target_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        if candidate in available_dates:
            return candidate
    return None


def _find_next_date(
    after_date: str,
    sorted_dates: list[str],
) -> str | None:
    """Find the first date > after_date in a sorted list."""
    for d in sorted_dates:
        if d > after_date:
            return d
    return None


# ---------- Main logic ----------

def run_stage0_prices(
    db_path: str,
    where_filter: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify events from overnight returns.  Returns summary stats."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Build event query
        base_where = (
            "e.classified_at IS NULL AND e.classification_failed_at IS NULL"
        )
        extra = f" AND ({where_filter})" if where_filter else ""
        order_limit = f" ORDER BY e.id LIMIT {limit}" if limit else ""

        rows = conn.execute(
            f"SELECT e.id, e.ticker, e.event_date, e.event_type, e.headline "
            f"FROM events e "
            f"WHERE {base_where} {extra} {order_limit}"
        ).fetchall()

        if not rows:
            logger.info("No events to process")
            return {"classified": 0, "skipped": 0, "total": 0}

        logger.info("Fetched %d unclassified events", len(rows))

        # Collect unique tickers
        tickers = sorted({r["ticker"] for r in rows})
        logger.info("Unique tickers: %d", len(tickers))

        # Load all prices for these tickers
        placeholders = ",".join("?" for _ in tickers)
        price_rows = conn.execute(
            f"SELECT ticker, date, close, adj_close "
            f"FROM prices WHERE ticker IN ({placeholders}) "
            f"ORDER BY ticker, date",
            tickers,
        ).fetchall()

        # Build lookup: ticker → sorted list of dates, and ticker → {date: adj_close}
        from collections import defaultdict
        ticker_dates: dict[str, list[str]] = defaultdict(list)
        ticker_prices: dict[str, dict[str, float]] = defaultdict(dict)

        for pr in price_rows:
            ticker_dates[pr["ticker"]].append(pr["date"])
            close_val = pr["adj_close"] if pr["adj_close"] else pr["close"]
            if close_val and close_val > 0:
                ticker_prices[pr["ticker"]][pr["date"]] = close_val

        # Convert dates to sets for fast lookups
        ticker_date_sets = {
            t: set(dates) for t, dates in ticker_dates.items()
        }
        logger.info("Loaded prices for %d tickers (%d rows)",
                     len(ticker_dates), len(price_rows))

        # Classify each event
        classified = 0
        skipped_no_price = 0
        skipped_no_next = 0
        updates: list[tuple] = []

        for r in rows:
            ev_id = r["id"]
            ticker = r["ticker"]
            event_date = r["event_date"]
            keyword_et = r["event_type"]

            dates_list = ticker_dates.get(ticker)
            dates_set = ticker_date_sets.get(ticker)
            price_map = ticker_prices.get(ticker)

            if not dates_list or not price_map:
                skipped_no_price += 1
                continue

            # Find event-day close (nearest prior trading day)
            event_close_date = _find_closest_date(event_date, dates_set)
            if event_close_date is None:
                skipped_no_price += 1
                continue

            close_today = price_map.get(event_close_date)
            if close_today is None or close_today <= 0:
                skipped_no_price += 1
                continue

            # Find next trading day close
            next_date = _find_next_date(event_close_date, dates_list)
            if next_date is None:
                skipped_no_next += 1
                continue

            close_next = price_map.get(next_date)
            if close_next is None or close_next <= 0:
                skipped_no_next += 1
                continue

            # Compute overnight return
            ret = (close_next - close_today) / close_today

            # Cap extreme returns (likely data errors, stock splits, etc.)
            if abs(ret) > 0.50:
                skipped_no_price += 1
                continue

            direction, magnitude, confidence = classify_from_return(ret)

            updates.append((
                direction, magnitude, confidence,
                keyword_et,
                f"overnight return {ret:+.2%} "
                f"(close {close_today:.0f} → {close_next:.0f})",
                ev_id,
            ))

            classified += 1

        logger.info(
            "Classified: %d  Skipped (no price): %d  Skipped (no next): %d",
            classified, skipped_no_price, skipped_no_next,
        )

        # Write to DB
        if not dry_run and updates:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE events SET "
                "ai_direction = ?, "
                "ai_magnitude = ?, "
                "ai_confidence = ?, "
                "ai_event_type = ?, "
                "ai_headline_en = '(price-based)', "
                "ai_summary = ?, "
                "ai_event_subtype = 'price_implied', "
                "classified_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                updates,
            )
            conn.commit()
            logger.info("Wrote %d classifications to DB", len(updates))
        elif dry_run:
            logger.info("DRY RUN — no changes written")
            # Show distribution
            from collections import Counter
            dirs = Counter(u[0] for u in updates)
            mags = Counter(u[1] for u in updates)
            confs = Counter(u[2] for u in updates)
            logger.info("Direction: %s", dict(dirs))
            logger.info("Magnitude: %s", dict(mags))
            logger.info("Confidence: %s", dict(confs))

        return {
            "classified": classified,
            "skipped_no_price": skipped_no_price,
            "skipped_no_next": skipped_no_next,
            "total": len(rows),
            "dry_run": dry_run,
        }

    finally:
        conn.close()


# ---------- CLI ----------

def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    p = argparse.ArgumentParser(
        description="Stage 0: classify events from overnight returns"
    )
    p.add_argument("--db", required=True, help="Path to eventbase.db")
    p.add_argument("--limit", type=int, default=None,
                   help="Max events to process (for smoke testing)")
    p.add_argument("--filter", default=None,
                   help="Extra SQL WHERE clause")
    p.add_argument("--in-scope-only", action="store_true",
                   help="Only sentiment-relevant event types")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute and report without writing to DB")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    IN_SCOPE = (
        "event_type IN ('earnings','forecast_revision','dividend',"
        "'buyback','ma','tender_offer')"
    )
    where_filter = args.filter
    if args.in_scope_only:
        where_filter = IN_SCOPE
        logger.info("Using in-scope filter")

    result = run_stage0_prices(
        db_path=args.db,
        where_filter=where_filter,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("Result: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
