"""
classifier.py — Clean rewrite of the TSE_EventBase AI classifier.

Replaces the audit-failed classifier in TSE_EventBase/classifier/event_classifier.py.
See classifier_audit_report.md for the bugs this rewrite addresses.

Design decisions
----------------
1. Resume uses `classified_at IS NULL AND classification_failed_at IS NULL`.
   Keyword-based event_type is preserved; AI value goes to ai_event_type.
2. Two-stage sentiment strategy: this script does Stage 1 (LLM on headline only).
   Stage 2 (overnight return augmentation) is a separate concern.
3. Bounded retries with exponential backoff. After max retries, batch is marked
   `classification_failed_at` with error message — NEVER an infinite loop.
4. Strict enum validation on every field. Validation failures are surfaced, not
   silently coerced.
5. Two new event_type columns are populated (keyword keeps, AI adds):
   - event_type        : keyword-derived (unchanged)
   - ai_event_type     : LLM-derived
6. Configurable rate limiting between batches.
7. --limit flag for smoke testing.
8. Token & cost tracking with running totals.

Schema additions (handled by migrate_db.py)
--------------------------------------------
  ai_event_type, ai_event_subtype, ai_direction, ai_magnitude, ai_confidence,
  ai_headline_en, ai_summary, classification_failed_at, classification_error

Existing columns kept:
  event_type (keyword), classified_at (set on success)

Usage
-----
    python classifier.py --db data/eventbase.db --dry-run
    python classifier.py --db data/eventbase.db --limit 100   # smoke
    python classifier.py --db data/eventbase.db               # full run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------- Constants ----------

ALLOWED_EVENT_TYPES = {
    "earnings", "forecast_revision", "dividend", "buyback", "ma",
    "tender_offer", "leadership_change", "stock_split", "large_holding",
    "capital_raise", "delisting", "other",
}
ALLOWED_DIRECTIONS = {"positive", "negative", "neutral"}
ALLOWED_MAGNITUDES = {"large", "medium", "small"}
ALLOWED_CONFIDENCES = {"high", "medium", "low"}

DEFAULT_BATCH_SIZE = 20
DEFAULT_DELAY_MS = 200
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_SEC = 60

# Retry backoff schedule (seconds). Length defines max attempts.
RETRY_BACKOFF = [2, 8, 30]


# ---------- Prompt ----------

SYSTEM_PROMPT = """You are classifying Japanese corporate disclosure headlines from the Tokyo Stock Exchange (TDnet).

IMPORTANT LIMITATIONS:
- You see ONLY the headline, ticker, company name, and date.
- You do NOT see actual financial numbers (EPS, revenue, etc).
- If the headline contains a clear sentiment marker (e.g., 上方修正, 下方修正, 増配, 減配), use it.
- If the headline is neutral (e.g., "決算短信" without qualifier), return direction="neutral" with confidence="low".
- Do NOT hallucinate sentiment. "neutral" with low confidence is preferred over a guessed direction.

OUTPUT SCHEMA (JSON object with a "classifications" array, one entry per input event, same order):
{
  "classifications": [
    {
      "id": <int from input>,
      "event_type": <one of: earnings, forecast_revision, dividend, buyback, ma, tender_offer, leadership_change, stock_split, large_holding, capital_raise, delisting, other>,
      "event_subtype": <short string or null>,
      "direction": <one of: positive, negative, neutral>,
      "magnitude": <one of: large, medium, small>,
      "confidence": <one of: high, medium, low>,
      "headline_en": <English translation>,
      "summary": <one-sentence English summary>
    }
  ]
}

EXAMPLES:

Input: id=1 | 7203 | Toyota | 2024-05-08 | 「2024年3月期 業績予想の上方修正に関するお知らせ」
Output entry: {"id":1,"event_type":"forecast_revision","event_subtype":"upward_revision","direction":"positive","magnitude":"medium","confidence":"high","headline_en":"Notice of Upward Revision of FY2024 Earnings Forecast","summary":"Toyota raised FY2024 earnings forecast."}

Input: id=2 | 9984 | SoftBank Group | 2024-08-07 | 「2025年3月期 第1四半期決算短信〔IFRS〕（連結）」
Output entry: {"id":2,"event_type":"earnings","event_subtype":"quarterly","direction":"neutral","magnitude":"medium","confidence":"low","headline_en":"Q1 FY2025 Consolidated Earnings Summary (IFRS)","summary":"SoftBank Group released Q1 FY2025 earnings. Direction not inferable from headline."}

Input: id=3 | 6758 | Sony | 2024-02-14 | 「自己株式取得に係る事項の決定に関するお知らせ」
Output entry: {"id":3,"event_type":"buyback","event_subtype":"share_buyback_decision","direction":"positive","magnitude":"medium","confidence":"high","headline_en":"Notice of Decision on Share Buyback Matters","summary":"Sony decided on share buyback details."}

Input: id=4 | 7974 | Nintendo | 2024-08-06 | 「特別損失の計上に関するお知らせ」
Output entry: {"id":4,"event_type":"other","event_subtype":"special_loss","direction":"negative","magnitude":"medium","confidence":"high","headline_en":"Notice of Recording Special Loss","summary":"Nintendo recorded a special loss."}

Input: id=5 | 8306 | MUFG | 2024-11-13 | 「中間配当予想の修正（増配）に関するお知らせ」
Output entry: {"id":5,"event_type":"dividend","event_subtype":"dividend_increase","direction":"positive","magnitude":"small","confidence":"high","headline_en":"Notice of Revision of Interim Dividend Forecast (Increase)","summary":"MUFG revised interim dividend forecast upward."}

Return ONLY the JSON object. No prose, no markdown fences.
"""


# ---------- Data classes ----------

@dataclass
class EventInput:
    id: int
    ticker: str
    company_name: str
    event_date: str
    headline: str


@dataclass
class Classification:
    id: int
    event_type: str
    event_subtype: str | None
    direction: str
    magnitude: str
    confidence: str
    headline_en: str
    summary: str


@dataclass
class BatchResult:
    classifications: list[Classification] = field(default_factory=list)
    validation_errors: dict[int, str] = field(default_factory=dict)
    batch_error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


# ---------- Prompt construction ----------

def build_user_message(events: list[EventInput]) -> str:
    lines = ["Please classify the following events:\n"]
    for ev in events:
        lines.append(
            f"id={ev.id} | {ev.ticker} | {ev.company_name} | "
            f"{ev.event_date} | 「{ev.headline}」"
        )
    lines.append("\nReturn the JSON object now.")
    return "\n".join(lines)


# ---------- Response parsing & validation ----------

def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json or ``` opening
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def parse_response(response_text: str, expected_ids: set[int]) -> tuple[list[Classification], dict[int, str], str | None]:
    """
    Parse and validate the LLM response.

    Returns (valid_classifications, per_event_errors, batch_error).
    - valid_classifications: list of Classification objects that pass enum checks.
    - per_event_errors: {event_id: error_message} for events that failed validation.
    - batch_error: str if the whole response is unparseable; None otherwise.
    """
    text = _strip_fences(response_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        return [], {}, f"json_decode_error: {e}"

    if not isinstance(parsed, dict) or "classifications" not in parsed:
        return [], {}, f"missing_classifications_key (got keys: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__})"

    arr = parsed["classifications"]
    if not isinstance(arr, list):
        return [], {}, f"classifications_not_array (got {type(arr).__name__})"

    valid: list[Classification] = []
    errors: dict[int, str] = {}
    seen_ids: set[int] = set()

    for i, item in enumerate(arr):
        if not isinstance(item, dict):
            errors[-i - 1] = f"item_not_object: {item!r}"
            continue
        try:
            ev_id = int(item.get("id"))
        except (TypeError, ValueError):
            errors[-i - 1] = f"missing_or_invalid_id: {item!r}"
            continue

        if ev_id in seen_ids:
            errors[ev_id] = "duplicate_id_in_response"
            continue
        seen_ids.add(ev_id)

        if ev_id not in expected_ids:
            errors[ev_id] = "id_not_in_batch"
            continue

        # Validate enums
        et = item.get("event_type")
        if et not in ALLOWED_EVENT_TYPES:
            errors[ev_id] = f"invalid_event_type:{et!r}"
            continue
        dr = item.get("direction")
        if dr not in ALLOWED_DIRECTIONS:
            errors[ev_id] = f"invalid_direction:{dr!r}"
            continue
        mg = item.get("magnitude")
        if mg not in ALLOWED_MAGNITUDES:
            errors[ev_id] = f"invalid_magnitude:{mg!r}"
            continue
        cf = item.get("confidence")
        if cf not in ALLOWED_CONFIDENCES:
            errors[ev_id] = f"invalid_confidence:{cf!r}"
            continue

        # String fields
        sub = item.get("event_subtype")
        if sub is not None and not isinstance(sub, str):
            sub = str(sub)
        h_en = item.get("headline_en")
        summ = item.get("summary")
        if not isinstance(h_en, str) or not isinstance(summ, str):
            errors[ev_id] = "headline_en_or_summary_not_string"
            continue

        valid.append(Classification(
            id=ev_id, event_type=et, event_subtype=sub,
            direction=dr, magnitude=mg, confidence=cf,
            headline_en=h_en, summary=summ,
        ))

    # Events expected but missing from response
    returned_ids = {c.id for c in valid} | set(errors.keys())
    for missing_id in expected_ids - returned_ids:
        errors[missing_id] = "missing_from_response"

    return valid, errors, None


# ---------- API client wrapper ----------

class LLMClient:
    """Wraps the OpenAI-compatible client with retry + token tracking."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: int = DEFAULT_TIMEOUT_SEC,
    ):
        from openai import OpenAI  # lazy import so tests can stub
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.max_retries = min(max_retries, len(RETRY_BACKOFF))
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_api_calls = 0
        self.total_failed_batches = 0

    def call(self, system_prompt: str, user_message: str) -> tuple[str, int, int]:
        """
        Make one API call with retries.

        Returns (response_text, input_tokens, output_tokens) on success.
        Raises RuntimeError after max retries exhausted.
        """
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self.total_api_calls += 1
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.0,
                    # response_format omitted: not all Qwen variants support it,
                    # and we parse defensively anyway.
                )
                text = resp.choices[0].message.content or ""
                usage = resp.usage
                in_tok = getattr(usage, "prompt_tokens", 0) or 0
                out_tok = getattr(usage, "completion_tokens", 0) or 0
                self.total_input_tokens += in_tok
                self.total_output_tokens += out_tok
                return text, in_tok, out_tok
            except Exception as e:
                last_err = e
                backoff = RETRY_BACKOFF[attempt]
                logger.warning(
                    "API call failed (attempt %d/%d): %s — sleeping %ds",
                    attempt + 1, self.max_retries, e, backoff,
                )
                time.sleep(backoff)
        self.total_failed_batches += 1
        raise RuntimeError(f"API call failed after {self.max_retries} attempts: {last_err}")


# ---------- DB operations ----------

REQUIRED_COLUMNS = [
    "ai_event_type", "ai_event_subtype", "ai_direction", "ai_magnitude",
    "ai_confidence", "ai_headline_en", "ai_summary",
    "classification_failed_at", "classification_error",
]


def check_schema(db_path: str) -> list[str]:
    """Return list of missing required columns; empty list means schema is ready."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("PRAGMA table_info(events)")
        existing = {row[1] for row in cur.fetchall()}
        return [c for c in REQUIRED_COLUMNS if c not in existing]
    finally:
        conn.close()


def count_unclassified(db_path: str, where_filter: str | None = None) -> int:
    conn = sqlite3.connect(db_path)
    try:
        sql = (
            "SELECT COUNT(*) FROM events "
            "WHERE classified_at IS NULL "
            "  AND classification_failed_at IS NULL"
        )
        if where_filter:
            sql += f" AND ({where_filter})"
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def fetch_unclassified_batch(
    db_path: str, batch_size: int, where_filter: str | None = None
) -> list[EventInput]:
    conn = sqlite3.connect(db_path)
    try:
        sql = (
            "SELECT id, ticker, company_name, event_date, headline "
            "FROM events "
            "WHERE classified_at IS NULL "
            "  AND classification_failed_at IS NULL"
        )
        if where_filter:
            sql += f" AND ({where_filter})"
        sql += " ORDER BY id LIMIT ?"
        rows = conn.execute(sql, (batch_size,)).fetchall()
        return [
            EventInput(id=r[0], ticker=str(r[1]), company_name=str(r[2] or ""),
                       event_date=str(r[3]), headline=str(r[4]))
            for r in rows
        ]
    finally:
        conn.close()


def write_classifications(
    db_path: str,
    classifications: list[Classification],
    validation_errors: dict[int, str],
    batch_error: str | None,
    expected_ids: set[int],
) -> None:
    """
    Persist results atomically per event_id.

    - Successful classifications: set ai_* columns + classified_at.
    - Validation errors: set classification_failed_at + classification_error.
    - Batch-level error: all expected_ids marked failed.
    """
    conn = sqlite3.connect(db_path)
    try:
        if batch_error is not None:
            for ev_id in expected_ids:
                conn.execute(
                    "UPDATE events SET "
                    "classification_failed_at = CURRENT_TIMESTAMP, "
                    "classification_error = ? "
                    "WHERE id = ?",
                    (batch_error[:500], ev_id),
                )
            conn.commit()
            return

        for c in classifications:
            conn.execute(
                "UPDATE events SET "
                "ai_event_type = ?, ai_event_subtype = ?, "
                "ai_direction = ?, ai_magnitude = ?, ai_confidence = ?, "
                "ai_headline_en = ?, ai_summary = ?, "
                "classified_at = CURRENT_TIMESTAMP, "
                "classification_failed_at = NULL, "
                "classification_error = NULL "
                "WHERE id = ?",
                (c.event_type, c.event_subtype, c.direction, c.magnitude,
                 c.confidence, c.headline_en, c.summary, c.id),
            )

        for ev_id, err in validation_errors.items():
            if ev_id < 0:
                continue  # synthetic error id for unparseable items
            conn.execute(
                "UPDATE events SET "
                "classification_failed_at = CURRENT_TIMESTAMP, "
                "classification_error = ? "
                "WHERE id = ?",
                (err[:500], ev_id),
            )

        conn.commit()
    finally:
        conn.close()


# ---------- Batch driver ----------

def process_batch(
    client: LLMClient,
    events: list[EventInput],
    dry_run: bool = False,
) -> BatchResult:
    """Single-batch end-to-end: prompt → API → parse → validate."""
    user_msg = build_user_message(events)
    expected_ids = {e.id for e in events}

    if dry_run:
        # Don't call the API in dry-run mode
        return BatchResult(batch_error="dry_run_no_api_call")

    try:
        response_text, in_tok, out_tok = client.call(SYSTEM_PROMPT, user_msg)
    except RuntimeError as e:
        return BatchResult(batch_error=f"api_failed:{e}")

    valid, errors, batch_err = parse_response(response_text, expected_ids)
    return BatchResult(
        classifications=valid,
        validation_errors=errors,
        batch_error=batch_err,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


def _write_status(status_file: str, payload: dict) -> None:
    """Atomically write status JSON. Safe to read from another process."""
    import json
    import tempfile
    import os as _os
    d = _os.path.dirname(_os.path.abspath(status_file)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with _os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        _os.replace(tmp, status_file)
    except Exception:
        try:
            _os.unlink(tmp)
        except OSError:
            pass


def reset_transient_failures(db_path: str) -> int:
    """Reset classification_failed_at for transient errors (timeout, connection, json).
    Returns the number of rows reset."""
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute(
            "UPDATE events "
            "SET classification_failed_at = NULL, classification_error = NULL "
            "WHERE classification_failed_at IS NOT NULL "
            "  AND (classification_error LIKE '%timed out%' "
            "       OR classification_error LIKE '%Connection error%' "
            "       OR classification_error LIKE '%json_decode_error%')"
        ).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def pre_filter_auto_classify(db_path: str, where_filter: str | None = None) -> int:
    """Auto-classify earnings events with no directional signal in the headline.

    Targets: 決算短信, 四半期決算, 中間期 headlines that contain NO
    sentiment keywords (上方修正, 下方修正, 増配, 減配, 特別利益,
    特別損失, 無配, 復配, 差異, 訂正).

    Sets ai_event_type=earnings, ai_direction=neutral, ai_magnitude=medium,
    ai_confidence=low, and classified_at to mark them done.  These will be
    skipped by subsequent AI batches, roughly halving the API load.

    Returns the number of events auto-classified.
    """
    base_where = (
        "classified_at IS NULL AND classification_failed_at IS NULL "
        "AND event_type = 'earnings'"
        " AND (headline LIKE '%決算短信%' OR headline LIKE '%四半期%'"
        "      OR headline LIKE '%中間期%')"
        " AND headline NOT LIKE '%訂正%'"
        " AND headline NOT LIKE '%上方修正%'"
        " AND headline NOT LIKE '%下方修正%'"
        " AND headline NOT LIKE '%増配%'"
        " AND headline NOT LIKE '%減配%'"
        " AND headline NOT LIKE '%特別利益%'"
        " AND headline NOT LIKE '%特別損失%'"
        " AND headline NOT LIKE '%無配%'"
        " AND headline NOT LIKE '%復配%'"
        " AND headline NOT LIKE '%差異%'"
        " AND headline NOT LIKE '%予想%'"
    )
    extra_filter = (
        f" AND ({where_filter})" if where_filter else ""
    )

    conn = sqlite3.connect(db_path)
    try:
        count_sql = f"SELECT COUNT(*) FROM events WHERE {base_where} {extra_filter}"
        eligible = conn.execute(count_sql).fetchone()[0]
        if eligible == 0:
            logger.info("Pre-filter: 0 events eligible for auto-classification")
            return 0

        update_sql = f"""
            UPDATE events SET
                ai_event_type = 'earnings',
                ai_event_subtype = 'quarterly_or_annual',
                ai_direction = 'neutral',
                ai_magnitude = 'medium',
                ai_confidence = 'low',
                ai_headline_en = 'Earnings Summary',
                ai_summary = 'Quarterly/annual earnings report (auto-classified: no directional signal in headline)',
                classified_at = CURRENT_TIMESTAMP
            WHERE {base_where} {extra_filter}
        """
        n = conn.execute(update_sql).rowcount
        conn.commit()
        logger.info(
            "Pre-filter: auto-classified %d neutral earnings events "
            "(no directional markers in headline). Remaining events will "
            "be sent to the LLM.",
            n,
        )
        return n
    finally:
        conn.close()


def run(
    db_path: str,
    api_key: str,
    base_url: str,
    model: str,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    delay_ms: int = DEFAULT_DELAY_MS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    where_filter: str | None = None,
    dry_run: bool = False,
    concurrency: int = 1,
    status_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    pre_filter: bool = False,
) -> dict[str, Any]:
    """Top-level orchestrator. Returns summary stats."""
    missing_cols = check_schema(db_path)
    if missing_cols:
        raise RuntimeError(
            f"DB schema missing columns: {missing_cols}. "
            "Run migrate_db.py first."
        )

    if pre_filter and not dry_run:
        n_auto = pre_filter_auto_classify(db_path, where_filter)
        if n_auto > 0:
            logger.info(
                "Pre-filter saved ~%d LLM calls (%.0f%% reduction)",
                n_auto, n_auto / max(n_auto + count_unclassified(db_path, where_filter), 1) * 100,
            )

    total = count_unclassified(db_path, where_filter)
    target = total if limit is None else min(total, limit)
    logger.info("Unclassified events: %d (processing up to %d)", total, target)

    if dry_run:
        # Show the prompt for the first batch and exit
        sample = fetch_unclassified_batch(db_path, min(batch_size, target), where_filter)
        if not sample:
            logger.info("No events to process.")
            return {"processed": 0, "successful": 0, "failed": 0}
        user_msg = build_user_message(sample)
        logger.info("=== DRY RUN — first batch prompt ===")
        logger.info("System prompt length: %d chars", len(SYSTEM_PROMPT))
        logger.info("User message:\n%s", user_msg)
        approx_in_tokens = (len(SYSTEM_PROMPT) + len(user_msg)) // 3
        approx_out_per_event = 110
        approx_total_in = approx_in_tokens * (target // batch_size + 1)
        approx_total_out = approx_out_per_event * target
        logger.info(
            "Estimated total tokens for %d events: %d in / %d out",
            target, approx_total_in, approx_total_out,
        )
        return {"processed": 0, "dry_run": True, "estimate_in": approx_total_in,
                "estimate_out": approx_total_out}

    client = LLMClient(api_key=api_key, base_url=base_url, model=model,
                       max_retries=max_retries, timeout=timeout)

    processed = 0
    n_ok = 0
    n_failed = 0
    start = time.time()

    if concurrency <= 1:
        # Sequential path — preserves original behavior for debugging
        while processed < target:
            remaining = target - processed
            bs = min(batch_size, remaining)
            events = fetch_unclassified_batch(db_path, bs, where_filter)
            if not events:
                logger.info("No more unclassified events available.")
                break

            result = process_batch(client, events, dry_run=False)
            write_classifications(
                db_path, result.classifications, result.validation_errors,
                result.batch_error, expected_ids={e.id for e in events},
            )

            batch_ok = len(result.classifications)
            batch_failed = len(events) - batch_ok
            n_ok += batch_ok
            n_failed += batch_failed
            processed += len(events)

            elapsed = time.time() - start
            rate = processed / elapsed if elapsed > 0 else 0
            eta_sec = (target - processed) / rate if rate > 0 else 0

            logger.info(
                "Batch done | size=%d ok=%d fail=%d | total %d/%d | "
                "tokens in=%d out=%d | rate=%.1f ev/s | ETA=%.0fs",
                len(events), batch_ok, batch_failed,
                processed, target,
                client.total_input_tokens, client.total_output_tokens,
                rate, eta_sec,
            )

            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
    else:
        # Concurrent path — fan out batches across workers
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        db_lock = threading.Lock()  # SQLite writes are serialized

        # Pre-fetch all batch IDs upfront to avoid race on fetch_unclassified_batch
        all_remaining = fetch_unclassified_batch(db_path, target, where_filter)
        # Slice into batches
        batches = [all_remaining[i:i + batch_size]
                   for i in range(0, len(all_remaining), batch_size)]
        logger.info("Dispatching %d batches across %d workers ...",
                    len(batches), concurrency)

        def process_and_write(events_in_batch):
            result = process_batch(client, events_in_batch, dry_run=False)
            with db_lock:
                write_classifications(
                    db_path, result.classifications, result.validation_errors,
                    result.batch_error, expected_ids={e.id for e in events_in_batch},
                )
            return len(events_in_batch), len(result.classifications)

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {ex.submit(process_and_write, b): b for b in batches}
            for fut in as_completed(futures):
                try:
                    n_batch, n_batch_ok = fut.result()
                except Exception as e:
                    logger.error("Worker failed unexpectedly: %s", e)
                    continue
                n_ok += n_batch_ok
                n_failed += (n_batch - n_batch_ok)
                processed += n_batch
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed > 0 else 0
                eta_sec = (target - processed) / rate if rate > 0 else 0
                logger.info(
                    "Progress | %d/%d (ok=%d fail=%d) | "
                    "tokens in=%d out=%d | rate=%.1f ev/s | ETA=%.0fs",
                    processed, target, n_ok, n_failed,
                    client.total_input_tokens, client.total_output_tokens,
                    rate, eta_sec,
                )
                # Heartbeat status file (atomic write via rename)
                if status_file:
                    _write_status(status_file, {
                        "processed": processed,
                        "target": target,
                        "ok": n_ok,
                        "failed": n_failed,
                        "input_tokens": client.total_input_tokens,
                        "output_tokens": client.total_output_tokens,
                        "rate_ev_per_sec": round(rate, 2),
                        "eta_seconds": round(eta_sec),
                        "elapsed_seconds": round(elapsed),
                        "last_update": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })

    logger.info("=" * 60)

    elapsed = time.time() - start
    logger.info("Done. processed=%d ok=%d failed=%d in %.1fs",
                processed, n_ok, n_failed, elapsed)
    logger.info("Total tokens: in=%d out=%d | API calls=%d | failed batches=%d",
                client.total_input_tokens, client.total_output_tokens,
                client.total_api_calls, client.total_failed_batches)

    return {
        "processed": processed,
        "successful": n_ok,
        "failed": n_failed,
        "input_tokens": client.total_input_tokens,
        "output_tokens": client.total_output_tokens,
        "api_calls": client.total_api_calls,
        "failed_batches": client.total_failed_batches,
        "elapsed_sec": elapsed,
    }


# ---------- CLI ----------

def main():
    # Load .env from cwd or parent dirs (e.g., E:\TSE_EventBase\.env)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # dotenv optional; values can come from process env

    p = argparse.ArgumentParser(description="AI classifier for TSE_EventBase events.")
    p.add_argument("--db", required=True, help="Path to eventbase.db SQLite file")
    p.add_argument("--api-key",
                   default=os.getenv("OPENAI_API_KEY") or os.getenv("ALIBABA_API_KEY"),
                   help="API key. Default reads OPENAI_API_KEY or ALIBABA_API_KEY from env/.env")
    p.add_argument("--base-url",
                   default=os.getenv("OPENAI_BASE_URL"),
                   help="OpenAI-compatible base URL. Default reads OPENAI_BASE_URL from env/.env")
    p.add_argument("--model",
                   default=os.getenv("MODEL"),
                   help="Model name. Default reads MODEL from env/.env")
    p.add_argument("--limit", type=int, default=None,
                   help="Max events to classify (smoke testing). Omit for all.")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument("--delay-ms", type=int, default=DEFAULT_DELAY_MS,
                   help="Delay between batches in milliseconds.")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                   help="API request timeout in seconds (default 60). "
                        "Increase for larger batch sizes (e.g. 120 for batch-size=50).")
    p.add_argument("--concurrency", type=int, default=1,
                   help="Number of concurrent API workers (default 1 = sequential). "
                        "8 is a reasonable default for high-throughput plans.")
    p.add_argument("--status-file", default=None,
                   help="If set, write running progress JSON to this file each batch. "
                        "Lets you monitor a long run via `type <file>` from another shell.")
    p.add_argument("--filter", default=None,
                   help="Extra SQL WHERE clause (e.g. \"event_type='earnings'\")")
    p.add_argument("--in-scope-only", action="store_true",
                   help="Classify only sentiment-relevant event types: "
                        "earnings, forecast_revision, dividend, buyback, ma, tender_offer. "
                        "Equivalent to --filter but avoids shell quoting issues.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print sample prompt + token estimates, don't call API.")
    p.add_argument("--reset-failures", action="store_true",
                   help="Reset transient failures (timeout/connection/json) before starting. "
                        "Previously-failed events become retryable.")
    p.add_argument("--pre-filter", action="store_true",
                   help="Auto-classify neutral earnings reports (決算短信 etc. without "
                        "directional keywords) before the main LLM run. Roughly halves "
                        "API calls by skipping events that are unambiguously neutral.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Reset transient failures before checking credentials or launching
    if args.reset_failures:
        n_reset = reset_transient_failures(args.db)
        if n_reset > 0:
            logger.info("Reset %d transient failures for retry", n_reset)
        else:
            logger.info("No transient failures to reset")

    if not args.dry_run:
        missing = []
        if not args.api_key:
            missing.append("OPENAI_API_KEY (api key)")
        if not args.base_url:
            missing.append("OPENAI_BASE_URL (endpoint URL)")
        if not args.model:
            missing.append("MODEL (model name)")
        if missing:
            logger.error("Missing required config: %s", ", ".join(missing))
            logger.error("Set in .env or as environment variables. Example:")
            logger.error("  OPENAI_API_KEY=sk-...")
            logger.error("  OPENAI_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
            logger.error("  MODEL=qwen3.6-plus")
            sys.exit(2)

    # Resolve the filter
    IN_SCOPE_FILTER = (
        "event_type IN ('earnings','forecast_revision','dividend',"
        "'buyback','ma','tender_offer')"
    )
    where_filter = args.filter
    if args.in_scope_only:
        where_filter = IN_SCOPE_FILTER
        logger.info("Using in-scope filter: %s", IN_SCOPE_FILTER)

    try:
        summary = run(
            db_path=args.db,
            api_key=args.api_key or "DRY_RUN",
            base_url=args.base_url,
            model=args.model,
            limit=args.limit,
            batch_size=args.batch_size,
            delay_ms=args.delay_ms,
            max_retries=args.max_retries,
            where_filter=where_filter,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
            status_file=args.status_file,
            timeout=args.timeout,
            pre_filter=args.pre_filter,
        )
        logger.info("Summary: %s", summary)
        return 0
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())