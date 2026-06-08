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
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_doc_id ON events(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);

-- J-Quants financial statement summaries (/fins/summary), Stage 2 source.
-- One row per disclosure; column comments map back to the V2 abbreviated field names.
CREATE TABLE IF NOT EXISTS jquants_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    disclosure_no TEXT,                -- DiscNo (unique disclosure id from J-Quants)
    local_code TEXT,                   -- Code: 5-digit J-Quants code (e.g. "72030")
    ticker TEXT,                       -- 4-digit TSE code (local_code[:4]); joins to events.ticker
    disclosed_date DATE,               -- DiscDate (YYYY-MM-DD)
    disclosed_time TEXT,               -- DiscTime
    doc_type TEXT,                     -- DocType (period + consolidated/non + JP/IFRS)
    period_type TEXT,                  -- CurPerType: 1Q / 2Q / 3Q / FY
    current_fy_end DATE,               -- CurFYEn (fiscal-year-end; same-FY join key)
    -- consolidated actual results (cumulative for the current period)
    net_sales REAL,                    -- Sales
    operating_profit REAL,             -- OP
    ordinary_profit REAL,              -- OdP
    profit REAL,                       -- NP (profit attributable to owners of parent)
    eps REAL,                          -- EPS
    total_assets REAL,                 -- TA
    equity REAL,                       -- Eq
    bps REAL,                          -- BPS
    -- company forecast for the current full fiscal year
    forecast_net_sales REAL,           -- FSales
    forecast_operating_profit REAL,    -- FOP
    forecast_ordinary_profit REAL,     -- FOdP
    forecast_profit REAL,              -- FNP
    forecast_eps REAL,                 -- FEPS
    -- annual dividends per share
    result_dps_annual REAL,            -- DivAnn
    forecast_dps_annual REAL,          -- FDivAnn
    raw_json TEXT,                     -- full original /fins/summary row as JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jq_disclosure_no ON jquants_statements(disclosure_no);
CREATE INDEX IF NOT EXISTS idx_jq_ticker_date ON jquants_statements(ticker, disclosed_date);
CREATE INDEX IF NOT EXISTS idx_jq_ticker_fyend ON jquants_statements(ticker, current_fy_end);