#!/usr/bin/env python3
"""
Main entry point for scraping TDnet and EDINET data.
This script runs both scrapers to collect corporate events.
"""

import argparse
import logging
from datetime import datetime
from config import SCRAPE_START_DATE, SCRAPE_END_DATE, EDINET_API_KEY
from scrapers.tdnet_scraper import TDnetScraper
from scrapers.edinet_scraper import EdinetScraper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Scrape TDnet and EDINET for corporate events")
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    parser.add_argument("--source", choices=["tdnet", "edinet", "both"], default="both", 
                       help="Which source to scrape (default: both)")
    args = parser.parse_args()
    
    logger.info(f"Starting scrape for date range: {args.start_date} to {args.end_date}")
    logger.info(f"Scraping source(s): {args.source}")
    
    total_events = 0
    
    if args.source in ["tdnet", "both"]:
        logger.info("Starting TDnet scraping...")
        tdnet_scraper = TDnetScraper()
        tdnet_events = tdnet_scraper.scrape_date_range(args.start_date, args.end_date)
        total_events += tdnet_events
        logger.info(f"TDnet scraping completed. Added {tdnet_events} events.")
    
    if args.source in ["edinet", "both"]:
        if not EDINET_API_KEY:
            logger.error("EDINET_API_KEY environment variable is required for EDINET scraping")
            return 1
        
        logger.info("Starting EDINET scraping...")
        edinet_scraper = EdinetScraper(api_key=EDINET_API_KEY)
        edinet_events, edinet_financials = edinet_scraper.scrape_date_range(args.start_date, args.end_date)
        total_events += edinet_events
        logger.info(f"EDINET scraping completed. Added {edinet_events} events and {edinet_financials} financial records.")
    
    logger.info(f"All scraping completed. Total events added: {total_events}")
    print(f"\nScraping completed successfully!")
    print(f"Total events added to database: {total_events}")
    
    return 0

if __name__ == "__main__":
    exit(main())