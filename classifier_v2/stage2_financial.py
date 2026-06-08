"""
Stage 2: Financial fundamentals enrichment (data-driven beat/miss).

Derives an objective sentiment signal for financial-results events by comparing
J-Quants reported figures against the company's own prior forecast — the true
"earnings surprise vs. management guidance". Reads the ``jquants_statements``
table (populated by ``run_jquants.py``) and writes ``data_*`` columns onto
``events``.

Architecture (thesis pipeline):
  Stage 0:  market-implied sentiment from overnight returns (stage0_prices.py)
  Stage 1:  LLM headline classification             (classifier.py)
  Stage 2 (this): J-Quants fundamentals — beat vs. prior forecast

Signal definition (per matched disclosure)
------------------------------------------
For each statement we look back to the most recent PRIOR disclosure for the
SAME fiscal year (CurFYEn) that carried a forecast, then:

  * actual_vs_forecast — only at CurPerType='FY' (the full-year actual is
    comparable to the full-year forecast):
        surprise = (actual - prior_forecast) / |prior_forecast|
  * forecast_revision — otherwise, when this disclosure itself carries a
    forecast (e.g. a quarterly report or a guidance revision):
        surprise = (new_forecast - prior_forecast) / |prior_forecast|

EPS drives earnings/forecast_revision events; DPS (annual dividend per share)
drives dividend events. Quarterly *actuals* are cumulative-to-date and so are
NOT compared to the full-year forecast (that would be apples-to-oranges); they
instead surface the guidance signal above. Half-year (2Q) forecast comparison
is a possible future refinement.

Thresholds (tunable; calibrate against classifier_v2/validation/ gold standard):
  |surprise| >= 10%  -> large
  |surprise| >=  3%  -> medium (and sets direction)
  otherwise          -> small / neutral

Usage:
    python stage2_financial.py --db ../data/tse_eventbase.db --dry-run
    python stage2_financial.py --db ../data/tse_eventbase.db
    python stage2_financial.py --db ../data/tse_eventbase.db --reset   # recompute
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------- Config ----------

LARGE_THRESHOLD = 0.10   # ±10% surprise -> large
MEDIUM_THRESHOLD = 0.03  # ±3% surprise -> medium (and non-neutral direction)

SCOPE_EVENT_TYPES = ("earnings", "forecast_revision", "dividend")

REQUIRED_DATA_COLUMNS = [
    "data_direction", "data_magnitude", "data_surprise_pct", "data_basis",
    "data_metric", "data_actual", "data_forecast", "data_statement_id",
    "data_enriched_at",
]


# ---------- Pure signal logic ----------

def compute_surprise(actual: Optional[float], forecast: Optional[float]) -> Optional[float]:
    """(actual - forecast) / |forecast|. None if not computable.

    Using |forecast| in the denominator keeps the sign meaningful even when the
    forecast is negative (e.g. a smaller-than-forecast loss is a positive
    surprise).
    """
    if actual is None or forecast is None or forecast == 0:
        return None
    return (actual - forecast) / abs(forecast)


def classify_surprise(surprise: float) -> tuple[str, str]:
    """Map a surprise ratio to (direction, magnitude)."""
    abs_s = abs(surprise)
    if abs_s >= LARGE_THRESHOLD:
        magnitude = "large"
    elif abs_s >= MEDIUM_THRESHOLD:
        magnitude = "medium"
    else:
        magnitude = "small"

    if surprise >= MEDIUM_THRESHOLD:
        direction = "positive"
    elif surprise <= -MEDIUM_THRESHOLD:
        direction = "negative"
    else:
        direction = "neutral"
    return direction, magnitude


@dataclass
class Stmt:
    id: int
    ticker: str
    disclosed_date: str
    disclosed_time: Optional[str]
    period_type: Optional[str]
    current_fy_end: Optional[str]
    eps: Optional[float]
    forecast_eps: Optional[float]
    result_dps: Optional[float]
    forecast_dps: Optional[float]


@dataclass
class Signal:
    direction: str
    magnitude: str
    surprise_pct: float
    basis: str          # actual_vs_forecast | forecast_revision
    metric: str         # eps | dps
    actual: float
    forecast: float


def _latest_prior_forecast(
    stmts: list[Stmt], idx: int, forecast_attr: str
) -> Optional[float]:
    """Most recent forecast for the SAME fiscal year disclosed before stmts[idx]."""
    cur = stmts[idx]
    if not cur.current_fy_end:
        return None
    for j in range(idx - 1, -1, -1):
        p = stmts[j]
        if (p.current_fy_end == cur.current_fy_end
                and getattr(p, forecast_attr) is not None):
            return getattr(p, forecast_attr)
    return None


def build_signal(
    stmts: list[Stmt], idx: int, actual_attr: str, forecast_attr: str, metric: str
) -> Optional[Signal]:
    """Compute the beat/miss (or revision) signal for stmts[idx], or None."""
    cur = stmts[idx]
    actual = getattr(cur, actual_attr)
    cur_forecast = getattr(cur, forecast_attr)
    prior_forecast = _latest_prior_forecast(stmts, idx, forecast_attr)
    if prior_forecast is None:
        return None

    # True beat/miss only when the full-year actual is available.
    if cur.period_type == "FY" and actual is not None:
        s = compute_surprise(actual, prior_forecast)
        if s is None:
            return None
        d, m = classify_surprise(s)
        return Signal(d, m, s, "actual_vs_forecast", metric, actual, prior_forecast)

    # Otherwise: guidance revision (this disclosure's forecast vs the prior one).
    if cur_forecast is not None:
        s = compute_surprise(cur_forecast, prior_forecast)
        if s is None:
            return None
        d, m = classify_surprise(s)
        return Signal(d, m, s, "forecast_revision", metric, cur_forecast, prior_forecast)

    return None


# ---------- DB helpers ----------

def check_schema(conn: sqlite3.Connection) -> list[str]:
    """Return missing prerequisites (empty list = ready)."""
    missing: list[str] = []
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "jquants_statements" not in tables:
        missing.append("table:jquants_statements")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    for c in REQUIRED_DATA_COLUMNS:
        if c not in cols:
            missing.append(f"column:{c}")
    return missing


def load_statements(conn: sqlite3.Connection) -> dict[str, list[Stmt]]:
    """Load statements grouped by ticker, each list ordered chronologically."""
    rows = conn.execute(
        "SELECT id, ticker, disclosed_date, disclosed_time, period_type, "
        "current_fy_end, eps, forecast_eps, result_dps_annual, forecast_dps_annual "
        "FROM jquants_statements "
        "WHERE ticker IS NOT NULL AND disclosed_date IS NOT NULL "
        "ORDER BY ticker, disclosed_date, disclosed_time, id"
    ).fetchall()
    by_ticker: dict[str, list[Stmt]] = defaultdict(list)
    for r in rows:
        by_ticker[r[1]].append(Stmt(
            id=r[0], ticker=r[1], disclosed_date=r[2], disclosed_time=r[3],
            period_type=r[4], current_fy_end=r[5], eps=r[6], forecast_eps=r[7],
            result_dps=r[8], forecast_dps=r[9],
        ))
    return by_ticker


def compute_all_signals(
    by_ticker: dict[str, list[Stmt]]
) -> tuple[dict[int, Signal], dict[int, Signal], dict[tuple[str, str], list[int]]]:
    """Precompute EPS and DPS signals per statement and a (ticker,date)->ids index."""
    eps_signals: dict[int, Signal] = {}
    dps_signals: dict[int, Signal] = {}
    date_index: dict[tuple[str, str], list[int]] = defaultdict(list)

    for stmts in by_ticker.values():
        for idx, st in enumerate(stmts):
            date_index[(st.ticker, st.disclosed_date)].append(st.id)
            eps = build_signal(stmts, idx, "eps", "forecast_eps", "eps")
            if eps:
                eps_signals[st.id] = eps
            dps = build_signal(stmts, idx, "result_dps", "forecast_dps", "dps")
            if dps:
                dps_signals[st.id] = dps
    return eps_signals, dps_signals, date_index


def _pick_signal(
    candidate_ids: list[int],
    event_type: str,
    eps_signals: dict[int, Signal],
    dps_signals: dict[int, Signal],
) -> tuple[Optional[int], Optional[Signal]]:
    """Choose the best (statement_id, signal) for an event from same-day candidates."""
    prefer_dps = event_type == "dividend"
    primary = dps_signals if prefer_dps else eps_signals
    secondary = eps_signals if prefer_dps else dps_signals

    # candidate_ids are in chronological order; prefer the latest with a signal.
    for table in (primary, secondary):
        hits = [sid for sid in candidate_ids if sid in table]
        if hits:
            sid = hits[-1]
            return sid, table[sid]
    return None, None


# ---------- Main logic ----------

def reset_enrichment(db_path: str) -> int:
    """Clear all data_* enrichment so it can be recomputed. Returns rows reset."""
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute(
            "UPDATE events SET data_direction=NULL, data_magnitude=NULL, "
            "data_surprise_pct=NULL, data_basis=NULL, data_metric=NULL, "
            "data_actual=NULL, data_forecast=NULL, data_statement_id=NULL, "
            "data_enriched_at=NULL WHERE data_enriched_at IS NOT NULL"
        ).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def run_stage2_financial(
    db_path: str,
    where_filter: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Enrich in-scope events with the beat-vs-forecast signal. Returns stats."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        missing = check_schema(conn)
        if missing:
            raise RuntimeError(
                f"Schema not ready: missing {missing}. "
                "Run: python jquants/migrate_db.py --db <db>"
            )

        by_ticker = load_statements(conn)
        n_stmts = sum(len(v) for v in by_ticker.values())
        if n_stmts == 0:
            logger.warning("jquants_statements is empty. Run run_jquants.py first.")
            return {"enriched": 0, "matched_no_signal": 0, "unmatched": 0, "total": 0}
        logger.info("Loaded %d statements across %d tickers", n_stmts, len(by_ticker))

        eps_signals, dps_signals, date_index = compute_all_signals(by_ticker)
        logger.info("Computed signals: %d EPS, %d DPS",
                    len(eps_signals), len(dps_signals))

        # Select in-scope, not-yet-enriched events.
        scope = ", ".join(f"'{t}'" for t in SCOPE_EVENT_TYPES)
        sql = (
            f"SELECT id, ticker, event_date, event_type FROM events "
            f"WHERE data_enriched_at IS NULL AND event_type IN ({scope})"
        )
        if where_filter:
            sql += f" AND ({where_filter})"
        sql += " ORDER BY id"
        if limit:
            sql += f" LIMIT {limit}"
        events = conn.execute(sql).fetchall()
        logger.info("In-scope events to process: %d", len(events))

        enriched = 0
        matched_no_signal = 0
        unmatched = 0
        updates: list[tuple] = []

        for ev in events:
            candidates = date_index.get((ev["ticker"], ev["event_date"]))
            if not candidates:
                unmatched += 1
                continue
            sid, sig = _pick_signal(candidates, ev["event_type"],
                                    eps_signals, dps_signals)
            if sig is None:
                matched_no_signal += 1
                continue
            updates.append((
                sig.direction, sig.magnitude, sig.surprise_pct, sig.basis,
                sig.metric, sig.actual, sig.forecast, sid, ev["id"],
            ))
            enriched += 1

        logger.info(
            "Enriched: %d  Matched-but-no-signal: %d  Unmatched (no statement): %d",
            enriched, matched_no_signal, unmatched,
        )

        if dry_run:
            from collections import Counter
            logger.info("DRY RUN — no changes written")
            logger.info("Direction: %s", dict(Counter(u[0] for u in updates)))
            logger.info("Magnitude: %s", dict(Counter(u[1] for u in updates)))
            logger.info("Basis: %s", dict(Counter(u[3] for u in updates)))
            logger.info("Metric: %s", dict(Counter(u[4] for u in updates)))
        elif updates:
            conn.executemany(
                "UPDATE events SET "
                "data_direction = ?, data_magnitude = ?, data_surprise_pct = ?, "
                "data_basis = ?, data_metric = ?, data_actual = ?, "
                "data_forecast = ?, data_statement_id = ?, "
                "data_enriched_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                updates,
            )
            conn.commit()
            logger.info("Wrote %d enrichments to DB", len(updates))

        return {
            "enriched": enriched,
            "matched_no_signal": matched_no_signal,
            "unmatched": unmatched,
            "total": len(events),
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
        description="Stage 2: enrich events with J-Quants beat-vs-forecast signal"
    )
    p.add_argument("--db", required=True, help="Path to eventbase.db")
    p.add_argument("--limit", type=int, default=None,
                   help="Max events to process (smoke testing)")
    p.add_argument("--filter", default=None, help="Extra SQL WHERE clause")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute and report without writing to DB")
    p.add_argument("--reset", action="store_true",
                   help="Clear existing data_* enrichment before running (recompute)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.reset and not args.dry_run:
        n = reset_enrichment(args.db)
        logger.info("Reset %d previously-enriched events", n)

    result = run_stage2_financial(
        db_path=args.db,
        where_filter=args.filter,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("Result: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
