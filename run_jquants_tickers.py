#!/usr/bin/env python3
"""
Populate the tickers table from J-Quants /listed/info (get_list).

Maps the current listed-company snapshot to (ticker, company_name,
company_name_en, sector, market_segment). One API call. Used to attach company
names/sectors to the enriched export. Delisted companies are absent from the
current snapshot, so their names may be blank.

Usage:
    python run_jquants_tickers.py
"""

import argparse
import logging
import sqlite3

import pandas as pd

from config import DB_PATH
from jquants.client import JQuantsClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

_NULL = {"", "-", "nan", "NaN", "None", "NaT"}


def _s(v):
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s if s and s not in _NULL else None


def run(db_path: str, api_key: str | None = None) -> dict:
    cli = JQuantsClient(api_key=api_key)
    df = cli.list_info()
    if df is None or df.empty:
        logger.warning("/listed/info returned no rows")
        return {"rows": 0}

    logger.info("listed/info: %d companies, %d columns", len(df), len(df.columns))
    rows = []
    seen = set()
    for r in df.to_dict(orient="records"):
        code = _s(r.get("Code"))
        if not code:
            continue
        ticker = code[:4]
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.append((
            ticker,
            _s(r.get("CoName")),                              # company name (JP)
            _s(r.get("CoNameEn")),                            # company name (EN)
            _s(r.get("S33NmEn")) or _s(r.get("S33Nm")) or _s(r.get("S17NmEn")),  # sector
            _s(r.get("MktNmEn")) or _s(r.get("MktNm")),       # market segment
        ))

    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO tickers "
            "(ticker, company_name, company_name_en, sector, market_segment) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
    finally:
        conn.close()
    logger.info("Upserted %d tickers (table now %d rows)", len(rows), n)
    return {"rows": len(rows), "total": n}


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    p = argparse.ArgumentParser(description="Populate tickers from J-Quants /listed/info")
    p.add_argument("--db", default=DB_PATH, help="Path to eventbase.db")
    p.add_argument("--api-key", default=None, help="Override JQUANTS_API_KEY")
    args = p.parse_args()
    result = run(args.db, api_key=args.api_key)
    print(f"\nPopulated tickers: {result.get('rows', 0)} (table total {result.get('total', 0)}).")
    return 0


if __name__ == "__main__":
    exit(main())
