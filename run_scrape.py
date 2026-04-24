#!/usr/bin/env python3
"""
Orchestrator: run TDnet and/or EDINET scrapers.
For a single source use run_tdnet.py or run_edinet.py directly.
"""

import argparse
import logging
from config import SCRAPE_START_DATE, SCRAPE_END_DATE, EDINET_API_KEY
from scrapers.tdnet_scraper import TDnetScraper
from scrapers.edinet_scraper import EdinetScraper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape TDnet and/or EDINET corporate events"
    )
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--source",
        choices=["tdnet", "edinet", "both"],
        default="both",
        help="Data source to scrape (default: both)"
    )
    args = parser.parse_args()

    logger.info(f"Scraping {args.source} from {args.start_date} to {args.end_date}")
    total_events = 0

    if args.source in ("tdnet", "both"):
        logger.info("Starting TDnet scraping...")
        tdnet_events = TDnetScraper().scrape_date_range(args.start_date, args.end_date)
        total_events += tdnet_events
        logger.info(f"TDnet done — {tdnet_events} events added.")

    if args.source in ("edinet", "both"):
        if not EDINET_API_KEY:
            logger.error("EDINET_API_KEY is not set — skipping EDINET. Set it in .env to enable.")
            if args.source == "edinet":
                return 1
        else:
            logger.info("Starting EDINET scraping...")
            edinet_events, edinet_financials = EdinetScraper(api_key=EDINET_API_KEY).scrape_date_range(
                args.start_date, args.end_date
            )
            total_events += edinet_events
            logger.info(f"EDINET done — {edinet_events} events and {edinet_financials} financial records added.")

    print(f"\nScraping completed. Total events added: {total_events}")
    return 0


if __name__ == "__main__":
    exit(main())
