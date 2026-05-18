"""
Sample 50 events stratified across event_type for hand-coding.
Output: validation/gold_standard_blank.csv

Author will hand-code direction/magnitude/event_type to create ground-truth.
N=50 is small but defensible for Cohen's kappa estimation on a preliminary
validation set (Cohen 1960; Landis & Koch 1977 thresholds for kappa
interpretation: 0.61-0.80 substantial, >0.80 almost perfect).

Excludes 'other' event_type since the project scope skips classifying those.
"""
import sqlite3
import csv
import random
from pathlib import Path

DB = r"E:\TSE_EventBase\data\tse_eventbase.db"
OUT = Path(__file__).parent / "gold_standard_blank.csv"

# Stratified sample: weighted toward sentiment-relevant types we'll actually classify
STRATA = {
    "earnings": 15,
    "forecast_revision": 10,
    "buyback": 8,
    "dividend": 7,
    "ma": 5,
    "tender_offer": 5,
}

random.seed(42)  # reproducibility -- cite in thesis methodology

conn = sqlite3.connect(DB)
all_samples = []
for et, n in STRATA.items():
    rows = conn.execute(
        "SELECT id, ticker, company_name, event_date, headline, event_type "
        "FROM events WHERE event_type=? AND classified_at IS NULL "
        "  AND classification_failed_at IS NULL "
        "ORDER BY RANDOM() LIMIT ?",
        (et, n),
    ).fetchall()
    all_samples.extend(rows)
    print(f"{et}: sampled {len(rows)}")

random.shuffle(all_samples)

with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "ticker", "company_name", "event_date", "headline",
                "keyword_event_type",
                "hand_event_type", "hand_direction", "hand_magnitude",
                "hand_confidence", "notes"])
    for r in all_samples:
        w.writerow(list(r) + ["", "", "", "", ""])

print(f"\nWrote {len(all_samples)} rows to {OUT}")
print("\nFill in the hand_* columns. Allowed values:")
print("  hand_event_type: earnings, forecast_revision, dividend, buyback, ma,")
print("                   tender_offer, leadership_change, stock_split,")
print("                   large_holding, capital_raise, delisting, other")
print("  hand_direction:  positive, negative, neutral")
print("  hand_magnitude:  large, medium, small")
print("  hand_confidence: high, medium, low")
print("\nLeave hand_* blank ONLY for events you cannot judge -- these become")
print("'undetermined' in the metrics, separating ambiguous events from coder bias.")
