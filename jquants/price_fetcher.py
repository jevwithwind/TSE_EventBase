#!/usr/bin/env python3
"""
J-Quants daily price fetcher for TSE_EventBase.

Fetches split-adjusted daily OHLCV from the J-Quants V2 API
(``/equities/bars/daily``) into the ``prices`` table, restricted to the tickers
that have a J-Quants financial disclosure (the "event tickers").

Mirrors the statements fetcher: day-by-day, paced with exponential 429 backoff,
per-day gz cache, weekends skipped, idempotent via INSERT OR IGNORE on the
``(ticker, date)`` primary key — so it is resumable and re-runs are cheap.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config import DB_PATH
# Reuse the month iterator from the statements fetcher.
from jquants.statements_fetcher import _month_chunks

logger = logging.getLogger(__name__)

# Raw V2 daily-bars columns we keep (abbreviated names).
RAW_COLS = ["Date", "Code", "O", "H", "L", "C", "AdjC", "Vo"]
# prices table column order (PRIMARY KEY (ticker, date)).
INSERT_COLS = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]


class JQuantsPriceFetcher:
    """Fetches /equities/bars/daily into the prices table for event tickers."""

    def __init__(self, db_path: str = DB_PATH, client=None, api_key: Optional[str] = None):
        self.db_path = db_path
        self._client = client
        self._api_key = api_key
        self._tickers: Optional[set] = None

    @property
    def client(self):
        if self._client is None:
            from jquants.client import JQuantsClient
            self._client = JQuantsClient(api_key=self._api_key)
        return self._client

    def event_tickers(self) -> set:
        """4-digit tickers that have at least one J-Quants disclosure."""
        if self._tickers is None:
            conn = sqlite3.connect(self.db_path)
            try:
                self._tickers = {
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT ticker FROM jquants_statements "
                        "WHERE ticker IS NOT NULL"
                    )
                }
            finally:
                conn.close()
        return self._tickers

    def _call_day(self, yyyymmdd: str, retry_waits: tuple) -> Optional[pd.DataFrame]:
        """Fetch one date's quotes with adaptive 429 backoff; None on skip/fail."""
        for attempt in range(len(retry_waits) + 1):
            try:
                return self.client.eq_bars_daily(date_yyyymmdd=yyyymmdd)
            except Exception as e:
                msg = str(e)
                if "subscription covers" in msg or " 400 " in msg:
                    logger.debug("out-of-window %s: %s", yyyymmdd, msg[:80])
                    return None
                if "429" in msg and attempt < len(retry_waits):
                    wait = retry_waits[attempt]
                    logger.warning("429 on %s — backing off %ds (retry %d/%d)",
                                   yyyymmdd, wait, attempt + 1, len(retry_waits))
                    time.sleep(wait)
                    continue
                logger.error("price fetch failed %s: %s", yyyymmdd, msg[:120])
                return None
        return None

    def _fetch_day(self, yyyymmdd: str, cache_dir: str, pace: float,
                   retry_waits: tuple) -> tuple:
        """Return (df_or_None, was_api_call). Reuses the per-day gz cache if present."""
        cache_path = ""
        if cache_dir:
            cache_path = os.path.join(cache_dir, yyyymmdd[:4], f"v2_daily_{yyyymmdd}.csv.gz")
            if os.path.isfile(cache_path):
                try:
                    return pd.read_csv(cache_path, dtype=str), False
                except Exception:
                    pass  # corrupt cache → refetch

        df = self._call_day(yyyymmdd, retry_waits)

        if cache_path and df is not None and not df.empty:
            try:
                keep = [c for c in RAW_COLS if c in df.columns]
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                df[keep].to_csv(cache_path, index=False)
            except Exception as e:
                logger.debug("price cache write failed %s: %s", yyyymmdd, e)

        if pace > 0:
            time.sleep(pace)
        return df, True

    def _rows_from_df(self, df, tickers: set) -> list:
        """Map a day's quotes to prices rows, keeping only event tickers (vectorized).

        Pandas-vectorized rather than a per-row loop — ~10x faster for the
        ~4,000 rows returned per trading day.
        """
        if df is None or df.empty or "Code" not in df.columns or "Date" not in df.columns:
            return []
        d = df.loc[:, [c for c in RAW_COLS if c in df.columns]].copy()
        d["ticker"] = d["Code"].astype(str).str.strip().str[:4]
        d = d[d["ticker"].isin(tickers)]
        if d.empty:
            return []
        d["date"] = pd.to_datetime(d["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        d = d[d["date"].notna()]
        if d.empty:
            return []
        for src, dst in (("O", "open"), ("H", "high"), ("L", "low"),
                         ("C", "close"), ("AdjC", "adj_close"), ("Vo", "volume")):
            d[dst] = pd.to_numeric(d[src], errors="coerce") if src in d.columns else None
        sub = d[["ticker", "date", "open", "high", "low",
                 "close", "adj_close", "volume"]].astype(object)
        sub = sub.where(pd.notna(sub), None)
        return list(sub.itertuples(index=False, name=None))

    def _insert_batch(self, conn: sqlite3.Connection, rows: list) -> int:
        if not rows:
            return 0
        before = conn.total_changes
        placeholders = ", ".join("?" for _ in INSERT_COLS)
        conn.executemany(
            f"INSERT OR IGNORE INTO prices ({', '.join(INSERT_COLS)}) "
            f"VALUES ({placeholders})",
            rows,
        )
        conn.commit()
        return conn.total_changes - before

    def fetch_date_range(self, start_date: str, end_date: str,
                         cache_dir: str = "", dry_run: bool = False,
                         pace: float = 0.5,
                         retry_waits: tuple = (15, 30, 60, 120)) -> dict:
        """Fetch daily quotes day-by-day for [start_date, end_date], event tickers only."""
        months = list(_month_chunks(start_date, end_date))
        if not months:
            logger.warning("Empty date range: %s .. %s", start_date, end_date)
            return {"fetched": 0, "inserted": 0, "dry_run": dry_run}

        if dry_run:
            logger.info("[dry-run] would fetch %d month(s) of daily prices (%s .. %s); "
                        "no API calls, no DB writes", len(months), start_date, end_date)
            return {"fetched": 0, "inserted": 0, "months": len(months), "dry_run": True}

        tickers = self.event_tickers()
        logger.info("Restricting prices to %d event tickers", len(tickers))
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -64000;")

        total_kept = 0
        total_inserted = 0
        api_calls = 0
        cache_hits = 0
        try:
            for cs, ce in months:
                day = datetime.strptime(cs, "%Y-%m-%d")
                last = datetime.strptime(ce, "%Y-%m-%d")
                month_rows = []
                while day <= last:
                    if day.weekday() < 5:
                        ymd = day.strftime("%Y%m%d")
                        df, was_api = self._fetch_day(ymd, cache_dir, pace, retry_waits)
                        api_calls += 1 if was_api else 0
                        cache_hits += 0 if was_api else 1
                        month_rows.extend(self._rows_from_df(df, tickers))
                    day += timedelta(days=1)

                inserted = self._insert_batch(conn, month_rows)
                total_kept += len(month_rows)
                total_inserted += inserted
                logger.info("  %s: kept %d rows, inserted %d new (running: api=%d cache=%d)",
                            cs[:7], len(month_rows), inserted, api_calls, cache_hits)
        finally:
            conn.close()

        logger.info("J-Quants price fetch complete. Kept %d rows, inserted %d new "
                    "(api_calls=%d, cache_hits=%d).",
                    total_kept, total_inserted, api_calls, cache_hits)
        return {"fetched": total_kept, "inserted": total_inserted,
                "api_calls": api_calls, "cache_hits": cache_hits, "dry_run": False}
