#!/usr/bin/env python3
"""
J-Quants statements fetcher for TSE_EventBase.

Fetches financial statement summaries from the J-Quants V2 API
(``/fins/summary``) and stores them, one row per disclosure, in the
``jquants_statements`` table. Idempotent via a unique index on the J-Quants
disclosure id (``DiscNo``) — re-runs skip rows already stored.

This is the raw-data layer for Stage 2. The derived "beat vs. prior forecast"
signal is computed separately by ``classifier_v2/stage2_financial.py``.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config import DB_PATH

logger = logging.getLogger(__name__)


# V2 /fins/summary abbreviated field -> jquants_statements column.
# (Verbose V1 equivalents are documented in db/schema.sql.)
COLUMN_MAP = {
    "DiscNo": "disclosure_no",
    "Code": "local_code",
    "DiscDate": "disclosed_date",
    "DiscTime": "disclosed_time",
    "DocType": "doc_type",
    "CurPerType": "period_type",
    "CurFYEn": "current_fy_end",
    "Sales": "net_sales",
    "OP": "operating_profit",
    "OdP": "ordinary_profit",
    "NP": "profit",
    "EPS": "eps",
    "TA": "total_assets",
    "Eq": "equity",
    "BPS": "bps",
    "FSales": "forecast_net_sales",
    "FOP": "forecast_operating_profit",
    "FOdP": "forecast_ordinary_profit",
    "FNP": "forecast_profit",
    "FEPS": "forecast_eps",
    "DivAnn": "result_dps_annual",
    "FDivAnn": "forecast_dps_annual",
}

# DB insert column order (id + created_at are auto-populated).
INSERT_COLS = [
    "disclosure_no", "local_code", "ticker", "disclosed_date", "disclosed_time",
    "doc_type", "period_type", "current_fy_end",
    "net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
    "total_assets", "equity", "bps",
    "forecast_net_sales", "forecast_operating_profit", "forecast_ordinary_profit",
    "forecast_profit", "forecast_eps",
    "result_dps_annual", "forecast_dps_annual", "raw_json",
]

# Columns that should be parsed as numbers (the rest are text/date).
_NUMERIC_DB_COLS = {
    "net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
    "total_assets", "equity", "bps",
    "forecast_net_sales", "forecast_operating_profit", "forecast_ordinary_profit",
    "forecast_profit", "forecast_eps",
    "result_dps_annual", "forecast_dps_annual",
}

# Tokens J-Quants uses for "no value".
_NULL_TOKENS = {"", "-", "－", "—", "nan", "NaN", "None", "NaT"}


def _num(v) -> Optional[float]:
    """Coerce a J-Quants cell (often a numeric string) to float or None."""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    if s in _NULL_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _str(v) -> Optional[str]:
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s if s and s not in _NULL_TOKENS else None


def _date(v) -> Optional[str]:
    """Normalize a date cell (pandas Timestamp or string) to 'YYYY-MM-DD'."""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d")
        except Exception:
            return None
    s = str(v).strip()
    if not s or s in _NULL_TOKENS:
        return None
    return s[:10]


def _json_safe(row: dict) -> dict:
    """Convert a DataFrame record into a JSON-serializable dict (full fidelity)."""
    out = {}
    for k, v in row.items():
        try:
            if pd.isna(v):
                out[k] = None
                continue
        except (TypeError, ValueError):
            pass
        if hasattr(v, "strftime"):
            try:
                out[k] = v.strftime("%Y-%m-%d")
            except Exception:
                out[k] = str(v)
        elif isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _month_chunks(start_date: str, end_date: str):
    """Yield (chunk_start, chunk_end) 'YYYY-MM-DD' pairs, one per calendar month.

    Used to group day-by-day fetching for progress logging and per-month DB flushes.
    """
    s = datetime.strptime(start_date, "%Y-%m-%d")
    e = datetime.strptime(end_date, "%Y-%m-%d")
    if e < s:
        return
    y, m = s.year, s.month
    while (y, m) <= (e.year, e.month):
        cs = s if (y == s.year and m == s.month) else datetime(y, m, 1)
        nm = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
        last = nm - timedelta(days=1)
        ce = e if (y == e.year and m == e.month) else last
        yield cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


class JQuantsStatementsFetcher:
    """Fetches /fins/summary into the jquants_statements table."""

    def __init__(self, db_path: str = DB_PATH, client=None,
                 api_key: Optional[str] = None, max_workers: Optional[int] = None):
        self.db_path = db_path
        self._client = client
        self._api_key = api_key
        self._max_workers = max_workers

    @property
    def client(self):
        """Lazily build a JQuantsClient (so --dry-run never needs the API key)."""
        if self._client is None:
            from jquants.client import JQuantsClient
            self._client = JQuantsClient(api_key=self._api_key,
                                         max_workers=self._max_workers)
        return self._client

    def _parse_row(self, row: dict) -> Optional[tuple]:
        """Map one /fins/summary record to an INSERT tuple, or None to skip."""
        disclosure_no = _str(row.get("DiscNo"))
        local_code = _str(row.get("Code"))
        if not disclosure_no or not local_code:
            return None

        values = {
            "disclosure_no": disclosure_no,
            "local_code": local_code,
            "ticker": local_code[:4],
            "disclosed_date": _date(row.get("DiscDate")),
            "disclosed_time": _str(row.get("DiscTime")),
            "doc_type": _str(row.get("DocType")),
            "period_type": _str(row.get("CurPerType")),
            "current_fy_end": _date(row.get("CurFYEn")),
            "raw_json": json.dumps(_json_safe(row), ensure_ascii=False),
        }
        # Numeric financial fields
        for src, dst in COLUMN_MAP.items():
            if dst in _NUMERIC_DB_COLS:
                values[dst] = _num(row.get(src))

        return tuple(values[c] for c in INSERT_COLS)

    def _insert_batch(self, conn: sqlite3.Connection, rows: list) -> int:
        if not rows:
            return 0
        before = conn.total_changes
        placeholders = ", ".join("?" for _ in INSERT_COLS)
        conn.executemany(
            f"INSERT OR IGNORE INTO jquants_statements "
            f"({', '.join(INSERT_COLS)}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
        return conn.total_changes - before

    def _call_day(self, yyyymmdd: str, retry_waits: tuple) -> Optional[pd.DataFrame]:
        """Fetch one disclosure date from the API with adaptive 429 backoff.

        Returns the DataFrame (possibly empty) on success, or None if the date is
        outside the subscription window or still failing after all retries.
        """
        for attempt in range(len(retry_waits) + 1):
            try:
                return self.client.fin_summary(date_yyyymmdd=yyyymmdd)
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
                logger.error("fetch failed %s: %s", yyyymmdd, msg[:120])
                return None
        return None

    def _fetch_day(self, yyyymmdd: str, cache_dir: str, pace: float,
                   retry_waits: tuple) -> tuple:
        """Return (df_or_None, was_api_call). Reuses the per-day gz cache if present."""
        cache_path = ""
        if cache_dir:
            cache_path = os.path.join(cache_dir, yyyymmdd[:4],
                                      f"v2_fin_summary_{yyyymmdd}.csv.gz")
            if os.path.isfile(cache_path):
                try:
                    return pd.read_csv(cache_path, dtype=str), False
                except Exception:
                    pass  # corrupt/empty cache → refetch

        df = self._call_day(yyyymmdd, retry_waits)

        if cache_path and df is not None and not df.empty:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                df.to_csv(cache_path, index=False)
            except Exception as e:
                logger.debug("cache write failed %s: %s", yyyymmdd, e)

        if pace > 0:
            time.sleep(pace)
        return df, True

    def fetch_date_range(self, start_date: str, end_date: str,
                         cache_dir: str = "", dry_run: bool = False,
                         pace: float = 0.5,
                         retry_waits: tuple = (15, 30, 60, 120)) -> dict:
        """Fetch /fins/summary day-by-day for [start_date, end_date].

        Requests one disclosure-date at a time, paced by ``pace`` seconds with
        exponential 429 backoff (``retry_waits``), to respect the J-Quants
        per-window rate limit. Weekends are skipped (no disclosures). Each
        non-empty day is cached as gz so re-runs are cheap; DB inserts use
        INSERT OR IGNORE, so the whole run is idempotent and resumable.
        """
        months = list(_month_chunks(start_date, end_date))
        if not months:
            logger.warning("Empty date range: %s .. %s", start_date, end_date)
            return {"fetched": 0, "inserted": 0, "dry_run": dry_run}

        if dry_run:
            logger.info("[dry-run] would fetch %d month(s) day-by-day (%s .. %s); "
                        "no API calls, no DB writes", len(months), start_date, end_date)
            return {"fetched": 0, "inserted": 0, "months": len(months), "dry_run": True}

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -64000;")

        total_fetched = 0
        total_inserted = 0
        api_calls = 0
        cache_hits = 0
        try:
            for cs, ce in months:
                day = datetime.strptime(cs, "%Y-%m-%d")
                last = datetime.strptime(ce, "%Y-%m-%d")
                month_rows = []
                month_fetched = 0
                while day <= last:
                    if day.weekday() < 5:  # Mon-Fri; disclosures don't post on weekends
                        ymd = day.strftime("%Y%m%d")
                        df, was_api = self._fetch_day(ymd, cache_dir, pace, retry_waits)
                        api_calls += 1 if was_api else 0
                        cache_hits += 0 if was_api else 1
                        if df is not None and not df.empty:
                            month_fetched += len(df)
                            for rec in df.to_dict(orient="records"):
                                parsed = self._parse_row(rec)
                                if parsed:
                                    month_rows.append(parsed)
                    day += timedelta(days=1)

                inserted = self._insert_batch(conn, month_rows)
                total_fetched += month_fetched
                total_inserted += inserted
                logger.info("  %s: fetched %d rows, inserted %d new "
                            "(running: api=%d cache=%d)",
                            cs[:7], month_fetched, inserted, api_calls, cache_hits)
        finally:
            conn.close()

        logger.info("J-Quants fetch complete. Fetched %d rows, inserted %d new "
                    "(api_calls=%d, cache_hits=%d).",
                    total_fetched, total_inserted, api_calls, cache_hits)
        return {"fetched": total_fetched, "inserted": total_inserted,
                "api_calls": api_calls, "cache_hits": cache_hits, "dry_run": False}
