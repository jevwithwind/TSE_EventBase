"""
LLM-as-judge cross-validation per Zheng et al. (2023, MT-Bench).

Runs a second classifier on events already classified by the primary model,
using a different LLM (e.g. qwen3.6-plus while primary is deepseek-v3.2,
or vice versa). Computes inter-model agreement.

Adds judge_* columns to events table. Idempotent: skips events already
judged.

Usage:
    set JUDGE_MODEL=qwen3.6-plus
    python llm_judge.py --db data/tse_eventbase.db --limit 2000
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure sibling modules are importable
_this_dir = Path(__file__).resolve().parent
_parent_dir = str(_this_dir.parent)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from classifier import (  # noqa: E402
    ALLOWED_CONFIDENCES,
    ALLOWED_DIRECTIONS,
    ALLOWED_EVENT_TYPES,
    ALLOWED_MAGNITUDES,
    DEFAULT_BATCH_SIZE,
    DEFAULT_DELAY_MS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SEC,
    EventInput,
    LLMClient,
    RETRY_BACKOFF,
    SYSTEM_PROMPT,
    build_user_message,
    parse_response,
)

logger = logging.getLogger(__name__)


# ---------- Judge-specific DB operations ----------

JUDGE_REQUIRED_COLUMNS = [
    "judge_event_type", "judge_event_subtype", "judge_direction",
    "judge_magnitude", "judge_confidence", "judge_headline_en",
    "judge_summary", "judge_classified_at", "judge_failed_at",
    "judge_error",
]


def check_judge_schema(db_path: str) -> list[str]:
    """Return list of missing judge columns; empty list means schema is ready."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("PRAGMA table_info(events)")
        existing = {row[1] for row in cur.fetchall()}
        return [c for c in JUDGE_REQUIRED_COLUMNS if c not in existing]
    finally:
        conn.close()


def count_judgeable(db_path: str) -> int:
    """Count events classified by primary but not yet judged."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE classified_at IS NOT NULL "
            "  AND judge_classified_at IS NULL "
            "  AND judge_failed_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def fetch_judge_batch(db_path: str, batch_size: int) -> list[EventInput]:
    """Fetch next batch of primary-classified events not yet judged."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, ticker, company_name, event_date, headline "
            "FROM events "
            "WHERE classified_at IS NOT NULL "
            "  AND judge_classified_at IS NULL "
            "  AND judge_failed_at IS NULL "
            "ORDER BY id LIMIT ?",
            (batch_size,),
        ).fetchall()
        return [
            EventInput(id=r[0], ticker=str(r[1]), company_name=str(r[2] or ""),
                       event_date=str(r[3]), headline=str(r[4]))
            for r in rows
        ]
    finally:
        conn.close()


def write_judge_classifications(
    db_path: str,
    classifications: list[Any],  # list[Classification]
    validation_errors: dict[int, str],
    batch_error: str | None,
    expected_ids: set[int],
) -> None:
    """Persist judge results atomically per event_id."""
    conn = sqlite3.connect(db_path)
    try:
        if batch_error is not None:
            for ev_id in expected_ids:
                conn.execute(
                    "UPDATE events SET "
                    "judge_failed_at = CURRENT_TIMESTAMP, "
                    "judge_error = ? "
                    "WHERE id = ?",
                    (batch_error[:500], ev_id),
                )
            conn.commit()
            return

        for c in classifications:
            conn.execute(
                "UPDATE events SET "
                "judge_event_type = ?, judge_event_subtype = ?, "
                "judge_direction = ?, judge_magnitude = ?, judge_confidence = ?, "
                "judge_headline_en = ?, judge_summary = ?, "
                "judge_classified_at = CURRENT_TIMESTAMP, "
                "judge_failed_at = NULL, "
                "judge_error = NULL "
                "WHERE id = ?",
                (c.event_type, c.event_subtype, c.direction, c.magnitude,
                 c.confidence, c.headline_en, c.summary, c.id),
            )

        for ev_id, err in validation_errors.items():
            if ev_id < 0:
                continue
            conn.execute(
                "UPDATE events SET "
                "judge_failed_at = CURRENT_TIMESTAMP, "
                "judge_error = ? "
                "WHERE id = ?",
                (err[:500], ev_id),
            )

        conn.commit()
    finally:
        conn.close()


# ---------- Batch driver ----------

@dataclass
class BatchResult:
    classifications: list[Any] = None
    validation_errors: dict[int, str] = None
    batch_error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self):
        if self.classifications is None:
            self.classifications = []
        if self.validation_errors is None:
            self.validation_errors = {}


def process_judge_batch(
    client: LLMClient,
    events: list[EventInput],
) -> BatchResult:
    """Single-batch end-to-end: prompt -> API -> parse -> validate."""
    user_msg = build_user_message(events)
    expected_ids = {e.id for e in events}

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


def run(
    db_path: str,
    api_key: str,
    base_url: str,
    model: str,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    delay_ms: int = DEFAULT_DELAY_MS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Top-level orchestrator. Returns summary stats."""
    missing_cols = check_judge_schema(db_path)
    if missing_cols:
        raise RuntimeError(
            f"DB schema missing judge columns: {missing_cols}. "
            "Run migrate_judge_columns.py first."
        )

    total = count_judgeable(db_path)
    target = total if limit is None else min(total, limit)
    logger.info("Judgeable events: %d (processing up to %d)", total, target)

    if total == 0:
        logger.info("No judgeable events found. Run the primary classifier first.")
        return {"processed": 0, "successful": 0, "failed": 0}

    client = LLMClient(api_key=api_key, base_url=base_url, model=model,
                       max_retries=max_retries)

    processed = 0
    n_ok = 0
    n_failed = 0
    start = time.time()

    while processed < target:
        remaining = target - processed
        bs = min(batch_size, remaining)
        events = fetch_judge_batch(db_path, bs)
        if not events:
            logger.info("No more judgeable events available.")
            break

        result = process_judge_batch(client, events)
        write_judge_classifications(
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
            "Judge batch | size=%d ok=%d fail=%d | total %d/%d | "
            "tokens in=%d out=%d | rate=%.1f ev/s | ETA=%.0fs",
            len(events), batch_ok, batch_failed,
            processed, target,
            client.total_input_tokens, client.total_output_tokens,
            rate, eta_sec,
        )

        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info("Judge done. processed=%d ok=%d failed=%d in %.1fs",
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
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    p = argparse.ArgumentParser(description="LLM-as-judge cross-validation.")
    p.add_argument("--db", required=True, help="Path to eventbase.db SQLite file")
    p.add_argument("--api-key",
                   default=os.getenv("OPENAI_API_KEY") or os.getenv("ALIBABA_API_KEY"),
                   help="API key (default: OPENAI_API_KEY or ALIBABA_API_KEY from env)")
    p.add_argument("--base-url",
                   default=os.getenv("OPENAI_BASE_URL"),
                   help="OpenAI-compatible base URL (default: OPENAI_BASE_URL from env)")
    p.add_argument("--model",
                   default=os.getenv("JUDGE_MODEL", "MODEL_NOT_SET"),
                   help="Model name for judging (default: JUDGE_MODEL from env)")
    p.add_argument("--limit", type=int, default=None,
                   help="Max events to judge (default: all primary-classified)")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument("--delay-ms", type=int, default=DEFAULT_DELAY_MS,
                   help="Delay between batches in milliseconds.")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    missing = []
    if not args.api_key:
        missing.append("OPENAI_API_KEY (api key)")
    if not args.base_url:
        missing.append("OPENAI_BASE_URL (endpoint URL)")
    if args.model == "MODEL_NOT_SET":
        logger.error(
            "JUDGE_MODEL is not set. Please set the JUDGE_MODEL environment variable "
            "or pass --model. Example: set JUDGE_MODEL=qwen3.6-plus"
        )
        sys.exit(2)
    if missing:
        logger.error("Missing required config: %s", ", ".join(missing))
        logger.error("Set in .env or as environment variables.")
        sys.exit(2)

    try:
        summary = run(
            db_path=args.db,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            limit=args.limit,
            batch_size=args.batch_size,
            delay_ms=args.delay_ms,
            max_retries=args.max_retries,
        )
        logger.info("Summary: %s", summary)
        return 0
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
