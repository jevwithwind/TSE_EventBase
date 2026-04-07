# TSE_EventBase — Open-source TDnet disclosure scraper for Tokyo Stock Exchange research

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/kazumili/TSE_EventBase/pulls)

## What this does

TSE_EventBase scrapes all timely disclosures (適時開示) from TDnet for Tokyo Stock Exchange (TSE)-listed companies via the Yanoshin Web API, stores them in SQLite, optionally classifies them with AI, and fetches historical stock prices via OpenBB. Designed for academic research on how corporate events influence stock prices and financial metrics.

## Features

- **Full TDnet history**: Access to all timely disclosures from 2016 onwards (~350 events per day)
- **Idempotent/resumable scraping**: Safe to restart, won't duplicate entries
- **AI-powered event classification**: Supports any Anthropic-compatible API (Claude, Qwen, etc.) for automated event categorization
- **Stock price fetching**: Historical price data via OpenBB/yfinance integration
- **Export capabilities**: Export to CSV/Parquet formats for analysis
- **Clean SQLite schema**: Designed specifically for event study research with optimized indexing

## Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/kazumili/TSE_EventBase.git
   cd TSE_EventBase
   ```

2. **Set up environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Initialize database**
   ```bash
   python db/init_db.py
   ```

5. **Run first scrape**
   ```bash
   python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-01
   ```

## Usage Manual

### Core Pipeline (Recommended)

The core workflow is now simplified and does not require AI classification:

1. **Initialize database**: `python db/init_db.py`
2. **Scrape events**: `python run_scrape.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD`
3. **Categorize events**: `python run_categorize.py` (keyword-based, no API required)
4. **Fetch prices**: `python run_prices.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD`
5. **Export data**: `python run_export.py`

### How to scrape

Scrape timely disclosures from TDnet for specific date ranges:

```bash
# Scrape all sources (TDnet + EDINET) for a date range
python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-31

# Scrape only TDnet
python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-31 --source tdnet

# Scrape only EDINET
python run_scrape.py --start-date 2025-01-01 --end-date 2025-01-31 --source edinet
```

### How to categorize (keyword-based)

Categorize events using keyword matching (no API required):

```bash
# Categorize all events using keyword matching
python run_categorize.py

# The script will print a summary of events per category
```

### How to fetch prices

Fetch historical stock prices for all companies in the database:

```bash
# Fetch prices for all companies in the database
python run_prices.py --start-date 2025-01-01 --end-date 2025-12-31

# Default uses the date range from config
python run_prices.py
```

### How to export

Export database tables to various formats:

```bash
# Export all tables to both CSV and Parquet
python run_export.py

# Export specific tables
python run_export.py --tables events prices

# Export only CSV format
python run_export.py --format csv
```

### How to check database contents

Use sqlite3 to perform health checks on your database:

```bash
# Count total events
sqlite3 data/tse_eventbase.db "SELECT COUNT(*) FROM events;"

# Count events by source
sqlite3 data/tse_eventbase.db "SELECT source, COUNT(*) FROM events GROUP BY source;"

# Count events by type (after categorization)
sqlite3 data/tse_eventbase.db "SELECT event_type, COUNT(*) FROM events GROUP BY event_type;"

# Check date range of events
sqlite3 data/tse_eventbase.db "SELECT MIN(event_date), MAX(event_date) FROM events;"

# Count unique tickers
sqlite3 data/tse_eventbase.db "SELECT COUNT(DISTINCT ticker) FROM events;"
```

## Optional: AI-Powered Classification

Researchers who want English translations, sentiment direction, and magnitude scoring can use the optional AI classification step. This requires an Anthropic-compatible API key (supports Claude, Qwen, etc.).

### How to AI classify

```bash
# Classify all uncategorized events using AI
python run_ai_classify.py

# Classify with custom batch size
python run_ai_classify.py --batch-size 25

# Classify only specific event types (filter)
python run_ai_classify.py --filter

# Exclude ETF daily disclosures
python run_ai_classify.py --exclude

# Dry run to see how many events would be processed
python run_ai_classify.py --dry-run
```

Note: The AI classification is an extension for researchers who want enhanced categorization with English translations and sentiment analysis. It supports any Anthropic-compatible API endpoint, including Claude models, Qwen models, or custom endpoints.

# Check date range of events
sqlite3 data/tse_eventbase.db "SELECT MIN(event_date), MAX(event_date) FROM events;"

# Count unique tickers
sqlite3 data/tse_eventbase.db "SELECT COUNT(DISTINCT ticker) FROM events;"
```

## Analyzing the Output (without AI)

### Loading exports into pandas

```python
import pandas as pd

# Load events data
events_df = pd.read_csv('data/exports/events.csv')

# Load prices data
prices_df = pd.read_csv('data/exports/prices.csv')

# Merge events with prices
merged_df = pd.merge(events_df, prices_df, on='ticker', how='inner')
```

### Basic event study setup

```python
import pandas as pd
import numpy as np

# Merge events with price data
events = pd.read_csv('data/exports/events.csv')
prices = pd.read_csv('data/exports/prices.csv')

# Calculate abnormal returns around event dates
def calculate_abnormal_returns(events, prices, window=5):
    # Implementation for calculating CAR (Cumulative Abnormal Returns)
    pass
```

### Example: Filtering for earnings announcements

```python
import matplotlib.pyplot as plt

# Filter for earnings announcements (決算短信)
earnings_events = events[events['headline'].str.contains('決算|earnings|四半期|通期')]

# Plot Cumulative Abnormal Returns around event dates
# Implementation would involve aligning price data around event dates
```

Useful Python libraries for analysis: `easy_es`, `statsmodels`, `matplotlib`, `seaborn`, `plotly`.

## Analyzing the Output (with AI)

### Using classified events

Leverage the structured event classifications (event_type, direction, magnitude) as inputs for deeper analysis:

```python
# Use event_type, direction, and magnitude for stratified analysis
classified_events = events[events['event_type'].notna()]

# Group by event characteristics
earnings_positive = classified_events[
    (classified_events['event_type'] == 'earnings') & 
    (classified_events['direction'] == 'positive')
]
```

### Narrative analysis with LLMs

Feed event descriptions and price windows to an LLM for narrative analysis:

```python
# Example of feeding data to an LLM for analysis
prompt = f"""
Analyze the market reaction to this corporate event:
Event: {event.headline}
Company: {event.company_name}
Date: {event.event_date}
Type: {event.event_type}
Direction: {event.direction}
Magnitude: {event.magnitude}

Price data 5 days before and after:
{price_window}
"""
```

### OpenBB AI integration

Use OpenBB's AI agent for follow-up research:

```python
from openbb import obb

# Use OpenBB's AI capabilities for additional insights
research = obb.ai.summary(prompt="Analyze recent earnings trends in Japanese tech sector")
```

## Database Schema

The database contains four main tables optimized for event study research:

### `events` table
- `id`: Primary key
- `ticker`: TSE code (e.g., "7203")
- `company_name`: Japanese company name
- `company_name_en`: English company name (if available)
- `event_date`: Date of disclosure
- `event_time`: Time of disclosure (if available)
- `headline`: Original Japanese headline
- `headline_en`: English translation (AI-generated)
- `summary`: AI-generated summary
- `event_type`: Classification (earnings, forecast_revision, dividend, buyback, ma, tender_offer, leadership_change, stock_split, large_holding, capital_raise, delisting, other)
- `event_subtype`: More granular classification
- `direction`: Market sentiment (positive, negative, neutral)
- `magnitude`: Impact scale (large, medium, small)
- `source`: "tdnet" or "edinet"
- `source_url`: Original document URL
- `source_doc_id`: Original document ID
- `raw_json`: Full original API response
- `classified_at`: Timestamp when AI classification was done
- `created_at`: Record creation timestamp

### `prices` table
- `ticker`: TSE code (e.g., "7203")
- `date`: Trading date
- `open`: Opening price
- `high`: Highest price
- `low`: Lowest price
- `close`: Closing price
- `adj_close`: Adjusted closing price
- `volume`: Trading volume

### `financials` table
- `id`: Primary key
- `ticker`: TSE code
- `company_name`: Company name
- `fiscal_year`: Fiscal year
- `fiscal_period`: Period (annual, q1, q2, q3, q4)
- `accounting_standard`: Accounting standard (JP GAAP, IFRS, US GAAP)
- `net_sales`: Net sales
- `operating_income`: Operating income
- `ordinary_income`: Ordinary income
- `net_income`: Net income
- `total_assets`: Total assets
- `total_equity`: Total equity
- `eps`: Earnings per share
- `bps`: Book value per share
- `roe`: Return on equity
- `source_doc_id`: Source document ID
- `raw_json`: Raw document data
- `created_at`: Creation timestamp

### `tickers` table
- `ticker`: TSE code (primary key)
- `company_name`: Japanese company name
- `company_name_en`: English company name
- `sector`: Business sector
- `market_segment`: Market segment (Prime, Standard, Growth)
- `listed_date`: Listing date
- `delisted_date`: Delisting date (if applicable)

## API Notes

### Yanoshin TDnet API behavior

- **Date format**: Uses YYYYMMDD format in URLs (e.g., `20250401.json` for April 1, 2025)
- **Limit parameter**: Requires `?limit=10000` to retrieve all events in a single request (otherwise defaults to 100)
- **Typical volume**: ~350 events per trading day
- **Response structure**: `{"total_count": N, "items": [{"Tdnet": {...}}]}`

## Contributing

We welcome contributions! Here's how to get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Commit your changes (`git commit -m 'Add amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

Please make sure to update tests as appropriate and follow the existing code style.

## Citation

If you use TSE_EventBase in academic research, please cite it as:

```bibtex
@software{TSE_EventBase,
  author = {Li, Kazumi},
  title = {TSE_EventBase: Open-source TDnet disclosure scraper for Tokyo Stock Exchange research},
  year = {2025-2026},
  url = {https://github.com/kazumili/TSE_EventBase},
  version = {1.0.0}
}
```

## License

MIT