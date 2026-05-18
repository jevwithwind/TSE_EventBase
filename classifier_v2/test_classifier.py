"""
Unit tests for classifier.py and migrate_db.py.

Run with:
    pytest test_classifier.py -v

Tests exercise the audit FAIL points to confirm they're fixed:
- Parsing handles malformed/missing/extra responses
- Enum validation rejects bad values
- Retry logic respects max_retries (no infinite loop)
- Batch failures mark events as failed (not silently dropped)
- Resume query uses classified_at, not event_type
- DB writes are idempotent and survive partial completion
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import classifier as clf
import migrate_db


# ============================================================================
# Helpers
# ============================================================================

def make_temp_db(events: list[dict] | None = None) -> str:
    """Create a temp eventbase.db with the events table + sample rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            ticker TEXT,
            company_name TEXT,
            event_date TEXT,
            event_time TEXT,
            headline TEXT,
            event_type TEXT,
            classified_at TEXT
        )
    """)
    if events:
        for e in events:
            conn.execute(
                "INSERT INTO events (id, ticker, company_name, event_date, headline, event_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (e["id"], e["ticker"], e.get("company_name", "TestCo"),
                 e["event_date"], e["headline"], e.get("event_type", "earnings")),
            )
    conn.commit()
    conn.close()
    return path


def good_response(events: list[clf.EventInput]) -> str:
    """Build a valid LLM response covering all given events."""
    classifications = []
    for ev in events:
        classifications.append({
            "id": ev.id,
            "event_type": "earnings",
            "event_subtype": "quarterly",
            "direction": "neutral",
            "magnitude": "medium",
            "confidence": "low",
            "headline_en": "Earnings Summary",
            "summary": "Some earnings.",
        })
    return json.dumps({"classifications": classifications})


# ============================================================================
# Migration
# ============================================================================

def test_migrate_adds_all_columns():
    db = make_temp_db()
    result = migrate_db.migrate(db, dry_run=False)
    assert result["added"] == len(migrate_db.NEW_COLUMNS)

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    for name, _ in migrate_db.NEW_COLUMNS:
        assert name in cols
    conn.close()
    os.unlink(db)


def test_migrate_idempotent():
    db = make_temp_db()
    migrate_db.migrate(db, dry_run=False)
    result2 = migrate_db.migrate(db, dry_run=False)
    assert result2["added"] == 0
    os.unlink(db)


def test_migrate_dry_run_makes_no_changes():
    db = make_temp_db()
    result = migrate_db.migrate(db, dry_run=True)
    assert result["dry_run"] is True
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    for name, _ in migrate_db.NEW_COLUMNS:
        assert name not in cols
    conn.close()
    os.unlink(db)


# ============================================================================
# Schema check
# ============================================================================

def test_check_schema_reports_missing():
    db = make_temp_db()
    missing = clf.check_schema(db)
    assert set(missing) == set(clf.REQUIRED_COLUMNS)
    os.unlink(db)


def test_check_schema_clean_after_migration():
    db = make_temp_db()
    migrate_db.migrate(db, dry_run=False)
    assert clf.check_schema(db) == []
    os.unlink(db)


# ============================================================================
# Parsing — audit FAIL: silent drops, bad JSON, missing fields
# ============================================================================

def test_parse_valid_response():
    expected = {1, 2}
    text = json.dumps({"classifications": [
        {"id": 1, "event_type": "earnings", "event_subtype": "q1",
         "direction": "positive", "magnitude": "medium", "confidence": "high",
         "headline_en": "EN1", "summary": "S1"},
        {"id": 2, "event_type": "buyback", "event_subtype": None,
         "direction": "positive", "magnitude": "small", "confidence": "medium",
         "headline_en": "EN2", "summary": "S2"},
    ]})
    valid, errors, batch_err = clf.parse_response(text, expected)
    assert len(valid) == 2
    assert errors == {}
    assert batch_err is None


def test_parse_json_decode_error():
    valid, errors, batch_err = clf.parse_response("not json at all", {1})
    assert valid == []
    assert batch_err and "json_decode_error" in batch_err


def test_parse_strips_markdown_fences():
    inner = json.dumps({"classifications": [{
        "id": 1, "event_type": "earnings", "event_subtype": None,
        "direction": "neutral", "magnitude": "medium", "confidence": "low",
        "headline_en": "X", "summary": "Y"}]})
    text = f"```json\n{inner}\n```"
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert len(valid) == 1
    assert batch_err is None


def test_parse_missing_classifications_key():
    text = json.dumps({"results": []})
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert valid == []
    assert batch_err and "missing_classifications_key" in batch_err


def test_parse_rejects_invalid_event_type():
    text = json.dumps({"classifications": [{
        "id": 1, "event_type": "INVENTED", "event_subtype": None,
        "direction": "positive", "magnitude": "medium", "confidence": "high",
        "headline_en": "X", "summary": "Y"}]})
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert valid == []
    assert errors[1].startswith("invalid_event_type")


def test_parse_rejects_invalid_direction():
    text = json.dumps({"classifications": [{
        "id": 1, "event_type": "earnings", "event_subtype": None,
        "direction": "bullish", "magnitude": "medium", "confidence": "high",
        "headline_en": "X", "summary": "Y"}]})
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert valid == []
    assert errors[1].startswith("invalid_direction")


def test_parse_rejects_invalid_magnitude():
    text = json.dumps({"classifications": [{
        "id": 1, "event_type": "earnings", "event_subtype": None,
        "direction": "positive", "magnitude": "huge", "confidence": "high",
        "headline_en": "X", "summary": "Y"}]})
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert valid == []
    assert errors[1].startswith("invalid_magnitude")


def test_parse_rejects_invalid_confidence():
    text = json.dumps({"classifications": [{
        "id": 1, "event_type": "earnings", "event_subtype": None,
        "direction": "positive", "magnitude": "medium", "confidence": "very_high",
        "headline_en": "X", "summary": "Y"}]})
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert valid == []
    assert errors[1].startswith("invalid_confidence")


def test_parse_marks_missing_events():
    """If LLM returns 1 event but 3 were expected, the other 2 are marked missing."""
    text = json.dumps({"classifications": [{
        "id": 1, "event_type": "earnings", "event_subtype": None,
        "direction": "positive", "magnitude": "medium", "confidence": "high",
        "headline_en": "X", "summary": "Y"}]})
    valid, errors, batch_err = clf.parse_response(text, {1, 2, 3})
    assert len(valid) == 1
    assert errors[2] == "missing_from_response"
    assert errors[3] == "missing_from_response"


def test_parse_rejects_id_not_in_batch():
    """If LLM hallucinates id=999, it's flagged."""
    text = json.dumps({"classifications": [{
        "id": 999, "event_type": "earnings", "event_subtype": None,
        "direction": "positive", "magnitude": "medium", "confidence": "high",
        "headline_en": "X", "summary": "Y"}]})
    valid, errors, batch_err = clf.parse_response(text, {1, 2})
    assert valid == []
    assert errors[999] == "id_not_in_batch"


def test_parse_handles_duplicate_ids():
    text = json.dumps({"classifications": [
        {"id": 1, "event_type": "earnings", "event_subtype": None,
         "direction": "positive", "magnitude": "medium", "confidence": "high",
         "headline_en": "X", "summary": "Y"},
        {"id": 1, "event_type": "buyback", "event_subtype": None,
         "direction": "neutral", "magnitude": "small", "confidence": "low",
         "headline_en": "X2", "summary": "Y2"},
    ]})
    valid, errors, batch_err = clf.parse_response(text, {1})
    assert len(valid) == 1
    assert errors[1] == "duplicate_id_in_response"


# ============================================================================
# Retry — audit FAIL: infinite loop on persistent error
# ============================================================================

class _FakeBrokenClient:
    """Simulates a client that always raises. Used to test retry bound."""
    def __init__(self):
        self.call_count = 0
    @property
    def chat(self):
        return self
    @property
    def completions(self):
        return self
    def create(self, **kwargs):
        self.call_count += 1
        raise RuntimeError("simulated_api_failure")


def test_retry_respects_max_attempts(monkeypatch):
    """LLMClient must raise after max_retries, not loop forever."""
    fake = _FakeBrokenClient()

    # Monkeypatch OpenAI inside the call() chain
    def fake_openai_init(*a, **kw):
        return fake

    # We can't patch OpenAI cleanly since it's imported inside __init__.
    # Instead, construct LLMClient with a real OpenAI instance, then replace
    # .client with our fake.
    monkeypatch.setattr(clf, "RETRY_BACKOFF", [0.001, 0.001, 0.001])

    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **kw: fake)

    client = clf.LLMClient(api_key="k", base_url="http://x", model="m",
                           max_retries=3, timeout=1)
    client.client = fake

    with pytest.raises(RuntimeError) as excinfo:
        client.call("sys", "user")
    assert "failed after 3 attempts" in str(excinfo.value)
    assert fake.call_count == 3  # NOT infinite


def test_retry_eventually_succeeds(monkeypatch):
    """Client recovers on 2nd attempt."""
    monkeypatch.setattr(clf, "RETRY_BACKOFF", [0.001, 0.001, 0.001])

    class _RecoveringClient:
        def __init__(self):
            self.call_count = 0
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("transient")
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = '{"classifications":[]}'
            mock_resp.usage.prompt_tokens = 100
            mock_resp.usage.completion_tokens = 50
            return mock_resp

    fake = _RecoveringClient()
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **kw: fake)

    client = clf.LLMClient(api_key="k", base_url="http://x", model="m", max_retries=3)
    client.client = fake
    text, in_tok, out_tok = client.call("sys", "user")
    assert text == '{"classifications":[]}'
    assert in_tok == 100 and out_tok == 50


# ============================================================================
# DB write — audit FAIL: silent drops, batch atomicity
# ============================================================================

def test_write_classifications_persists_success():
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1"},
        {"id": 2, "ticker": "9984", "event_date": "2024-08-07", "headline": "h2"},
    ])
    migrate_db.migrate(db, dry_run=False)

    classifications = [
        clf.Classification(id=1, event_type="earnings", event_subtype="q1",
                          direction="positive", magnitude="medium", confidence="high",
                          headline_en="EN1", summary="S1"),
        clf.Classification(id=2, event_type="buyback", event_subtype=None,
                          direction="positive", magnitude="small", confidence="medium",
                          headline_en="EN2", summary="S2"),
    ]
    clf.write_classifications(db, classifications, {}, None, {1, 2})

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT id, ai_event_type, ai_direction, classified_at, classification_failed_at "
        "FROM events ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows[0][1] == "earnings"
    assert rows[0][2] == "positive"
    assert rows[0][3] is not None    # classified_at set
    assert rows[0][4] is None        # not failed
    assert rows[1][1] == "buyback"
    os.unlink(db)


def test_write_batch_error_marks_all_failed():
    """Audit FAIL #1: batch error must mark all events failed, not silently drop."""
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1"},
        {"id": 2, "ticker": "9984", "event_date": "2024-08-07", "headline": "h2"},
    ])
    migrate_db.migrate(db, dry_run=False)

    clf.write_classifications(db, [], {}, "api_failed:timeout", {1, 2})

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT id, classified_at, classification_failed_at, classification_error "
        "FROM events ORDER BY id"
    ).fetchall()
    conn.close()
    for r in rows:
        assert r[1] is None                          # NOT classified
        assert r[2] is not None                      # failed_at set
        assert "api_failed" in r[3]                  # error captured
    os.unlink(db)


def test_write_validation_errors_per_event():
    """Some events succeed, others fail validation — both states recorded."""
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1"},
        {"id": 2, "ticker": "9984", "event_date": "2024-08-07", "headline": "h2"},
    ])
    migrate_db.migrate(db, dry_run=False)

    classifications = [
        clf.Classification(id=1, event_type="earnings", event_subtype=None,
                          direction="positive", magnitude="medium", confidence="high",
                          headline_en="EN1", summary="S1"),
    ]
    errors = {2: "invalid_event_type:bogus"}
    clf.write_classifications(db, classifications, errors, None, {1, 2})

    conn = sqlite3.connect(db)
    r1 = conn.execute("SELECT classified_at, classification_failed_at FROM events WHERE id=1").fetchone()
    r2 = conn.execute("SELECT classified_at, classification_failed_at, classification_error FROM events WHERE id=2").fetchone()
    conn.close()
    assert r1[0] is not None and r1[1] is None       # event 1 classified
    assert r2[0] is None and r2[1] is not None        # event 2 failed
    assert "invalid_event_type" in r2[2]
    os.unlink(db)


# ============================================================================
# Resume — audit FAIL: query checks event_type, finds zero
# ============================================================================

def test_count_unclassified_respects_classified_at():
    """Events with event_type populated by keyword classifier should still be counted."""
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1", "event_type": "earnings"},
        {"id": 2, "ticker": "9984", "event_date": "2024-08-07", "headline": "h2", "event_type": "earnings"},
    ])
    migrate_db.migrate(db, dry_run=False)
    # Both events have event_type populated; original classifier would see 0.
    # Our resume query uses classified_at, so both should be picked up.
    assert clf.count_unclassified(db) == 2
    os.unlink(db)


def test_count_unclassified_skips_already_classified():
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1"},
        {"id": 2, "ticker": "9984", "event_date": "2024-08-07", "headline": "h2"},
    ])
    migrate_db.migrate(db, dry_run=False)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE events SET classified_at = '2024-01-01' WHERE id = 1")
    conn.commit()
    conn.close()
    assert clf.count_unclassified(db) == 1
    os.unlink(db)


def test_count_unclassified_skips_failed():
    """Previously-failed batches are not re-attempted automatically."""
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1"},
    ])
    migrate_db.migrate(db, dry_run=False)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE events SET classification_failed_at = '2024-01-01' WHERE id = 1")
    conn.commit()
    conn.close()
    assert clf.count_unclassified(db) == 0
    os.unlink(db)


def test_count_unclassified_with_filter():
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "h1", "event_type": "earnings"},
        {"id": 2, "ticker": "9984", "event_date": "2024-08-07", "headline": "h2", "event_type": "buyback"},
    ])
    migrate_db.migrate(db, dry_run=False)
    assert clf.count_unclassified(db, where_filter="event_type='earnings'") == 1
    os.unlink(db)


# ============================================================================
# Smoke / dry-run
# ============================================================================

def test_run_dry_run(caplog):
    db = make_temp_db([
        {"id": 1, "ticker": "7203", "event_date": "2024-05-08", "headline": "決算"},
    ])
    migrate_db.migrate(db, dry_run=False)
    result = clf.run(
        db_path=db, api_key="dry", base_url="http://x", model="m",
        limit=10, dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["estimate_in"] > 0
    os.unlink(db)


def test_run_raises_on_missing_schema():
    db = make_temp_db([{"id": 1, "ticker": "x", "event_date": "2024-01-01", "headline": "h"}])
    with pytest.raises(RuntimeError, match="schema missing"):
        clf.run(db_path=db, api_key="x", base_url="http://x", model="m", limit=1)
    os.unlink(db)


# ============================================================================
# Prompt construction
# ============================================================================

def test_user_message_includes_all_events():
    events = [
        clf.EventInput(id=1, ticker="7203", company_name="Toyota",
                       event_date="2024-05-08", headline="決算短信"),
        clf.EventInput(id=2, ticker="9984", company_name="SoftBank",
                       event_date="2024-08-07", headline="買戻し"),
    ]
    msg = clf.build_user_message(events)
    assert "id=1" in msg and "id=2" in msg
    assert "7203" in msg and "9984" in msg
    assert "決算短信" in msg and "買戻し" in msg


def test_system_prompt_documents_limitation():
    """The prompt must acknowledge headline-only limitation to discourage hallucination."""
    assert "headline" in clf.SYSTEM_PROMPT.lower()
    assert "neutral" in clf.SYSTEM_PROMPT.lower()
    assert "do not see actual financial numbers" in clf.SYSTEM_PROMPT.lower()
