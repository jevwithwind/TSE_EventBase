#!/usr/bin/env python3
"""
Export the quantifiable J-Quants events — financial-results disclosures that
carry a data-driven beat-vs-forecast signal — with full financials and price
context. This is the deliverable for the J-Quants-native pipeline: only events
that J-Quants can quantify (a data-driven signal exists) are included.

Pipeline:
  jquants_statements (events + financials)
        + Stage-2 signal (reused from classifier_v2/stage2_financial.py)
        + tickers (company name / sector, from /listed/info)
        + prices (event-day close, next-day overnight return)
        -> data/exports/quantifiable_events.{csv,parquet}

Usage:
    python export_enriched_events.py
    python export_enriched_events.py --format csv
    python export_enriched_events.py --start-date 2016-06-08 --end-date 2025-12-31
"""

import sys
import os
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "classifier_v2"))

import argparse
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

from config import DB_PATH, EXPORT_DIR
import stage2_financial as s2
import stage0_prices as s0

logger = logging.getLogger(__name__)

# Full statement columns carried into the export (raw J-Quants financials).
STMT_COLS = [
    "id", "disclosure_no", "local_code", "ticker", "disclosed_date", "disclosed_time",
    "doc_type", "period_type", "current_fy_end",
    "net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
    "total_assets", "equity", "bps",
    "forecast_net_sales", "forecast_operating_profit", "forecast_ordinary_profit",
    "forecast_profit", "forecast_eps",
    "result_dps_annual", "forecast_dps_annual",
]

# Final export column order.
OUTPUT_COLS = [
    # identity
    "ticker", "local_code", "company_name", "company_name_en", "sector",
    "market_segment", "disclosed_date", "disclosed_time", "period_type",
    "doc_type", "current_fy_end", "disclosure_no",
    # data-driven signal (beat vs. prior forecast)
    "signal_direction", "signal_magnitude", "signal_surprise_pct", "signal_basis",
    "signal_metric", "signal_actual", "signal_forecast",
    # actual results
    "net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
    "total_assets", "equity", "bps",
    # company forecast (current FY)
    "forecast_net_sales", "forecast_operating_profit", "forecast_ordinary_profit",
    "forecast_profit", "forecast_eps",
    # dividends
    "result_dps_annual", "forecast_dps_annual",
    # price context (split-adjusted)
    "event_close", "next_close", "overnight_return",
]


def _load_prices(conn, tickers):
    """Return (ticker->sorted dates, ticker->date set, ticker->{date: adj_close})."""
    ticker_dates = defaultdict(list)
    ticker_prices = defaultdict(dict)
    if not tickers:
        return ticker_dates, {}, ticker_prices
    placeholders = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"SELECT ticker, date, close, adj_close FROM prices "
        f"WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
        list(tickers),
    ).fetchall()
    for t, d, close, adj in rows:
        ticker_dates[t].append(d)
        v = adj if adj else close
        if v and v > 0:
            ticker_prices[t][d] = v
    ticker_date_sets = {t: set(ds) for t, ds in ticker_dates.items()}
    return ticker_dates, ticker_date_sets, ticker_prices


def _price_context(ticker, event_date, dates, dsets, pmap):
    """(event_close, next_close, overnight_return) using nearest prior trading day."""
    dlist = dates.get(ticker)
    dset = dsets.get(ticker)
    pm = pmap.get(ticker)
    if not dlist or not pm:
        return None, None, None
    ecd = s0._find_closest_date(event_date, dset)
    if not ecd:
        return None, None, None
    close_e = pm.get(ecd)
    nd = s0._find_next_date(ecd, dlist)
    close_n = pm.get(nd) if nd else None
    ret = None
    if close_e and close_n and close_e > 0:
        ret = (close_n - close_e) / close_e
    return close_e, close_n, ret


def run_export(db_path: str, out_dir: str, fmt: str,
               start_date: str | None, end_date: str | None) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 1) Compute the data-driven signal for every statement (reuse Stage 2).
        by_ticker = s2.load_statements(conn)
        eps_sig, dps_sig, _ = s2.compute_all_signals(by_ticker)
        logger.info("Signals computed: %d EPS, %d DPS", len(eps_sig), len(dps_sig))

        # 2) Company metadata.
        tinfo = {}
        for r in conn.execute(
            "SELECT ticker, company_name, company_name_en, sector, market_segment FROM tickers"
        ).fetchall():
            tinfo[r["ticker"]] = r
        logger.info("Ticker metadata rows: %d", len(tinfo))

        # 3) Full statement rows.
        stmt_rows = conn.execute(
            f"SELECT {', '.join(STMT_COLS)} FROM jquants_statements"
        ).fetchall()

        # Which tickers do we need prices for? (those with a quantifiable signal)
        signal_tickers = set()
        for r in stmt_rows:
            if r["id"] in eps_sig or r["id"] in dps_sig:
                signal_tickers.add(r["ticker"])
        dates, dsets, pmap = _load_prices(conn, signal_tickers)
        logger.info("Loaded prices for %d tickers", len(pmap))

        records = []
        for r in stmt_rows:
            sig = eps_sig.get(r["id"]) or dps_sig.get(r["id"])
            if sig is None:
                continue  # not quantifiable
            d = r["disclosed_date"]
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue

            meta = tinfo.get(r["ticker"])
            ec, nc, ret = _price_context(r["ticker"], d, dates, dsets, pmap)

            rec = {
                "ticker": r["ticker"],
                "local_code": r["local_code"],
                "company_name": meta["company_name"] if meta else None,
                "company_name_en": meta["company_name_en"] if meta else None,
                "sector": meta["sector"] if meta else None,
                "market_segment": meta["market_segment"] if meta else None,
                "disclosed_date": d,
                "disclosed_time": r["disclosed_time"],
                "period_type": r["period_type"],
                "doc_type": r["doc_type"],
                "current_fy_end": r["current_fy_end"],
                "disclosure_no": r["disclosure_no"],
                "signal_direction": sig.direction,
                "signal_magnitude": sig.magnitude,
                "signal_surprise_pct": sig.surprise_pct,
                "signal_basis": sig.basis,
                "signal_metric": sig.metric,
                "signal_actual": sig.actual,
                "signal_forecast": sig.forecast,
                "net_sales": r["net_sales"],
                "operating_profit": r["operating_profit"],
                "ordinary_profit": r["ordinary_profit"],
                "profit": r["profit"],
                "eps": r["eps"],
                "total_assets": r["total_assets"],
                "equity": r["equity"],
                "bps": r["bps"],
                "forecast_net_sales": r["forecast_net_sales"],
                "forecast_operating_profit": r["forecast_operating_profit"],
                "forecast_ordinary_profit": r["forecast_ordinary_profit"],
                "forecast_profit": r["forecast_profit"],
                "forecast_eps": r["forecast_eps"],
                "result_dps_annual": r["result_dps_annual"],
                "forecast_dps_annual": r["forecast_dps_annual"],
                "event_close": ec,
                "next_close": nc,
                "overnight_return": ret,
            }
            records.append(rec)

        df = pd.DataFrame(records, columns=OUTPUT_COLS)
        df.sort_values(["disclosed_date", "ticker"], inplace=True)
        logger.info("Quantifiable events: %d (%s .. %s)",
                    len(df),
                    df["disclosed_date"].min() if len(df) else "-",
                    df["disclosed_date"].max() if len(df) else "-")

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        written = []
        if fmt in ("csv", "both"):
            p = out / "quantifiable_events.csv"
            df.to_csv(p, index=False, encoding="utf-8-sig")
            written.append(str(p))
        if fmt in ("parquet", "both"):
            p = out / "quantifiable_events.parquet"
            try:
                df.to_parquet(p, index=False)
                written.append(str(p))
            except Exception as e:
                logger.warning("Parquet write failed (%s); install pyarrow for Parquet.", e)

        # Quick distribution summary
        if len(df):
            with_price = int(df["overnight_return"].notna().sum())
            logger.info("Direction: %s", df["signal_direction"].value_counts().to_dict())
            logger.info("Basis: %s", df["signal_basis"].value_counts().to_dict())
            logger.info("Rows with price context: %d / %d", with_price, len(df))

        return {"rows": len(df), "written": written}
    finally:
        conn.close()


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    p = argparse.ArgumentParser(
        description="Export quantifiable J-Quants events (data-driven signal) with financials + prices."
    )
    p.add_argument("--db", default=DB_PATH, help="Path to eventbase.db")
    p.add_argument("--out-dir", default=EXPORT_DIR, help="Output directory (default data/exports)")
    p.add_argument("--format", choices=["csv", "parquet", "both"], default="both")
    p.add_argument("--start-date", default="2016-01-01", help="Min disclosed_date (YYYY-MM-DD)")
    p.add_argument("--end-date", default="2025-12-31", help="Max disclosed_date (YYYY-MM-DD)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    result = run_export(args.db, args.out_dir, args.format, args.start_date, args.end_date)
    logger.info("Result: %s", result)
    print(f"\nExported {result['rows']} quantifiable events to:")
    for w in result["written"]:
        print(f"  {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
