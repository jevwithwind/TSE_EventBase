#!/usr/bin/env python3
"""
Scrape timely disclosures from TDnet via the Yanoshin Web API.
No API key required.
"""

import argparse
import logging
from config import SCRAPE_START_DATE, SCRAPE_END_DATE
from scrapers.tdnet_scraper import TDnetScraper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape TDnet timely disclosures (Yanoshin API — no key required)"
    )
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    logger.info(f"Scraping TDnet from {args.start_date} to {args.end_date}")

    scraper = TDnetScraper()
    events = scraper.scrape_date_range(args.start_date, args.end_date)

    print(f"\nTDnet scraping completed. Added {events} events.")
    return 0


if __name__ == "__main__":
    exit(main())
