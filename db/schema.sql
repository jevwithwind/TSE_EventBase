CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,              -- TSE code e.g. "7203"
    company_name TEXT,                 -- Japanese name
    company_name_en TEXT,              -- English name if available
    event_date DATE NOT NULL,          -- Date of disclosure
    event_time TEXT,                   -- Time of disclosure if available
    headline TEXT NOT NULL,            -- Original Japanese headline
    headline_en TEXT,                  -- English translation (AI-generated)
    summary TEXT,                      -- AI-generated summary
    event_type TEXT,                   -- earnings, forecast_revision, dividend, buyback, ma, tender_offer, leadership_change, stock_split, large_holding, capital_raise, delisting, other
    event_subtype TEXT,                -- More granular classification
    direction TEXT,                    -- positive, negative, neutral
    magnitude TEXT,                    -- large, medium, small
    source TEXT NOT NULL,              -- "tdnet" or "edinet"
    source_url TEXT,
    source_doc_id TEXT,                -- Original document ID
    raw_json TEXT,                     -- Full original API response
    classified_at TIMESTAMP,           -- When AI classification was done
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,              -- TSE code e.g. "7203"
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS financials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT,
    fiscal_year TEXT,
    fiscal_period TEXT,                -- "annual", "q1", "q2", "q3", "q4"
    accounting_standard TEXT,          -- "JP GAAP", "IFRS", "US GAAP"
    net_sales REAL,
    operating_income REAL,
    ordinary_income REAL,
    net_income REAL,
    total_assets REAL,
    total_equity REAL,
    eps REAL,
    bps REAL,
    roe REAL,
    source_doc_id TEXT,
    raw_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickers (
    ticker TEXT PRIMARY KEY,           -- TSE code
    company_name TEXT,
    company_name_en TEXT,
    sector TEXT,
    market_segment TEXT,               -- Prime, Standard, Growth
    listed_date DATE,
    delisted_date DATE
);

CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);