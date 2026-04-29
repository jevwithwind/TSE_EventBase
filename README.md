# TSE_EventBase — Tokyo Stock Exchange corporate event database

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/jevwithwind/TSE_EventBase/pulls)

## What this does

TSE_EventBase collects corporate disclosure events from two sources — **TDnet** (timely disclosures via Yanoshin Web API) and **EDINET** (formal regulatory filings via FSA API) — stores them in a shared SQLite database, optionally classifies them with an AI model, and fetches historical stock prices via OpenBB. Designed for academic event-study research.

## Data sources

| Source | What it covers | API key required |
|---|---|---|
| **TDnet** (Yanoshin API) | All timely disclosures (~350/day): earnings, guidance revisions, dividends, M&A, buybacks, leadership changes, etc. | No |
| **EDINET** (FSA) | Formal regulatory filings: full securities reports, large-holding notifications (5%+), tender offers, treasury buyback plans | Yes — register at [edinet-fsa.go.jp](https://disclosure.edinet-fsa.go.jp/) |

Both sources write into the same SQLite database, differentiated by the `source` column (`"tdnet"` or `"edinet"`). This allows straightforward joins with the shared `prices` and `tickers` tables.

## Quick Start

```bash
git clone https://github.com/jevwithwind/TSE_EventBase.git
cd TSE_EventBase
pip install -r requirements.txt
cp .env.example .env       # edit .env with your keys
python db/init_db.py       # create the database
python run_tdnet.py --start-date 2025-01-01 --end-date 2025-01-31
```

## Core pipeline

```
db/init_db.py              →  create/migrate database
run_tdnet.py               →  scrape TDnet (no key needed)
run_edinet.py              →  scrape EDINET (needs EDINET_API_KEY)
run_scrape.py              →  scrape both in one command
run_categorize.py          →  keyword-based event categorization (no API needed)
run_prices.py              →  fetch historical stock prices via OpenBB
run_export.py              →  export tables to CSV / Parquet
run_ai_classify.py         →  [optional] AI-powered classification
generate_filter_list.py    →  export filtered event list for downstream pipelines
```

## Usage

### Initialize database

```bash
python db/init_db.py

# Reset (drops and recreates all tables):
python db/init_db.py --reset
```

### Scrape TDnet (Yanoshin API — no key needed)

```bash
python run_tdnet.py --start-date 2016-01-01 --end-date 2025-12-31
```

### Scrape EDINET (requires EDINET_API_KEY)

```bash
python run_edinet.py --start-date 2016-01-01 --end-date 2025-12-31
```

### Scrape both sources at once

```bash
# Both (EDINET is skipped gracefully if EDINET_API_KEY is not set)
python run_scrape.py --start-date 2016-01-01 --end-date 2025-12-31

# Explicit source selection
python run_scrape.py --start-date 2016-01-01 --end-date 2025-12-31 --source tdnet
python run_scrape.py --start-date 2016-01-01 --end-date 2025-12-31 --source edinet
```

The scraper is safe to interrupt and resume — `INSERT OR IGNORE` on a unique `source_doc_id` index prevents duplicates on re-run.

### Categorize events (keyword-based, no API needed)

```bash
python run_categorize.py
```

### Fetch historical prices

Pad the window by one month each side to support event study windows:

```bash
python run_prices.py --start-date 2015-12-01 --end-date 2025-12-31
```

### Export data

```bash
# Export all tables to CSV and Parquet
python run_export.py

# Specific tables or format
python run_export.py --tables events prices
python run_export.py --format csv
```

### Generate event filter list

Exports a CSV of the six core event types for use in downstream pipelines (e.g. tick data stream-and-discard):

```bash
python generate_filter_list.py
# → data/exports/event_filter_list.csv
```

Output columns: `ticker`, `event_date`, `event_time`, `event_type`, `headline`.
Covers `earnings`, `forecast_revision`, `dividend`, `buyback`, `ma`, `tender_offer`.

### Database health checks

```bash
sqlite3 data/tse_eventbase.db "SELECT source, COUNT(*) FROM events GROUP BY source;"
sqlite3 data/tse_eventbase.db "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC;"
sqlite3 data/tse_eventbase.db "SELECT MIN(event_date), MAX(event_date) FROM events;"
sqlite3 data/tse_eventbase.db "SELECT COUNT(DISTINCT ticker) FROM events;"

# Confirm no duplicates
sqlite3 data/tse_eventbase.db \
  "SELECT source_doc_id, COUNT(*) FROM events GROUP BY source_doc_id HAVING COUNT(*) > 1 LIMIT 5;"
```

## Optional: AI-powered classification

`run_ai_classify.py` enriches events with English translations, sentiment direction (positive / negative / neutral), and magnitude scoring (large / medium / small). It uses any **OpenAI-compatible API** — OpenAI, Azure OpenAI, local Ollama, Qwen, etc.

Set `OPENAI_API_KEY`, `OPENAI_BASE_URL` (optional), and `MODEL` in `.env`, then:

```bash
# Classify all unclassified events
python run_ai_classify.py

# Custom batch size
python run_ai_classify.py --batch-size 25

# Only classify events matching key financial keywords
python run_ai_classify.py --filter

# Exclude ETF daily disclosures
python run_ai_classify.py --exclude

# Dry run: count matching events without classifying
python run_ai_classify.py --dry-run
```

AI classification is fully optional. The keyword-based categorizer (`run_categorize.py`) works without any API key and covers most research needs.

## Environment variables

Copy `.env.example` to `.env` and fill in the values you need:

| Variable | Required for | Default |
|---|---|---|
| `EDINET_API_KEY` | `run_edinet.py` / EDINET scraping | — |
| `OPENAI_API_KEY` | `run_ai_classify.py` | — |
| `OPENAI_BASE_URL` | Custom OpenAI-compatible endpoint | OpenAI default |
| `MODEL` | AI classification model | `gpt-4o` |
| `SCRAPE_START_DATE` | Default date range | 5 years ago |
| `SCRAPE_END_DATE` | Default date range | today |
| `DB_PATH` | Database location | `data/tse_eventbase.db` |

## Database schema

All tables live in a single SQLite file (default: `data/tse_eventbase.db`).

### `events`

| Column | Description |
|---|---|
| `ticker` | TSE 4-digit code (e.g. `7203`) |
| `company_name` | Japanese company name |
| `event_date` | Disclosure date |
| `event_time` | Disclosure time (TDnet only) |
| `headline` | Original Japanese headline |
| `headline_en` | English translation (AI) |
| `summary` | English summary (AI) |
| `event_type` | See event type taxonomy below |
| `direction` | positive / negative / neutral (AI) |
| `magnitude` | large / medium / small (AI) |
| `source` | `tdnet` or `edinet` |
| `source_url` | Original document URL |
| `source_doc_id` | Original document ID (unique index) |
| `raw_json` | Full API response |

**Event type taxonomy** (assigned by `run_categorize.py`):

| event_type | Keyword signal |
|---|---|
| `earnings` | 決算短信 |
| `forecast_revision` | 業績予想 |
| `dividend` | 配当 |
| `buyback` | 自己株式 |
| `ma` | 合併 / 買収 |
| `tender_offer` | 公開買付 / TOB / MBO |
| `ceo_change` | 代表取締役 |
| `executive_change` | 役員の異動 / 役員人事 |
| `stock_split` | 株式分割 |
| `large_holding` | 大量保有 / 主要株主の異動 |
| `third_party_allotment` | 第三者割当 |
| `borrowing` | 資金の借入 |
| `capital_change` | 資本 |
| `other` | Everything else |

### `prices`

Daily OHLCV data keyed by `(ticker, date)`.

### `financials`

Financial metrics extracted from EDINET securities reports: net sales, operating income, net income, total assets, EPS, BPS, ROE, etc.

### `tickers`

Company metadata: sector, market segment (Prime / Standard / Growth), listing/delisting dates.

## Performance notes

The TDnet scraper is optimized for bulk historical loads:

- **Single connection per run** — one `sqlite3.connect()` for the entire date range, not per row
- **WAL mode** — `PRAGMA journal_mode = WAL` allows reads during writes
- **Batch insert** — all rows for a trading day are accumulated and written in a single `executemany()` + `commit()`
- **`INSERT OR IGNORE`** — duplicate detection is handled by a unique index on `source_doc_id`, eliminating the per-row `SELECT COUNT(*)` check
- **Cache** — `PRAGMA cache_size = -64000` (64 MB) reduces I/O on large re-scrapes

On a typical machine, a full 2016–2025 re-scrape (~750K events) completes in under 90 minutes, bottlenecked entirely by the Yanoshin API's response time and the 1-second courtesy delay between days.

## API notes

### Yanoshin TDnet API

- URL: `https://webapi.yanoshin.jp/webapi/tdnet/list/YYYYMMDD.json?limit=10000`
- No authentication required
- ~350 events per trading day; ~85K per year as of 2025
- Response: `{"total_count": N, "items": [{"Tdnet": {...}}]}`

### EDINET API

- Wrapped via `edinet-tools` library
- Free tier: 100 calls/day
- Covers: securities reports, large-holding reports (5%+), tender offers, treasury buyback plans

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes
4. Open a Pull Request

## Citation

```bibtex
@software{TSE_EventBase,
  author = {Li, Kazumi},
  title  = {TSE_EventBase: Tokyo Stock Exchange corporate event database},
  year   = {2025-2026},
  url    = {https://github.com/jevwithwind/TSE_EventBase}
}
```

## License

MIT
