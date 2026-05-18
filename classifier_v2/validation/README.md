# Validation Framework

## Purpose

Thesis-defense-quality validation framework for the TSE_EventBase AI classifier,
following the LLM-as-annotator methodology from:

- **Gilardi et al. (2023)** — ChatGPT outperforms crowd workers for text-annotation tasks
- **Ziems et al. (2024)** — Can LLMs transform computational social science?
- **Zheng et al. (2023)** — LLM-as-a-Judge with MT-Bench

## Files

| File | Role |
|---|---|
| `sample_gold_standard.py` | Stratified random sample of 50 events for hand-coding |
| `gold_standard_blank.csv` | Generated: 50 events to hand-code (id, ticker, headline, etc.) |
| `gold_standard_filled.csv` | After hand-coding: same schema + `hand_event_type`, `hand_direction`, `hand_magnitude`, `hand_confidence` |
| `migrate_judge_columns.py` | Adds `judge_*` columns to the events table |
| `llm_judge.py` | Runs a second LLM on already-classified events (cross-validation) |
| `compute_metrics.py` | Computes kappa, accuracy, confusion matrix, coverage stats |
| `metrics_report.md` | Generated: paste-ready thesis appendix |
| `confusion_matrix_event_type.png` | Generated: heatmap for event_type |
| `error_analysis.csv` | Generated: top 10 worst disagreements with headlines |

## Workflow

### Step 1: Sample gold standard

```powershell
python sample_gold_standard.py
```

Produces `gold_standard_blank.csv` with 50 events.

### Step 2: Hand-code the 50 events

Open `gold_standard_blank.csv`, fill in the `hand_*` columns:

| Column | Allowed values |
|---|---|
| `hand_event_type` | earnings, forecast_revision, dividend, buyback, ma, tender_offer, leadership_change, stock_split, large_holding, capital_raise, delisting, other |
| `hand_direction` | positive, negative, neutral |
| `hand_magnitude` | large, medium, small |
| `hand_confidence` | high, medium, low |

Leave `hand_*` blank ONLY for events you cannot judge — these become "undetermined"
in the metrics, separating ambiguous events from coder bias.

Save as `gold_standard_filled.csv`.

### Step 3: Run primary classifier

Already implemented in `classifier_v2/classifier.py`. This is done separately and
should classify the events (including the 50 gold-standard events).

### Step 4: Add judge columns

```powershell
python migrate_judge_columns.py --db ../data/tse_eventbase.db
```

### Step 5: Run LLM-as-judge cross-validation

```powershell
$env:JUDGE_MODEL = "qwen3.6-plus"
python llm_judge.py --db ../data/tse_eventbase.db --limit 2000
```

### Step 6: Compute metrics

```powershell
python compute_metrics.py --db ../data/tse_eventbase.db
```

Produces `metrics_report.md`, `confusion_matrix_event_type.png`, and `error_analysis.csv`.

## Interpreting the Metrics

### Cohen's Kappa thresholds (Landis & Koch 1977)

| Kappa | Interpretation |
|---|---|
| > 0.80 | Almost perfect |
| 0.61–0.80 | Substantial |
| 0.41–0.60 | Moderate |
| 0.21–0.40 | Fair |
| 0.00–0.20 | Slight |

### What to look for

1. **event_type**: Target κ > 0.61 (substantial). This is the primary classification
   task and should be reliable.

2. **direction**: Target κ > 0.40 (moderate). Lower bar is acceptable due to Tier-3
   ambiguity — Japanese headlines like "Summary of Financial Results" carry no
   directional signal, and even human coders disagree. The classifier's confidence
   flag captures this.

3. **magnitude**: Expect moderate to fair κ. Magnitude judgments from headlines
   alone are inherently noisy. The two-stage strategy (Stage 2 overnight-return
   augmentation) addresses this limitation.

4. **Confidence distribution**: A high fraction of `confidence=low` on earnings
   events is EXPECTED, not a problem. These are exactly the events that should
   undergo Stage 2 overnight-return augmentation.

### Thesis Defense Talking Points

- **Methodological rigor**: 50 hand-coded events, stratified across 6 event types,
  with reproducible random seed (42). Metrics follow Cohen (1960) and Landis & Koch
  (1977) for kappa interpretation.

- **Beyond accuracy**: We report per-class precision/recall/F1 and confusion
  matrices — not just aggregate accuracy — to identify which event types the
  classifier handles well vs. poorly.

- **LLM-as-judge cross-validation**: A second LLM independently classifies the same
  events, providing an inter-model reliability metric (Zheng et al. 2023). This is
  distinct from the gold-standard comparison and provides a complementary measure
  of classification stability.

- **Ambiguity is a feature, not a bug**: The `confidence=low` flag explicitly
  models headline inferability. Events flagged low-confidence are routed to Stage 2
  overnight-return augmentation — this is a designed two-stage architecture, not a
  classifier failure.

- **Defensible N**: n=50 is small but within published norms for preliminary
  validation sets, particularly when each field is evaluated separately and
  bootstrap CIs are reported. A larger human-coded set is recommended for
  publication follow-up.
