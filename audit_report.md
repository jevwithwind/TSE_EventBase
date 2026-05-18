# TSE_EventBase Audit Report

**Date:** 2026-05-12
**Rows in events.csv:** 747,234
**Rows in prices.csv:** 8,454,305 (4,429 tickers, 2015-12-01 to 2025-04-28)

---

## events.csv Content Audit

### Column Populated Rates

| Column | Populated | Missing | % Missing |
|---|---|---|---|
| id | 747,234 | 0 | 0% |
| ticker | 747,234 | 0 | 0% |
| company_name | 747,234 | 0 | 0% |
| company_name_en | 0 | 747,234 | **100%** |
| event_date | 747,234 | 0 | 0% |
| event_time | 0 | 747,234 | **100%** |
| headline | 747,234 | 0 | 0% |
| headline_en | 0 | 747,234 | **100%** |
| summary | 0 | 747,234 | **100%** |
| event_type | 747,234 | 0 | 0% |
| event_subtype | 0 | 747,234 | **100%** |
| direction | 0 | 747,234 | **100%** |
| magnitude | 0 | 747,234 | **100%** |
| source | 747,234 | 0 | 0% |
| source_url | 0 | 747,234 | **100%** |
| source_doc_id | 747,234 | 0 | 0% |
| raw_json | 747,234 | 0 | 0% |
| classified_at | 0 | 747,234 | **100%** |
| created_at | 747,234 | 0 | 0% |

**Sentiment-like columns 100% empty:** direction, magnitude, headline_en, summary, event_subtype -- all of which are intended to be populated by the AI classifier (`run_ai_classify.py`).

**event_time is 100% empty in the export** despite the TDnet scraper parsing it from `pubdate` in `raw_json` and inserting it into the DB. This suggests either the DB column is actually NULL (scraper may have been run with a version that didn't populate it) or the export/pandas round-trip dropped it. Raw JSON confirms `pubdate` contains time (e.g., `"2016-01-04 15:30:00"`).

### event_type Distribution (keyword-based via `run_categorize.py`)

| event_type | Count |
|---|---|
| other | 367,724 |
| earnings | 173,198 |
| buyback | 62,703 |
| forecast_revision | 52,296 |
| dividend | 28,849 |
| ma | 24,436 |
| borrowing | 6,255 |
| capital_change | 5,898 |
| tender_offer | 4,069 |
| large_holding | 3,256 |
| stock_split | 1,118 |
| others (<1k) | ~17,432 |

### Source Distribution

| source | Count |
|---|---|
| tdnet | 747,234 |
| edinet | 0 |

All events are from TDnet. EDINET scraper exists but was never run (requires `EDINET_API_KEY` which is not set).

---

## Upstream Extraction

### Data Sources & What Gets Pulled

| Pipeline | Source | What It Pulls | Status |
|---|---|---|---|
| TDnet scraper | Yanoshin Web API (no key) | ticker, company_name, event_date, event_time, headline, source_url, source_doc_id, raw_json | **Active** — 747K events scraped |
| EDINET scraper | EDINET API (key required) | Securities reports, large holdings, tender offers → events + financials tables | **Not run** — 0 events, 0 financials |
| Keyword categorizer | SQL on headline | event_type (12 categories + 'other') via LIKE matching | **Active** — all 747K events categorized |
| AI classifier | OpenAI-compatible API | direction, magnitude, headline_en, summary, event_subtype, classified_at | **Not run** — all target columns empty |
| Price fetcher | OpenBB / yfinance | OHLCV data for categorized tickers | **Active** — 8.45M rows, 4,429 tickers |
| Preview flag | Price volume/return analysis | previewed=True/False for earnings events | **Active** — 136,985 earnings flagged |
| Analysis scripts | Merged parquet | Event study plots, cumulative returns, clean vs previewed comparison | **Active** |

### Extractors Whose Output Is Missing from events.csv

1. **AI Classifier** (`run_ai_classify.py` + `classifier/event_classifier.py`): Fully implemented, uses OpenAI-compatible API to classify events with direction (positive/negative/neutral), magnitude (large/medium/small), English headline, and English summary. All target columns in events.csv are 100% empty. Classification is done in batches of 50. The system tracks `classified_at` timestamps. Requires `OPENAI_API_KEY` in `.env`.

2. **EDINET Scraper** (`run_edinet.py` + `scrapers/edinet_scraper.py`): Fetches securities reports from EDINET, with a `financials` table schema that captures net_sales, operating_income, ordinary_income, net_income, total_assets, total_equity, eps, bps, roe. The `financials.csv` export has 0 rows. The `edinet_scraper.py:_parse_financial_data()` method admits it's a stub: *"For now, return minimal financial data — in a real implementation, you would extract XBRL data from the filing"* (line 315).

3. **event_time**: Populated in `event_filter_list.csv` (305K events) but empty in `events.csv`. The TDnet scraper correctly extracts time from `pubdate`; the discrepancy may be an export artifact.

4. **source_url**: The TDnet scraper stores `pdf_url` (raw PDF document link from `document_url` field). Empty in exported CSV.

---

## XBRL Availability

### Raw XBRL Data

**No XBRL or XML files exist in the repository** (`.xbrl`, `.xml` extensions return zero results).

**No XBRL parser exists** in any Python source file. The only reference to XBRL is in `edinet_scraper.py:275` where `xbrlUrl` is stored as `source_url` if available from EDINET API responses.

### XBRL URLs in TDnet raw_json

The TDnet API response includes `url_xbrl` fields pointing to XBRL data in ZIP format. For earnings events, these URLs are populated:

```
url_xbrl: https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/081220160104481179.zip
```

The TDnet API also provides structured financial report URLs (`url_report_type_summary`, `url_report_type_fs_consolidated`, `url_report_type_fs_non_consolidated`, `url_report_type_earnings_forecast`, `url_report_type_expected_dividends`), but these are `null` in the sampled events (possibly only populated for newer events, or the Yanoshin API doesn't backfill them for historical data).

**Key finding:** XBRL zip files exist at known URLs within `raw_json` for earnings events. There is no parser to download and extract structured financial data from them, but the infrastructure to locate them is in place.

---

## Quick-win Proxy: Overnight Return Surprise

### Question: Can we compute overnight return surprise from existing prices.csv?

**Yes.** The data structure fully supports it.

- **Coverage:** 87,381 out of 173,198 earnings events (50.5%) have valid close on event date + open on next trading day after joining with `prices.csv`. The remaining 49.5% miss due to: no price data for the ticker, no trading on the event date, or no trading on the next day.
- **Computation:** `overnight_return = open_d1 / close_d0 - 1` (or log return variant).
- **Distribution** (n=87,381):
  - Mean: +1.12%
  - Median: 0.00%
  - Std: 116.2% (outliers present, likely stock splits)
  - 1st percentile: -15.37%
  - 5th percentile: -8.55%
  - 95th percentile: +8.27%
  - 99th percentile: +17.65%

### Existing Session-Type Infrastructure

The `event_filter_list.csv` (305,101 events) already contains `session_type` (after_hours: 248,716, intraday: 56,385) and `reaction_anchor_dt` (datetime for next trading session open). These were computed externally (no source code in the repo generates them), suggesting this proxy approach was already prototyped.

### Caveats

- Overnight return captures the market's aggregate reaction to the disclosure, not the fundamental "surprise" (EPS vs forecast). It is a sign-magnitude proxy.
- The `run_preview_flag.py` script already uses a variant of this approach to detect "previewed" (leaked) earnings events via abnormal volume and pre-event price drift.
- Outlier filtering is needed (distribution has extreme values above 100× returns, likely stock splits or delisted tickers).

---

## Recommendation

**`events.csv` lacks sentiment and no XBRL parser exists — propose adding overnight return proxy + headline NLP**

### Immediate Actions (No External Data Acquisition Required)

1. **Run the existing AI classifier** (`run_ai_classify.py`): Populates direction, magnitude, headline_en, summary, event_subtype for all 747K events. Requires an OpenAI-compatible API key. This fills all 100%-empty sentiment columns.

2. **Compute overnight return surprise** from `prices.csv` + `events.csv`: A quantitative sign-magnitude signal (`open_d1 / close_d0 - 1`) already achievable with 50% coverage for earnings events. Can be extended to all event types. Requires only the existing data.

3. **Extract event_time from raw_json**: The `pubdate` field in `raw_json` contains timestamps. These can be backfilled into the `event_time` column to enable session-type classification (after-hours vs intraday).

### Medium-term (Requires Development)

4. **Download and parse TDnet XBRL ZIP files**: The `url_xbrl` field in `raw_json` points to structured financial data. Building a parser would provide actual EPS, net income, forecast-vs-actual values, enabling fundamental surprise measurement (not just market reaction). The TDnet XBRL format is standardized (JGAAP XBRL taxonomy).

5. **Run EDINET scraper**: Populates the `financials` table with structured financial data (requires `EDINET_API_KEY`). The existing scraper code needs the `_parse_financial_data()` method completed to extract actual numbers from EDINET filings.

### What NOT to Do

- Do not pursue external data acquisition — enough data exists in the repo to substantially improve signal quality.
- Do not discard the AI classifier — it is fully implemented, just never run. The 100% empty sentiment columns can be filled with one command.

### Summary Table

| Signal | Status | How to Unlock |
|---|---|---|
| event_type | Populated (keyword) | Already working |
| direction | Empty | Run `run_ai_classify.py` |
| magnitude | Empty | Run `run_ai_classify.py` |
| headline_en / summary | Empty | Run `run_ai_classify.py` |
| event_time | Empty (in CSV) | Extract from `raw_json.pubdate` |
| Overnight return proxy | Computable | Join events with prices; 50% coverage |
| XBRL financials (EPS, etc.) | URLs exist, no parser | Download ZIPs from `url_xbrl`, parse XBRL |
| EDINET financials | Schema exists, 0 rows | Set `EDINET_API_KEY`, run scraper, complete parser |
