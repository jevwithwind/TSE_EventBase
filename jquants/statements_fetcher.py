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
from datetime import datetime
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


def _year_chunks(start_date: str, end_date: str):
    """Yield (chunk_start, chunk_end) 'YYYY-MM-DD' pairs, one per calendar year."""
    s = datetime.strptime(start_date, "%Y-%m-%d")
    e = datetime.strptime(end_date, "%Y-%m-%d")
    if e < s:
        return
    for y in range(s.year, e.year + 1):
        cs = s if y == s.year else datetime(y, 1, 1)
        ce = e if y == e.year else datetime(y, 12, 31)
        yield cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")


class JQuantsStatementsFetcher:
    """Fetches /fins/summary into the jquants_statements table."""

    def __init__(self, db_path: str = DB_PATH, client=None, api_key: Optional[str] = None):
        self.db_path = db_path
        self._client = client
        self._api_key = api_key

    @property
    def client(self):
        """Lazily build a JQuantsClient (so --dry-run never needs the API key)."""
        if self._client is None:
            from jquants.client import JQuantsClient
            self._client = JQuantsClient(api_key=self._api_key)
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

    def fetch_date_range(self, start_date: str, end_date: str,
                         cache_dir: str = "", dry_run: bool = False) -> dict:
        """Fetch /fins/summary for [start_date, end_date], chunked by year.

        Returns summary stats. Idempotent: existing disclosures are skipped via
        the unique index on disclosure_no.
        """
        chunks = list(_year_chunks(start_date, end_date))
        if not chunks:
            logger.warning("Empty date range: %s .. %s", start_date, end_date)
            return {"fetched": 0, "inserted": 0, "dry_run": dry_run}

        if dry_run:
            for cs, ce in chunks:
                logger.info("[dry-run] would fetch /fins/summary %s .. %s", cs, ce)
            logger.info("[dry-run] %d yearly chunk(s); no API calls, no DB writes", len(chunks))
            return {"fetched": 0, "inserted": 0, "chunks": len(chunks), "dry_run": True}

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -64000;")

        total_fetched = 0
        total_inserted = 0
        try:
            for cs, ce in chunks:
                logger.info("Fetching /fins/summary %s .. %s", cs, ce)
                df = self.client.fin_summary_range(cs, ce, cache_dir=cache_dir)
                n = 0 if df is None or df.empty else len(df)
                total_fetched += n

                rows = []
                if n:
                    for rec in df.to_dict(orient="records"):
                        parsed = self._parse_row(rec)
                        if parsed:
                            rows.append(parsed)

                inserted = self._insert_batch(conn, rows)
                total_inserted += inserted
                logger.info("  %s .. %s: fetched %d, inserted %d new",
                            cs, ce, n, inserted)
        finally:
            conn.close()

        logger.info("J-Quants fetch complete. Fetched %d rows, inserted %d new.",
                    total_fetched, total_inserted)
        return {"fetched": total_fetched, "inserted": total_inserted,
                "chunks": len(chunks), "dry_run": False}
