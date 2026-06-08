"""
Unit tests for stage2_financial.py (J-Quants beat-vs-forecast enrichment)
and jquants/migrate_db.py.

Run with:
    pytest test_stage2_financial.py -v
"""
import os
import sqlite3
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)   # for `import config`, `from jquants import ...`
sys.path.insert(0, HERE)   # for `import stage2_financial`

import stage2_financial as s2          # noqa: E402
from jquants import migrate_db as jqm  # noqa: E402


# ============================================================================
# Helpers
# ============================================================================

def make_temp_db() -> str:
    """Temp DB with a full-enough events table, then run the Stage-2 migration."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            ticker TEXT, company_name TEXT, event_date TEXT, event_time TEXT,
            headline TEXT NOT NULL, event_type TEXT, source TEXT,
            source_doc_id TEXT, classified_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    jqm.migrate(path, dry_run=False)  # adds data_* columns + jquants_statements
    return path


def ins_stmt(conn, dno, ticker, date, ptype, fyend,
             eps=None, feps=None, rdps=None, fdps=None, time="15:00"):
    cur = conn.execute(
        "INSERT INTO jquants_statements "
        "(disclosure_no, local_code, ticker, disclosed_date, disclosed_time, "
        " period_type, current_fy_end, eps, forecast_eps, "
        " result_dps_annual, forecast_dps_annual) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (dno, ticker + "0", ticker, date, time, ptype, fyend,
         eps, feps, rdps, fdps),
    )
    return cur.lastrowid


def ins_event(conn, ticker, date, etype, headline="h"):
    cur = conn.execute(
        "INSERT INTO events (ticker, event_date, headline, event_type, source) "
        "VALUES (?,?,?,?,'tdnet')",
        (ticker, date, headline, etype),
    )
    return cur.lastrowid


# ============================================================================
# Pure: compute_surprise
# ============================================================================

def test_surprise_positive():
    assert s2.compute_surprise(110, 100) == pytest.approx(0.10)


def test_surprise_negative():
    assert s2.compute_surprise(90, 100) == pytest.approx(-0.10)


def test_surprise_negative_forecast_sign():
    # Smaller-than-forecast loss is a POSITIVE surprise.
    assert s2.compute_surprise(-50, -100) == pytest.approx(0.50)
    # Bigger-than-forecast loss is NEGATIVE.
    assert s2.compute_surprise(-200, -100) == pytest.approx(-1.0)


def test_surprise_none_cases():
    assert s2.compute_surprise(None, 100) is None
    assert s2.compute_surprise(100, None) is None
    assert s2.compute_surprise(100, 0) is None


# ============================================================================
# Pure: classify_surprise thresholds
# ============================================================================

def test_classify_large_positive():
    assert s2.classify_surprise(s2.LARGE_THRESHOLD + 0.05) == ("positive", "large")


def test_classify_medium_negative():
    mid = (s2.MEDIUM_THRESHOLD + s2.LARGE_THRESHOLD) / 2
    assert s2.classify_surprise(-mid) == ("negative", "medium")


def test_classify_small_is_neutral():
    assert s2.classify_surprise(s2.MEDIUM_THRESHOLD / 2) == ("neutral", "small")


def test_classify_boundaries():
    # exactly at the (calibrated) thresholds
    assert s2.classify_surprise(s2.MEDIUM_THRESHOLD) == ("positive", "medium")
    assert s2.classify_surprise(s2.LARGE_THRESHOLD) == ("positive", "large")
    assert s2.classify_surprise(-s2.MEDIUM_THRESHOLD) == ("negative", "medium")


# ============================================================================
# Pure: build_signal / prior-forecast lookup
# ============================================================================

def _stmt(**kw):
    base = dict(id=0, ticker="7203", disclosed_date="2024-01-01",
               disclosed_time="15:00", period_type="1Q",
               current_fy_end="2024-03-31", eps=None, forecast_eps=None,
               result_dps=None, forecast_dps=None)
    base.update(kw)
    return s2.Stmt(**base)


def test_build_signal_fy_actual_vs_forecast():
    stmts = [
        _stmt(id=1, disclosed_date="2023-08-04", period_type="1Q", forecast_eps=300.0),
        _stmt(id=2, disclosed_date="2024-05-10", period_type="FY", eps=360.0),
    ]
    sig = s2.build_signal(stmts, 1, "eps", "forecast_eps", "eps")
    assert sig is not None
    assert sig.basis == "actual_vs_forecast"
    assert sig.actual == 360.0 and sig.forecast == 300.0
    assert sig.surprise_pct == pytest.approx(0.20)
    assert sig.direction == "positive"
    # magnitude flows from the (calibrated) thresholds
    assert (sig.direction, sig.magnitude) == s2.classify_surprise(0.20)


def test_build_signal_forecast_revision():
    stmts = [
        _stmt(id=1, disclosed_date="2023-08-04", period_type="1Q", forecast_eps=300.0),
        _stmt(id=2, disclosed_date="2023-11-04", period_type="2Q", forecast_eps=330.0),
    ]
    sig = s2.build_signal(stmts, 1, "eps", "forecast_eps", "eps")
    assert sig.basis == "forecast_revision"
    assert sig.actual == 330.0 and sig.forecast == 300.0
    assert sig.surprise_pct == pytest.approx(0.10)
    assert sig.direction == "positive"
    assert (sig.direction, sig.magnitude) == s2.classify_surprise(0.10)


def test_build_signal_none_without_prior_forecast():
    stmts = [_stmt(id=1, period_type="FY", eps=360.0)]  # first ever; no prior
    assert s2.build_signal(stmts, 0, "eps", "forecast_eps", "eps") is None


def test_prior_forecast_ignores_other_fiscal_year():
    stmts = [
        _stmt(id=1, disclosed_date="2023-05-01", current_fy_end="2023-03-31", forecast_eps=200.0),
        _stmt(id=2, disclosed_date="2024-05-10", current_fy_end="2024-03-31", period_type="FY", eps=360.0),
    ]
    # The only prior forecast belongs to a different FY -> no signal.
    assert s2.build_signal(stmts, 1, "eps", "forecast_eps", "eps") is None


# ============================================================================
# End-to-end enrichment on a temp DB
# ============================================================================

def _setup_scenario(conn):
    """7203 FY ending 2024-03-31: guidance 300 -> raised 330 -> actual 360.
    Dividends: forecast 70 -> 75 -> actual 80."""
    s1 = ins_stmt(conn, "s1", "7203", "2023-08-04", "1Q", "2024-03-31",
                  eps=70, feps=300.0, fdps=70.0)
    s2_ = ins_stmt(conn, "s2", "7203", "2023-11-04", "2Q", "2024-03-31",
                   eps=160, feps=330.0, fdps=75.0)
    s3 = ins_stmt(conn, "s3", "7203", "2024-05-10", "FY", "2024-03-31",
                  eps=360.0, rdps=80.0)
    conn.commit()
    return s1, s2_, s3


def test_end_to_end_enrichment():
    db = make_temp_db()
    conn = sqlite3.connect(db)
    s1, s2id, s3 = _setup_scenario(conn)

    e_fy = ins_event(conn, "7203", "2024-05-10", "earnings")          # -> EPS actual vs forecast
    e_rev = ins_event(conn, "7203", "2023-11-04", "forecast_revision")  # -> EPS revision
    e_first = ins_event(conn, "7203", "2023-08-04", "earnings")       # -> matched, no signal
    e_div = ins_event(conn, "7203", "2024-05-10", "dividend")          # -> DPS actual vs forecast
    e_nostmt = ins_event(conn, "9999", "2024-05-10", "earnings")       # -> unmatched
    conn.commit()
    conn.close()

    result = s2.run_stage2_financial(db, dry_run=False)
    assert result["enriched"] == 3
    assert result["matched_no_signal"] == 1
    assert result["unmatched"] == 1
    assert result["total"] == 5

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    fy = conn.execute("SELECT * FROM events WHERE id=?", (e_fy,)).fetchone()
    assert fy["data_direction"] == "positive"
    assert fy["data_magnitude"] == s2.classify_surprise((360.0 - 330.0) / 330.0)[1]  # +9.1%
    assert fy["data_basis"] == "actual_vs_forecast"
    assert fy["data_metric"] == "eps"
    assert fy["data_actual"] == 360.0 and fy["data_forecast"] == 330.0
    assert fy["data_statement_id"] == s3
    assert fy["data_enriched_at"] is not None

    rev = conn.execute("SELECT * FROM events WHERE id=?", (e_rev,)).fetchone()
    assert rev["data_basis"] == "forecast_revision"
    assert rev["data_metric"] == "eps"
    assert rev["data_direction"] == "positive"
    assert rev["data_magnitude"] == s2.classify_surprise((330.0 - 300.0) / 300.0)[1]  # +10%

    div = conn.execute("SELECT * FROM events WHERE id=?", (e_div,)).fetchone()
    assert div["data_metric"] == "dps"                # dividend prefers DPS signal
    assert div["data_basis"] == "actual_vs_forecast"
    assert div["data_actual"] == 80.0 and div["data_forecast"] == 75.0

    first = conn.execute("SELECT * FROM events WHERE id=?", (e_first,)).fetchone()
    assert first["data_enriched_at"] is None          # matched but no prior forecast

    nostmt = conn.execute("SELECT * FROM events WHERE id=?", (e_nostmt,)).fetchone()
    assert nostmt["data_enriched_at"] is None          # no statement at all
    conn.close()
    os.unlink(db)


def test_resume_skips_already_enriched():
    db = make_temp_db()
    conn = sqlite3.connect(db)
    _setup_scenario(conn)
    ins_event(conn, "7203", "2024-05-10", "earnings")
    ins_event(conn, "7203", "2023-08-04", "earnings")   # never gets a signal
    ins_event(conn, "9999", "2024-05-10", "earnings")   # unmatched
    conn.commit()
    conn.close()

    first = s2.run_stage2_financial(db, dry_run=False)
    assert first["enriched"] == 1
    # Second run: the enriched event is gated out; only the 2 signal-less remain.
    second = s2.run_stage2_financial(db, dry_run=False)
    assert second["enriched"] == 0
    assert second["total"] == 2
    os.unlink(db)


def test_dry_run_writes_nothing():
    db = make_temp_db()
    conn = sqlite3.connect(db)
    _setup_scenario(conn)
    ins_event(conn, "7203", "2024-05-10", "earnings")
    conn.commit()
    conn.close()

    result = s2.run_stage2_financial(db, dry_run=True)
    assert result["enriched"] == 1 and result["dry_run"] is True

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM events WHERE data_enriched_at IS NOT NULL").fetchone()[0]
    conn.close()
    assert n == 0
    os.unlink(db)


def test_reset_enrichment():
    db = make_temp_db()
    conn = sqlite3.connect(db)
    _setup_scenario(conn)
    ins_event(conn, "7203", "2024-05-10", "earnings")
    conn.commit()
    conn.close()

    s2.run_stage2_financial(db, dry_run=False)
    assert s2.reset_enrichment(db) == 1
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM events WHERE data_enriched_at IS NOT NULL").fetchone()[0]
    conn.close()
    assert n == 0
    os.unlink(db)


def test_run_raises_without_schema():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ticker TEXT, event_date TEXT, event_type TEXT, headline TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="Schema not ready"):
        s2.run_stage2_financial(path, dry_run=True)
    os.unlink(path)
