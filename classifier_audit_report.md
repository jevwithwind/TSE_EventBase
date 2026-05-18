# AI Classifier Audit Report

**Date:** 2026-05-12
**Audited files:** 5 (run_ai_classify.py, event_classifier.py, __init__.py, config.py, .env)
**Repo commit:** `010b2ae` (2026-04-24) — "Switch from Anthropic to OpenAI-compatible API"

---

## File Inventory

| File | Lines | Last Modified | Role |
|---|---|---|---|
| `run_ai_classify.py` | 132 | 2026-04-24 | Entry point: parses CLI args, connects to DB, drives classify_filtered_events |
| `classifier/event_classifier.py` | 347 | 2026-04-24 | Core: DB queries, prompt construction, API calls, response parsing, DB writes |
| `classifier/__init__.py` | 0 | 2026-04-24 | Empty package init |
| `config.py` | 64 | 2026-04-24 | Loads .env, provides OPENAI_API_KEY, MODEL, CLASSIFICATION_BATCH_SIZE, DB_PATH |
| `.env` | 21 | 2026-05-12 | API key (empty), base URL (DashScope), model name |

---

## Verdict Summary

| Criterion | Status | Severity | Note |
|---|---|---|---|
| Prompt quality | FAIL | High | No few-shot examples; asks for direction from headline only (can't see numbers); English-only prompt for Japanese headlines |
| Structured output enforcement | CONCERN | Medium | JSON structure specified but LLM values never validated against allowed enum sets |
| Robust parsing (graceful failures) | FAIL | Critical | Any error returns `[]`, entire batch silently dropped; **infinite tight loop on persistent errors** |
| Silent drops impossible | FAIL | Critical | ALL failure paths drop the entire batch silently. No retry. No marking-events-as-failed. Logged but not recoverable. |
| Batching efficient | PASS | — | 50 events/batch. Prompt format is clear about which output maps to which input (Event 1, Event 2, ...). |
| Rate limiting present | FAIL | Critical | No sleep, no backoff, no retry-on-429. Error loop hammers API indefinitely. |
| Checkpointing on success | PASS | — | Each event written to DB immediately after classification. UPDATE includes classified_at timestamp. |
| Resume on restart | FAIL | Critical | Resume logic depends on `event_type IS NULL` — but keyword categorizer already populated ALL event_type values. AI classifier would find 0 events to classify on current DB. |
| LLM-agnostic base URL | CONCERN | Medium | Accepts OPENAI_BASE_URL env var, but .env has a non-standard DashScope URL and an invalid model name (`qwen3.6-plus`). |
| 100-event dry run feasible | FAIL | High | No `--limit` flag. Dry run only counts matching events without classifying. Requires code modification to test. |

**Overall: 4 PASS, 1 CONCERN, 5 FAIL (3 Critical). UNsafe to run as-is.**

---

## Detailed Findings

### Step 2: Prompt Inspection

#### Verbatim prompt template (event_classifier.py:126-167)

```
You are an expert financial analyst specializing in Japanese corporate events.
Your task is to classify corporate events from the Tokyo Stock Exchange based on
their headlines and context.

For each event, please provide the following classifications:

1. event_type: One of the following categories:
   - earnings: Earnings announcements (決算短信, 四半期決算, 通期決算)
   - forecast_revision: Changes to earnings forecasts (業績修正)
   - dividend: Dividend announcements (配当, 中間配当)
   - buyback: Share buybacks (自己株式買付, 株式買還)
   - ma: Mergers and acquisitions (M&A, 合併, 増資, 株式交換)
   - tender_offer: Tender offers (TOB, 証券公開買い付け)
   - leadership_change: Leadership changes (代表取締役変更, 社長交代)
   - stock_split: Stock splits or consolidations (株式分割, 株式併合)
   - large_holding: Large shareholding notifications (大量保有, 5%超保有)
   - capital_raise: Capital raising activities (新株発行, 第三者割当)
   - delisting: Delisting announcements (上場廃止, 上場維持困難)
   - other: Any other type of announcement

2. direction: Market sentiment impact (positive, negative, neutral)

3. magnitude: Impact scale (large, medium, small)

4. headline_en: English translation of the headline

5. summary: Brief English summary of the event

Please return your response as valid JSON with the following structure:
{
  "classifications": [
    {
      "id": <event_id>,
      "event_type": "<type>",
      "event_subtype": "<more_specific_type_if_applicable>",
      "direction": "<direction>",
      "magnitude": "<magnitude>",
      "headline_en": "<english_translation>",
      "summary": "<english_summary>"
    }
  ]
}

Be accurate and consistent in your classifications. If you cannot determine
a classification with confidence, use 'other' for event_type.
```

User prompt (event_classifier.py:169-173):
```
Please classify the following events:

<Event 1: ID, Ticker, Company, Date, Headline>
<Event 2: ...>

Return only the JSON response with classifications for all events.
```

#### Prompt Evaluation

| Criterion | Result | Justification |
|---|---|---|
| Structured JSON output requested? | PASS | JSON schema is explicitly shown with field names and types (line 152-165) |
| Enum values specified? | PASS | event_type (12 values, line 130-142), direction (3 values, line 144), magnitude (3 values, line 146) listed verbatim |
| Few-shot examples? | **FAIL** | Zero examples. The model has no anchor for correct classification patterns. |
| Prompt language? | CONCERN | Entirely English. All headlines are Japanese (avg 27 chars). Translation quality depends entirely on model. |
| Headline only or headline+metadata? | **FAIL** | `raw_json` is fetched from DB (line 62, 91-98 `'raw_json': row[5]`) but **never passed to the prompt** (lines 118-124 only transmit `id`, `ticker`, `company_name`, `event_date`, `headline`). The TDnet raw_json contains structured URLs for financial reports, XBRL links, and market strings — all of which could improve classification accuracy and are discarded. |
| Could it leak the answer? | CONCERN | Asking for `event_type` from headline alone is reasonable (keywords exist in the headline). But asking for `direction` and `magnitude` implies the model should infer market sentiment from the headline text alone — without seeing numbers. The model may hallucinate direction with high confidence. |
| Direction inference feasible? | **FAIL** | For earnings events, direction (positive/negative/neutral) depends on comparing reported numbers against forecasts — data the model **cannot see**. The prompt does not acknowledge this limitation. The model will guess based on headline tone or contextual cues, producing unreliable direction labels. |

---

### Step 3: Output Parsing (event_classifier.py:175-211)

The parsing function is `classify_events_batch()` (line 103). The response handling is at lines 186-211:

```python
response_text = response.choices[0].message.content.strip()        # line 187
if response_text.startswith("```json"):                              # line 190
    response_text = response_text[7:]
if response_text.endswith("```"):                                    # line 192
    response_text = response_text[:-3]
result = json.loads(response_text)                                   # line 196
if 'classifications' not in result:                                   # line 199
    logger.error(f"Invalid response structure: {response_text}")
    return []                                                        # line 201
return result['classifications']                                     # line 203
```

Error paths:
```python
except json.JSONDecodeError as e:                                     # line 205
    logger.error(f"Error parsing JSON response: {e}")
    return []                                                        # line 208
except Exception as e:                                                # line 209
    logger.error(f"Error calling OpenAI-compatible API: {e}")
    return []                                                        # line 211
```

#### Failure Scenario Trace Table

| Scenario | Behavior | Raises? | Retries? | DB effect | Events Dropped? |
|---|---|---|---|---|---|
| Valid JSON, all fields present | Returns classification list. Updates DB one-by-one (lines 312-318). | No | No | event_type, event_subtype, direction, magnitude, headline_en, summary, classified_at set | No |
| Valid JSON, some fields missing | `classification.get('missing_field')` returns `None` (line 230-235). DB stores `None`/NULL. | No | No | Missing fields stored as NULL — **no validation error** | No, but silently corrupt |
| Malformed JSON (extra text) | `json.JSONDecodeError` at line 205. Returns `[]`. | No | **No** | Nothing written to DB | **YES — ENTIRE BATCH** |
| LLM refusal ("I cannot determine...") | Depends on format. If JSON, likely passes parsing but may have different keys. If plain text, `json.JSONDecodeError` → returns `[]`. | No | **No** | Nothing written | **YES — ENTIRE BATCH** |
| API timeout | Generic `Exception` at line 209. Returns `[]`. | No | **No** | Nothing written | **YES — ENTIRE BATCH** |
| API 4xx/5xx | Generic `Exception` at line 209. Returns `[]`. | No | **No** | Nothing written | **YES — ENTIRE BATCH** |

#### The Silent Drop + Infinite Loop Problem (Critical)

The caller loop at lines 294-323:

```python
while True:                                                          # line 294
    events = self.get_filtered_unclassified_events(                  # line 296
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
        limit=batch_size
    )
    if not events:                                                    # line 302
        break                                                         # line 303
    classifications = self.classify_events_batch(events)              # line 308
    for classification in classifications:                            # line 312
        ...                                                           # lines 313-318
```

When `classify_events_batch()` returns `[]` (any failure), the `for` loop at line 312 iterates zero times. The while loop continues. `get_filtered_unclassified_events()` re-queries the **exact same events** (they still have NULL event_type). The API is called again. If the error is persistent (rate limit, bad prompt, model unavailable), this becomes a **tight infinite loop with zero inter-request delay**.

- **No `time.sleep()` anywhere in the classifier module.**
- **No exponential backoff.**
- **No retry counter.**
- **No failure threshold to mark events as failed.**

This loop will hammer the API indefinitely at the maximum rate the `openai` SDK allows, burning credits and never making progress.

---

### Step 4: Batching, Concurrency, Rate Limiting

| Criterion | Finding | Status |
|---|---|---|
| Batching model | Multiple events per API call | PASS |
| Batch size | Default 50, configurable via `CLASSIFICATION_BATCH_SIZE` env var or `--batch-size` CLI arg (`config.py:60`, `run_ai_classify.py:24`) | PASS |
| Output matching | Each event numbered "Event 1", "Event 2" with `id` field for correlation (lines 118-124). Format: `Event {i+1}:\nID: {id}\n...` | PASS |
| Concurrency | None. Single-threaded synchronous execution. | N/A |
| Rate limiting | **None.** No `time.sleep()` between batches. No retry-on-429. Rate limits are entirely external (API provider enforced). | **FAIL** |
| Exponential backoff | **None.** The `except Exception` at line 209 catches everything but returns `[]` immediately with no backoff. | **FAIL** |
| Retry on 429 | **None.** Not handled. The OpenAI SDK may have built-in retry for 429 but the try/except at lines 205-211 catches all exceptions and short-circuits to `return []`. If the SDK raises before retrying, the whole batch is lost. | **FAIL** |

**Concrete consequence:** With batch_size=50 and 747,234 events, that's ~14,945 API calls. At a conservative 3 seconds per call (DashScope), that's ~12.5 hours. Without any rate limiting, the script will attempt to run at wire speed, which almost any provider will rate-limit or bill rapidly.

---

### Step 5: Checkpointing and Resume

#### How state is written

Each classified event is written to DB immediately after the API response for its batch is parsed (line 316 `update_event_classification(event_id, classification)`). The UPDATE at lines 224-238:

```sql
UPDATE events
SET event_type = ?, event_subtype = ?, direction = ?, magnitude = ?,
    headline_en = ?, summary = ?, classified_at = CURRENT_TIMESTAMP
WHERE id = ?
```

Writes are committed immediately (line 239 `conn.commit()` within `update_event_classification`). Each event gets its own connection (line 221 `conn = sqlite3.connect()`) — **not batched in a transaction**. This is safe (each event committed before the next), but inefficient for 747K events.

#### Resume logic (how it finds unclassified events)

The query at lines 61-64:
```sql
SELECT id, ticker, company_name, event_date, headline, raw_json
FROM events
WHERE event_type IS NULL OR event_type = ''
```

**Critical problem: This query will return 0 events on the current database.** The keyword-based categorizer (`run_categorize.py`) has already populated `event_type` for all 747,234 events (confirmed in prior audit — 0% missing). The AI classifier would find nothing to do.

The two classifiers **compete for the same `event_type` column**:
- `run_categorize.py`: sets event_type via SQL LIKE
- `run_ai_classify.py`: sets event_type via LLM (and all other columns)

The resume filter should check `classified_at IS NULL`, not `event_type IS NULL`, since the intent is to classify events that haven't been AI-classified yet (regardless of keyword classification).

#### Ctrl-C behavior

If the script is killed:
- **Mid-API-call:** The API request might complete server-side but the response is lost. ~50 events in that batch are not classified but will be re-fetched. The API call counts against billing.
- **Mid-DB-write (after JSON parsed):** Each `update_event_classification` is an atomic UPDATE+COMMIT. Events already committed are safe. Any in-progress event fails gracefully (the UPDATE would have committed or not). Events not yet written from the current batch are lost but will be re-fetched.
- **Overall:** Partially resilient — no data corruption, but no transactional batch guarantee either.

#### Checkpointing verdict

| Criterion | Result |
|---|---|
| Immediate DB write on success | PASS (line 239) |
| Resume filter correct | **FAIL** — checks `event_type IS NULL` instead of `classified_at IS NULL` (line 64) |
| Batch atomicity | N/A — not batched per event |
| Survives Ctrl-C without corruption | PASS |
| Survives Ctrl-C without data loss | CONCERN — ~50 events lost if killed mid-batch, but re-fetched on restart |

---

### Step 6: Cost Projection

#### Configured model

- `config.py:21`: `MODEL = os.getenv("MODEL", "gpt-4o")` — default is GPT-4o
- `.env:21`: `MODEL=qwen3.6-plus` — **overrides to an invalid model name**
- `.env:18`: `OPENAI_BASE_URL=https://coding.dashscope.aliyuncs.com/v1` — points to Alibaba DashScope

`qwen3.6-plus` is **not a real Qwen model name**. Valid DashScope model names include: `qwen-max`, `qwen-plus`, `qwen-turbo`, `qwen3-235b-a22b`. The `.env` value appears to be a typo. The base URL `coding.dashscope.aliyuncs.com` also differs from the documented `dashscope.aliyuncs.com/compatible-mode/v1`.

#### Token estimates (per-event, batch_size=50)

| Component | Chars | Tokens (est.) |
|---|---|---|
| System prompt (amortized ÷50) | ~31 | ~8 |
| User header (amortized ÷50) | ~2 | ~1 |
| Per-event input (ID + ticker + company + date + headline) | ~94 | ~47 |
| **Total input per event** | | **~55** |
| Per-event output (JSON with headline_en + summary) | ~438 | ~110 |
| **Total output per event** | | **~110** |

#### Cost for 747,234 events

| Model | Input (41.4M tokens) | Output (81.8M tokens) | **Total** |
|---|---|---|---|
| GPT-4o ($2.50 / $10 per 1M) | $104 | $818 | **~$922** |
| Qwen-Plus ($0.28 / $1.14 per 1M) | $12 | $93 | **~$105** |
| Qwen-Max ($0.57 / $2.28 per 1M) | $24 | $187 | **~$210** |

#### API calls

- **~14,945 API calls** (747,234 ÷ 50)
- At ~3 seconds/call: **~12.5 hours**
- At ~10 seconds/call (rate-limited): **~41.5 hours**

#### LLM-agnostic compatibility

The classifier accepts `OPENAI_BASE_URL` (`event_classifier.py:25-28`), so any OpenAI-compatible endpoint works. However:
- `.env:21` has the invalid model name (`qwen3.6-plus`), which will cause a runtime error on any provider.
- `.env:18` has the non-standard URL segment (`/v1` vs standard `/compatible-mode/v1` for DashScope), which may also cause connection failures.

---

### Step 7: Smoke Test Feasibility

The script has a `--dry-run` flag (`run_ai_classify.py:30-31`) but it **only prints the count of matching events** — it does not classify a small sample:

```python
if args.dry_run:
    logger.info("Dry run completed. No events were classified.")
    print("Dry run completed. No events were classified.")
    conn.close()
    return 0
```

There is **no `--limit` flag** on the entry point. The only way to classify a subset is:
1. Modify `get_filtered_unclassified_events()` to accept and enforce a limit parameter (it already has a `limit` parameter at line 46, but `classify_filtered_events` always calls it with `limit=batch_size`, line 299).
2. Manually add `LIMIT 100` to the SQL query.
3. Run with `--filter` to restrict to specific keyword-matched headlines, reducing the pool.

A 100-event dry run therefore **requires code modification** — there is no built-in mechanism.

---

## Recommendation

### REWRITE — fundamental issues, propose new classifier from scratch

The current classifier has **three critical bugs** that make it unsafe to run on 747K events:

1. **Infinite error loop** (lines 294-323): Any persistent API error causes the script to hammer the API in a tight loop with no backoff, burning credits indefinitely until manually killed.

2. **Resume logic broken** (line 64): The WHERE clause checks `event_type IS NULL` but the keyword categorizer has already populated event_type for all events. The AI classifier would find 0 events and immediately exit. To work around this, you'd need to clear event_type for all events (losing the keyword classification) or modify the query.

3. **Model name invalid** (`.env:21`): `qwen3.6-plus` is not a real model name. The base URL may also be non-standard for DashScope.

**Do not run this code in production without fixing these issues.**

### Required fixes before any run (estimated 4-8 hours)

| Fix | Priority | Effort |
|---|---|---|
| Relocate resume filter from `event_type IS NULL` to `classified_at IS NULL` | Critical | 5 min |
| Add `time.sleep()` between batch API calls | Critical | 1 line |
| Add retry with exponential backoff on 429/timeout errors | Critical | 20 lines |
| Add max-retry counter to break the while loop on persistent failure | Critical | 5 lines |
| Fix model name in `.env` to valid Qwen model | Critical | 1 line |
| Add result validation — reject classifications where direction ∉ {positive,negative,neutral} | High | 10 lines |
| Add `--limit` flag for smoke testing | High | 5 lines |
| Pass raw_json metadata to prompt (at minimum: doc_type hints, market segment) | Medium | 10 lines |
| Add few-shot examples to prompt (2-3 per event_type) | Medium | 30 lines |
| Acknowledge in prompt that direction is inferred from headline text only, not numbers | Medium | 5 lines |
| Batch DB writes into transactions (not one-connection-per-event) | Low | 20 lines |
