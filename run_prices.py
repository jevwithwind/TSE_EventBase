#!/usr/bin/env python3
"""
Main entry point for fetching historical stock prices.
This script runs the price fetcher for all tickers in the database.
"""

import argparse
import logging
from config import SCRAPE_START_DATE, SCRAPE_END_DATE
from prices.price_fetcher import PriceFetcher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Fetch historical stock prices for TSE tickers")
    parser.add_argument("--start-date", default="2016-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="2023-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print how many tickers will be fetched without fetching")
    args = parser.parse_args()
    
    logger.info(f"Starting price fetch for date range: {args.start_date} to {args.end_date}")
    
    fetcher = PriceFetcher()
    processed = fetcher.fetch_all_prices(start_date=args.start_date, end_date=args.end_date, dry_run=args.dry_run)
    
    logger.info("Price fetching completed successfully!")
    print(f"\nPrice fetching completed!")
    if not args.dry_run:
        print(f"Processed {processed} tickers.")
    
    return 0

if __name__ == "__main__":
    exit(main())