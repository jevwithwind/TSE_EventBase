# Three-Stage Sentiment Classification Pipeline

## Architecture

```
events table (747K rows, 327K in-scope)
           │
           ▼
┌─────────────────────────────────────────────────────┐
│ Stage 0: Overnight Return Classification             │
│   Script:  stage0_prices.py                          │
│   Source:  prices table (8.4M rows, 2015–2025)       │
│   Method:  (next_day_close − event_day_close)        │
│            ÷ event_day_close                         │
│   Runtime: ~30 seconds (no API)                      │
│   Output:  ai_direction, ai_magnitude, ai_confidence │
│   Coverage: ~78% of in-scope events                  │
│                                                      │
│   Thresholds:                                        │
│     > +2.0% → positive / large                       │
│     > +0.5% → positive / medium                      │
│     ±0.5%   → neutral / small                        │
│     < −0.5%  → negative / medium                     │
│     < −2.0%  → negative / large                      │
└─────────────────────────────────────────────────────┘
           │
           ▼  remaining unclassified events
┌─────────────────────────────────────────────────────┐
│ Pre-Filter: Auto-Classify Neutral Earnings           │
│   Script:  classifier.py --pre-filter                │
│   Method:  SQL pattern match on headline text        │
│   Runtime: ~5 seconds (no API)                       │
│   Output:  ai_event_type, ai_direction, ai_confidence│
│   Coverage: ~161K of remaining events                │
│                                                      │
│   Matches: 決算短信, 四半期, 中間期 headlines       │
│            WITHOUT directional keywords              │
│            (上方修正, 下方修正, 増配, 減配, etc.)     │
│   Sets:    direction=neutral, confidence=low         │
│            magnitude=medium, event_type=earnings     │
│                                                      │
│   Rationale: these headlines unambiguously convey    │
│   zero directional signal — validated via gold-      │
│   standard hand-coding (76% neutral split). AI       │
│   processing would be wasteful.                      │
└─────────────────────────────────────────────────────┘
           │
           ▼  remaining unclassified events (~17K)
┌─────────────────────────────────────────────────────┐
│ Stage 1: LLM Headline Classification                 │
│   Script:  classifier.py                             │
│   Model:   DeepSeek V3.2 (configurable via MODEL)    │
│   Method:  Few-shot prompt with enum validation       │
│   Runtime: ~3.5 hours (batch_size=20, concurrency=4) │
│   Output:  ai_event_type, ai_event_subtype,          │
│            ai_direction, ai_magnitude, ai_confidence,│
│            ai_headline_en, ai_summary                │
│                                                      │
│   Features:                                          │
│   - Concurrent workers (ThreadPoolExecutor)          │
│   - Exponential retry with backoff (3 attempts)      │
│   - Enum validation on all fields                    │
│   - Resume-safe (skips classified_at IS NOT NULL)    │
│   - Status heartbeat via classification_status.json  │
│   - Strict temperature=0.0 for reproducibility       │
└─────────────────────────────────────────────────────┘
           │
           ▼  all events classified
┌─────────────────────────────────────────────────────┐
│ Stage 2 (Implemented): J-Quants Fundamental Data     │
│   Script:  stage2_financial.py                       │
│   Source:  jquants_statements (/fins/summary)        │
│   Method:  actual results vs. prior forecast         │
│   Output:  events.data_* (direction, magnitude,      │
│            surprise_pct, basis, actual, forecast)    │
│                                                      │
│   Signal (beat vs. company's own forecast):          │
│   - FY: actual EPS vs. prior FY forecast EPS         │
│   - Quarterly: forecast revision vs. prior           │
│   - Dividend events use DPS (annual)                 │
│                                                      │
│   Objective ground-truth for Stage 0 / 1             │
│   validation. Thresholds tunable; details            │
│   in jquants/README.md.                              │
└─────────────────────────────────────────────────────┘
```

## Launch

```powershell
cd E:\TSE_EventBase\classifier_v2
.\run_classifier_full.ps1
```

Stages 0 and pre-filter run synchronously in the foreground (seconds). Stage 1 launches as a background process — you can close the PowerShell window.

## Monitor

```powershell
# Progress snapshot
type classification_status.json

# Live log tail
Get-Content logs\classifier_run_*.log -Tail 20 -Wait

# Check if still running
Get-Process python -ErrorAction SilentlyContinue
```

## Data Flow

| Column | Stage 0 | Pre-Filter | Stage 1 | Description |
|---|---|---|---|---|
| `ai_event_type` | keyword | `earnings` | LLM | Event category |
| `ai_event_subtype` | `price_implied` | `quarterly_or_annual` | LLM | Subcategory |
| `ai_direction` | from return | `neutral` | LLM | positive/negative/neutral |
| `ai_magnitude` | from return | `medium` | LLM | large/medium/small |
| `ai_confidence` | from return | `low` | LLM | high/medium/low |
| `ai_headline_en` | `(price-based)` | translation | LLM | English headline |
| `ai_summary` | return calc | auto message | LLM | English summary |

## Validation Framework

See `classifier_v2/validation/` — gold-standard hand-coding against LLM.
All results use `classified_at` for pipeline stage gating — each stage
only processes events where `classified_at IS NULL`.

## References

- Cohen, J. (1960). A coefficient of agreement for nominal scales.
- Landis, J. R., & Koch, G. G. (1977). The measurement of observer agreement.
- Gilardi, F. et al. (2023). ChatGPT outperforms crowd workers.
- Ziems, C. et al. (2024). Can LLMs Transform Computational Social Science?
- Zheng, L. et al. (2023). Judging LLM-as-a-Judge with MT-Bench.
