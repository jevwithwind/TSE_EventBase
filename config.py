import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_PATH = os.getenv("DB_PATH", "data/tse_eventbase.db")

# Date range configuration (default: last 5 years)
SCRAPE_START_DATE = os.getenv("SCRAPE_START_DATE", (datetime.now() - timedelta(days=5*365)).strftime("%Y-%m-%d"))
SCRAPE_END_DATE = os.getenv("SCRAPE_END_DATE", datetime.now().strftime("%Y-%m-%d"))

# API Keys
EDINET_API_KEY = os.getenv("EDINET_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

# Model configuration for AI classification
MODEL = os.getenv("MODEL", "gpt-4o")

# Export directory
EXPORT_DIR = os.getenv("EXPORT_DIR", "data/exports")

# Event type constants
EVENT_TYPES = [
    "earnings",
    "forecast_revision", 
    "dividend",
    "buyback",
    "ma",  # mergers and acquisitions
    "tender_offer",
    "leadership_change",
    "stock_split",
    "large_holding",
    "capital_raise",
    "delisting",
    "other"
]

DIRECTIONS = [
    "positive",
    "negative", 
    "neutral"
]

MAGNITUDES = [
    "large",
    "medium",
    "small"
]

# Rate limiting configurations
TDNET_DELAY = float(os.getenv("TDNET_DELAY", "1.0"))  # seconds between requests
EDINET_RATE_LIMIT_CALLS = int(os.getenv("EDINET_RATE_LIMIT_CALLS", "100"))  # per day
EDINET_RATE_LIMIT_SLEEP = int(os.getenv("EDINET_RATE_LIMIT_SLEEP", "900"))  # seconds to sleep between calls

# Classification batch size
CLASSIFICATION_BATCH_SIZE = int(os.getenv("CLASSIFICATION_BATCH_SIZE", "50"))

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "tse_eventbase.log")