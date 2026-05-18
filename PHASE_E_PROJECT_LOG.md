# Phase E Project Log

**Last updated**: 2026-05-12
**Status**: AI classifier pipeline complete and tested; full classification run pending overnight.

## Purpose of this log

Decisions, dead ends, and findings from Phase E (ML pipeline construction).
Future-me (or thesis defense committee) will need to know *why* the current
state of the code is what it is. Code shows the *what*; this log captures
the *why*.

---

## Timeline summary

| Phase | Output | Status |
|---|---|---|
| E.1 Target construction | `targets.py`, `targets.parquet` (305K rows) | ✅ Complete |
| E.2a Feature engineering (intraday) | `features.py` v2, polars-native | ✅ Code complete, smoke-pending |
| E.2b Controls | `controls.py`, `controls.parquet` (305K rows, 87% OK) | ✅ Complete |
| E.2c Pre-event window data (after-hours) | `build_pre_event_store.py` | ⏸️ **Deferred** — see decision D5 |
| E.2d Sentiment augmentation | `classifier.py`, validated κ=0.95/0.73 | ✅ Code complete, full-run pending |
| E.3 Sample construction | — | ⏳ Next after classifier completes |
| E.4 Modeling | — | ⏳ |

---

## Key architectural decisions

### D1: Target T1 reversal flag is a continuum, not a category

**Decision**: Add `overshoot_ratio` (continuous, max|r_t| / |r_final|)
as a diagnostic field. Do NOT filter events by T1_flag at modeling time.

**Why**: The first full-run diagnostic showed 51.5% of events flagged as
"reversal". Investigation revealed the reversal flag fires both on (a) real
overshoot-and-revert behavior and (b) microstructure jitter on high-activity
stocks (median n_ticks: reversal=304 vs ok=23). Of reversal events, 83.2%
have overshoot > 1.10 — real overshoots — but the flag conflates two
phenomena and shouldn't be used as a categorical exclusion.

**Implication**: Downstream models use `overshoot_ratio` as a control feature,
not as a filter. Documented in `targets.py` docstring.

### D2: T2 (completeness) median for earnings = 0.109

**Observation**: 11% of D+5 price move occurs in the first 120 minutes for
earnings events. Strong evidence of post-earnings announcement drift (PEAD)
on TSE — consistent with international literature (Bernard & Thomas 1989,
Sadka 2006). This is a thesis result, not a methodology issue.

### D3: Feature engineering went through three versions

**v1 (rejected)**: Called pandas-only `tse_tick.features`. Failed because the
installed `tse_tick` at `E:\NEEDS_tick` is polars-native.

**v2 (current)**: Polars-native. Calls `tse_tick.features` directly with
polars DataFrames. Faster, no pandas conversion.

**Lesson**: Always check the *installed* package, not the project folder
sharing a name. We had `E:\tse_tick\` (pandas, old) and `E:\NEEDS_tick\`
(polars, current installed version) coexisting.

### D4: Pre-event window asymmetry for intraday vs after-hours

**Pre-event window definitions**:
- Intraday: `[reaction_anchor - 30min, reaction_anchor]` — same trading day
- After-hours: `[prior_trading_day 14:25 JST, prior_trading_day 14:55 JST]`

**Why this is correct**:
- For after-hours events, `reaction_anchor` = next morning's 09:00 = opening
  auction. Using 08:30-09:00 as pre-event would be look-ahead bias *into* the
  opening auction price discovery. Going back to the prior session's last
  30 min of continuous trading is the right answer (avoids closing auction
  at 15:00 by ending at 14:55).
- The asymmetry is a methodological feature, not a bug, but it has data
  consequences — see D5.

### D5: Pre-event tick data is NOT in the existing parquet store for after-hours events

**Finding**: The upstream `tse-tick ingest` pipeline used `--window 120`
(±120 min around `reaction_anchor`). For after-hours events with anchor at
D+1 09:00, this covers D+1 07:00–11:00 only — NOT D's afternoon 14:25–14:55.

**Decision**: Deferred microstructure redownload (Path A). Pursued sentiment
augmentation instead (Path D).

**Why**:
- Path A: 10 days of active babysitting to redownload raw NEEDS zips by year
  (~50-200 GB transit, processed year-by-year with deletion).
- Path D: ~3 days of automated LLM classification, addressing a *more
  fundamental* limitation than missing pre-event microstructure.

**Justification for Path D over A**: TSE_EventBase's `event_type` is a
keyword-derived label only. There's no direction (positive/negative) or
magnitude signal. The existing data couldn't distinguish good vs bad
earnings reports — fatal limitation for PEAD-style modeling. After audit,
discovered TSE_EventBase had an unfinished AI classifier infrastructure
designed exactly for this purpose, never run.

**Defense answer if challenged**: "We prioritized sentiment classification
(critical for PEAD literature) over after-hours microstructure (incremental
improvement). Intraday events (23%) have full microstructure features;
after-hours events (77%) rely on daily controls + sentiment."

### D6: Audit-driven rewrite of TSE_EventBase classifier

The original `classifier/event_classifier.py` was audited and rated
**REWRITE** due to:

1. **Infinite error loop** on persistent API failures
2. **Broken resume logic** (checked `event_type IS NULL` but keyword
   classifier had already populated all `event_type` values)
3. **Invalid model name** in the configured default
4. **No few-shot examples**, no enum validation, silent failure modes

The new classifier (`classifier_v2/classifier.py`) addresses each:
- Bounded retries: `RETRY_BACKOFF = [2, 8, 30]` then mark failed and move on
- Resume on `classified_at IS NULL AND classification_failed_at IS NULL`
- Model name read from env (`MODEL`), no hardcoded default
- 5 few-shot examples in system prompt, strict enum validation, explicit
  error tracking via `classification_failed_at` + `classification_error`
- 9 new columns added to events table via `migrate_db.py`:
  `ai_event_type, ai_event_subtype, ai_direction, ai_magnitude, ai_confidence,
   ai_headline_en, ai_summary, classification_failed_at, classification_error`

**Keyword `event_type` column is preserved** — AI writes to `ai_event_type`.
Allows cross-validation in methodology section.

### D7: LLM choice — DeepSeek v3.2 over Qwen3.6-plus

**Why**: Smoke test on 5 events showed Qwen3.6-plus emitted ~640 output
tokens per event due to reasoning chain-of-thought. DeepSeek v3.2 emitted
~90 output tokens per event. Both have OpenAI-compatible endpoints on
Alibaba Token Plan; switching was zero-cost.

**At scale (380K events)**: estimated savings of ~$500-$1,500 by switching.

**Backup plan**: If DeepSeek v3.2 runs out of credits, switch to DeepSeek
v4-flash (cheaper per token, more tokens per event, net positive).

### D8: Scope reduced to ~380K sentiment-relevant events

**Excluded**: 367K events with `event_type = 'other'` (mostly miscellaneous
disclosures). Not central to thesis. Can revisit if time permits.

**Included**: earnings, forecast_revision, dividend, buyback, ma, tender_offer.

### D9: Validation framework — 50 hand-coded events as gold standard

**Methodology**: Treat LLM as an automated coder per content-analysis
tradition (Krippendorff 2018, Gilardi et al. 2023). Hand-code 50 events as
gold standard, compute Cohen's κ.

**Reasoning**: Defense vulnerability if validation skipped — "another LLM
agreed" is weak. 1-2 hours of hand-coding produces a defensible κ number.

**Results** (2026-05-12):
| Field | Cohen's κ | 95% CI | Landis-Koch |
|---|---|---|---|
| event_type | 0.95 | [0.87, 1.00] | Almost perfect |
| direction | 0.73 | [0.47, 0.92] | Substantial |
| magnitude | 0.13 | [-0.09, 0.38] | Slight |

**Decision on magnitude**: Drop from downstream modeling. The error pattern
is a systematic calibration offset (LLM consistently one notch more
conservative than coder), and headline-only data has fundamental information
limits for magnitude. Defense answer: "magnitude assessment from headline
alone is inherently noisy; we use only event_type and direction as classified
features."

### D10: Pre-event windowing for intraday in features.py works with existing parquet

**Status**: features.py v2 is code-complete and tested but only smoke-tested
on intraday events using the existing event_windows parquet. After-hours
events return `feature_quality_flag = "insufficient_ticks"` for now.

**Downstream handling**: Intraday model gets full microstructure features.
After-hours model relies on controls + sentiment (D5, D9).

---

## Concurrency tuning observations (from 1000-event scale test)

| Config | Result |
|---|---|
| batch_size=50, concurrency=8 | 200/1000 timed out at 60s server limit |
| batch_size=20, concurrency=4 | 1000/1000 success, 1.35 ev/s, 0 failures |

**Bottleneck**: 60s server-side response timeout. Larger batches = longer
responses = more timeouts. Concurrency is not rate-limited but is response-
time-limited.

**Production config**: batch=20, concurrency=4.

---

## Open questions / future work

1. **Stage 2 sentiment augmentation**: For events with `ai_confidence = 'low'`
   (~70% of full set, estimated), augment with overnight return proxy
   computed from prices.csv (`open_d1 / close_d0 - 1`). Audit confirmed
   this is feasible. Not yet implemented.

2. **LLM-as-judge cross-validation**: Validation framework supports running
   a second model on the same 50 events or a larger sample. Not yet run.
   Would strengthen methodology section by ~one paragraph (Zheng et al.
   2023 framework).

3. **After-hours microstructure**: Path A redownload deferred. If time
   permits after main modeling complete, can revisit. `run_preevent_pipeline.ps1`
   and `build_pre_event_store.py` are ready to use.

4. **XBRL extraction for true SUE**: TSE_EventBase has XBRL URLs in
   `raw_json.url_xbrl` but no parser. Would give actual EPS/forecast-vs-actual
   numbers, enabling true standardized unexpected earnings. 2-5 day project.
   Probably unnecessary given the κ values achieved, but a strong extension.

5. **Coverage analysis bug in compute_metrics.py**: Reported confidence
   counts summed to 70 instead of 50. Likely counting all events with
   ai_confidence set, not filtering to the 50 in gold standard. Minor
   reporting issue, not a methodology issue. Fix before thesis writeup.

---

## File locations reference

```
E:\thesis_ml\
  src\
    targets.py              T1/T2 target construction
    features.py             order-book features (v2, polars-native)
    controls.py             daily controls + Nikkei preview Tier 1
    test_*.py               unit tests (27/20/20 passing)
  data\
    targets.parquet         305K events, T1/T2 + overshoot_ratio
    controls.parquet        305K events, daily controls
  scripts\
    build_pre_event_store.py  one-time pre-event parquet builder (unused)

E:\TSE_EventBase\
  data\
    tse_eventbase.db        SQLite, 747K events
    exports\                source CSVs (prices, calendar, filter list)
  classifier_v2\
    classifier.py           production classifier
    migrate_db.py           adds 9 ai_* columns to events table
    test_classifier.py      29 tests passing
    validation\
      sample_gold_standard.py   stratified sampler
      gold_standard_filled.csv  50 hand-coded events
      compute_metrics.py        Cohen's κ + classification report
      llm_judge.py              cross-validation against second model
      metrics_report.md         current validation results
      README.md                 workflow documentation

E:\NEEDS_tick\               installed tse_tick (polars-native)
E:\tse_tick\                 OLD pandas version, DO NOT USE
F:\JEV_SSD\event_windows\    parquet store, 10GB, ±120min reaction windows
D:\event_windows\            alternate path (same data)
```

---

## Citations for thesis methodology section

- Bernard, V. L., & Thomas, J. K. (1989). Post-earnings-announcement
  drift: Delayed price response or risk premium? *Journal of Accounting
  Research*, 27, 1-36.
- Cohen, J. (1960). A coefficient of agreement for nominal scales.
  *Educational and Psychological Measurement*, 20(1), 37-46.
- Gilardi, F., Alizadeh, M., & Kubli, M. (2023). ChatGPT outperforms
  crowd workers for text-annotation tasks. *PNAS*, 120(30).
- Krippendorff, K. (2018). *Content Analysis: An Introduction to Its
  Methodology* (4th ed.). SAGE Publications.
- Landis, J. R., & Koch, G. G. (1977). The measurement of observer
  agreement for categorical data. *Biometrics*, 33(1), 159-174.
- Lopez-Lira, A., & Tang, Y. (2023). Can ChatGPT forecast stock price
  movements? Return predictability and large language models. *SSRN*.
- Sadka, R. (2006). Momentum and post-earnings-announcement drift
  anomalies: The role of liquidity risk. *Journal of Financial
  Economics*, 80(2), 309-349.
- Zheng, L., et al. (2023). Judging LLM-as-a-judge with MT-Bench and
  Chatbot Arena. *NeurIPS*.
- Ziems, C., et al. (2024). Can large language models transform
  computational social science? *Computational Linguistics*, 50(1).
