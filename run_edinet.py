#!/usr/bin/env python3
"""
Scrape regulatory filings from EDINET (FSA disclosure system).
Requires EDINET_API_KEY — register at https://disclosure.edinet-fsa.go.jp/
"""

import argparse
import logging
from config import SCRAPE_START_DATE, SCRAPE_END_DATE, EDINET_API_KEY
from scrapers.edinet_scraper import EdinetScraper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    if not EDINET_API_KEY:
        logger.error("EDINET_API_KEY is not set. Register at https://disclosure.edinet-fsa.go.jp/")
        return 1

    parser = argparse.ArgumentParser(
        description="Scrape EDINET regulatory filings (requires EDINET_API_KEY)"
    )
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    logger.info(f"Scraping EDINET from {args.start_date} to {args.end_date}")

    scraper = EdinetScraper(api_key=EDINET_API_KEY)
    events, financials = scraper.scrape_date_range(args.start_date, args.end_date)

    print(f"\nEDINET scraping completed. Added {events} events and {financials} financial records.")
    return 0


if __name__ == "__main__":
    exit(main())
