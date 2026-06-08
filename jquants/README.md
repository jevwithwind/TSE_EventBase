# Stage 2 — J-Quants Financial Enrichment

This package adds **objective, data-driven sentiment** to the pipeline by pulling
actual reported financials from the **J-Quants API** and comparing them to the
company's **own prior forecast** — the real "earnings surprise vs. management
guidance".

It fills the gap the headline-only stages cannot: TDnet (Yanoshin) tells us
*when / who / what kind* of disclosure happened, but not *how the numbers came
out*. Stage 1's LLM prompt even says so explicitly — *"You do NOT see actual
financial numbers (EPS, revenue, etc)."* Stage 2 supplies them.

```
events (TDnet: when / who / what)                jquants_statements (the numbers)
        │                                                  │
        └──────────────┐                ┌──────────────────┘
                        ▼                ▼
                 stage2_financial.py  →  events.data_* (beat / miss vs. forecast)
```

---

## How it fits the three-stage pipeline

| Stage | Source | Signal | Script |
|---|---|---|---|
| 0 | `prices` | market-implied (overnight return) | `classifier_v2/stage0_prices.py` |
| 1 | headline | LLM-inferred | `classifier_v2/classifier.py` |
| **2** | **J-Quants `/fins/summary`** | **fundamentals: actual vs. forecast** | **`classifier_v2/stage2_financial.py`** |

Stage 2 is **independent** of Stages 0/1: it writes its own `data_*` columns and
does not touch `ai_*`. You can run it before or after the AI stages.

---

## One-time setup

1. **Get a key.** J-Quants V2 authenticates with a **dashboard-issued API key**
   (your email/password is only for logging into the dashboard to issue/rotate
   the key — it is *not* used at call time). Issue one at
   <https://jpx-jquants.com/>.

2. **Configure it.** Add to `.env` (gitignored):
   ```ini
   JQUANTS_API_KEY=your_key_here
   ```

3. **Install the client** (already in `requirements.txt`):
   ```bash
   pip install jquants-api-client
   ```

4. **Migrate the database** (idempotent; adds `jquants_statements` + the
   `data_*` columns to `events`):
   ```bash
   python jquants/migrate_db.py --db data/tse_eventbase.db
   ```

---

## Usage

```bash
# 0) Probe — ONE live call to confirm the columns your plan returns.
python run_jquants.py --probe

# 1) Fetch financial summaries into jquants_statements (idempotent, cached).
#    Standard plan covers ~10 years; pad to your event range.
python run_jquants.py --start-date 2016-01-01 --end-date 2025-12-31

# 2) Enrich events with the beat-vs-forecast signal (dry-run first).
python classifier_v2/stage2_financial.py --db data/tse_eventbase.db --dry-run
python classifier_v2/stage2_financial.py --db data/tse_eventbase.db
```

`run_jquants.py` is **resumable**: each trading day is cached under
`data/jquants_cache/<year>/` and rows are de-duplicated on the J-Quants
disclosure id, so re-running only fetches what's missing.

`stage2_financial.py` is **resumable** too (it skips events where
`data_enriched_at` is set) and **re-computable** (`--reset` clears `data_*` so
you can recompute after changing thresholds or fetching more statements).

---

## The signal

For each disclosure we look back to the most recent **prior** disclosure for the
**same fiscal year** (`current_fy_end`) that carried a forecast, then:

- **`actual_vs_forecast`** — only when the full-year actual is in
  (`period_type = 'FY'`), because quarterly actuals are cumulative-to-date and
  not comparable to a full-year forecast:

  ```
  surprise = (actual − prior_forecast) / |prior_forecast|
  ```

- **`forecast_revision`** — otherwise, when this disclosure itself carries a
  forecast (a quarterly report or an explicit guidance revision):

  ```
  surprise = (new_forecast − prior_forecast) / |prior_forecast|
  ```

`|prior_forecast|` in the denominator keeps the sign meaningful even for losses
(a smaller-than-forecast loss is a *positive* surprise).

**Metric by event type:** `earnings` and `forecast_revision` events use the
**EPS** signal; `dividend` events use the **DPS** (annual dividend per share)
signal, falling back to EPS if no dividend signal exists.

**Thresholds** (in `stage2_financial.py`, tunable — calibrate against
`classifier_v2/validation/`):

| `|surprise|` | magnitude | direction |
|---|---|---|
| ≥ 10% | large | positive / negative |
| ≥ 3%  | medium | positive / negative |
| < 3%  | small | neutral |

---

## What lands in the database

### `jquants_statements` (raw, one row per disclosure)

Full fidelity — every `/fins/summary` field is preserved in `raw_json`; the
columns below are the typed subset used by Stage 2. Idempotent via a unique
index on `disclosure_no`.

| column | J-Quants V2 field | meaning |
|---|---|---|
| `disclosure_no` | `DiscNo` | unique disclosure id |
| `local_code` / `ticker` | `Code` | 5-digit code / 4-digit (`[:4]`) join key |
| `disclosed_date` / `disclosed_time` | `DiscDate` / `DiscTime` | when disclosed |
| `period_type` | `CurPerType` | `1Q` / `2Q` / `3Q` / `FY` |
| `current_fy_end` | `CurFYEn` | fiscal-year-end (same-FY join key) |
| `net_sales`,`operating_profit`,`ordinary_profit`,`profit`,`eps` | `Sales`,`OP`,`OdP`,`NP`,`EPS` | actuals |
| `forecast_*`,`forecast_eps` | `FSales`,`FOP`,`FOdP`,`FNP`,`FEPS` | current-FY forecast |
| `result_dps_annual` / `forecast_dps_annual` | `DivAnn` / `FDivAnn` | annual dividend per share |

### `events.data_*` (derived signal, joined back per event)

| column | meaning |
|---|---|
| `data_direction` | positive / negative / neutral (beat / miss / in-line) |
| `data_magnitude` | large / medium / small |
| `data_surprise_pct` | `(actual − forecast) / |forecast|` |
| `data_basis` | `actual_vs_forecast` or `forecast_revision` |
| `data_metric` | `eps` or `dps` |
| `data_actual` / `data_forecast` | the values compared |
| `data_statement_id` | FK → `jquants_statements.id` |
| `data_enriched_at` | set when Stage 2 processed the event |

Events are linked to statements by `(ticker, event_date == disclosed_date)`.
Events with no matching statement (e.g. non-financial disclosures, or the
company's earliest report with no prior forecast) keep `data_* = NULL`; running
Stage 2 again after fetching more statements will pick up any newly-matchable
ones.

---

## Coverage & limitations

- `/fins/summary` covers **financial-results disclosures only**. The ~493K
  non-earnings events (M&A, buybacks, leadership, large-holding, …) are not
  enrichable here — that's by design; Stage 2 augments, it doesn't replace TDnet.
- **Standard plan ≈ 10 years** of history. Events near the start of 2016 may sit
  at the edge of that window.
- True beat/miss is computed at **fiscal-year end**; quarterly disclosures yield
  the guidance-revision signal. Half-year (2Q) forecast comparison is a natural
  future refinement (the `*2Q` forecast fields are already captured in
  `raw_json`).
- **Not in scope (yet):** replacing the yfinance price feed with
  `/prices/daily_quotes`, and populating the `tickers` table from `/listed/info`.

---

## Tests

```bash
pytest classifier_v2/test_stage2_financial.py -v
```

Covers the surprise math, threshold boundaries, same-fiscal-year forecast
lookup, and end-to-end enrichment (EPS vs. DPS preference, resume gating, reset).
