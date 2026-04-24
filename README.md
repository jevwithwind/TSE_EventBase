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
db/init_db.py          →  create/migrate database
run_tdnet.py           →  scrape TDnet (no key needed)
run_edinet.py          →  scrape EDINET (needs EDINET_API_KEY)
run_scrape.py          →  scrape both in one command
run_categorize.py      →  keyword-based event categorization (no API needed)
run_prices.py          →  fetch historical stock prices via OpenBB
run_export.py          →  export tables to CSV / Parquet
run_ai_classify.py     →  [optional] AI-powered classification
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
python run_tdnet.py --start-date 2025-01-01 --end-date 2025-01-31
```

### Scrape EDINET (requires EDINET_API_KEY)

```bash
python run_edinet.py --start-date 2025-01-01 --end-date 2025-01-31
```

### Scrape both sources at once

```bash
# Both (EDINET is skipped gracefully if EDINET_API_KEY is not set)
python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-31

# Explicit source selection
python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-31 --source tdnet
python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-31 --source edinet
```

### Categorize events (keyword-based, no API needed)

```bash
python run_categorize.py
```

### Fetch historical prices

```bash
python run_prices.py --start-date 2025-01-01 --end-date 2025-12-31
```

### Export data

```bash
# Export all tables to CSV and Parquet
python run_export.py

# Specific tables or format
python run_export.py --tables events prices
python run_export.py --format csv
```

### Database health checks

```bash
sqlite3 data/tse_eventbase.db "SELECT source, COUNT(*) FROM events GROUP BY source;"
sqlite3 data/tse_eventbase.db "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC;"
sqlite3 data/tse_eventbase.db "SELECT MIN(event_date), MAX(event_date) FROM events;"
sqlite3 data/tse_eventbase.db "SELECT COUNT(DISTINCT ticker) FROM events;"
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
| `event_type` | earnings / forecast_revision / dividend / buyback / ma / tender_offer / leadership_change / stock_split / large_holding / capital_raise / delisting / other |
| `direction` | positive / negative / neutral (AI) |
| `magnitude` | large / medium / small (AI) |
| `source` | `tdnet` or `edinet` |
| `source_url` | Original document URL |
| `source_doc_id` | Original document ID |
| `raw_json` | Full API response |

### `prices`

Daily OHLCV data keyed by `(ticker, date)`.

### `financials`

Financial metrics extracted from EDINET securities reports: net sales, operating income, net income, total assets, EPS, BPS, ROE, etc.

### `tickers`

Company metadata: sector, market segment (Prime / Standard / Growth), listing/delisting dates.

## API notes

### Yanoshin TDnet API

- URL: `https://webapi.yanoshin.jp/webapi/tdnet/list/YYYYMMDD.json?limit=10000`
- No authentication required
- ~350 events per trading day
- Response: `{"total_count": N, "items": [{"Tdnet": {...}}]}`

### EDINET API

- Wrapped via `edinet-tools` library
- Free tier: 100 calls/day
- Covers: securities reports, large-holding reports (5%+), tender offers, treasury buyback plans

## Analysis examples

### Load into pandas

```python
import pandas as pd

events = pd.read_csv('data/exports/events.csv')
prices = pd.read_csv('data/exports/prices.csv')

# TDnet vs EDINET breakdown
print(events.groupby('source').size())

# Filter for TDnet earnings events
earnings = events[(events['source'] == 'tdnet') & (events['event_type'] == 'earnings')]
```

### Basic event study setup

```python
import pandas as pd

events = pd.read_csv('data/exports/events.csv')
prices = pd.read_csv('data/exports/prices.csv')
prices['date'] = pd.to_datetime(prices['date'])
events['event_date'] = pd.to_datetime(events['event_date'])

def get_price_window(ticker, event_date, prices, window=5):
    mask = (prices['ticker'] == ticker) & \
           (prices['date'] >= event_date - pd.Timedelta(days=window)) & \
           (prices['date'] <= event_date + pd.Timedelta(days=window))
    return prices[mask].sort_values('date')
```

### Filtering for classified events

```python
# After running run_ai_classify.py
classified = events[events['event_type'].notna()]

positive_earnings = classified[
    (classified['event_type'] == 'earnings') &
    (classified['direction'] == 'positive')
]
```

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
