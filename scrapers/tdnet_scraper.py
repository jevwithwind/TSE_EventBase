#!/usr/bin/env python3
"""
TDnet scraper module for TSE_EventBase project.
Fetches timely disclosures from TDnet via Yanoshin Web API.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import requests
import json
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
from config import DB_PATH, TDNET_DELAY

logger = logging.getLogger(__name__)

class TDnetScraper:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.base_url = "https://webapi.yanoshin.jp/webapi/tdnet/list/{date}.json"
        
    def _is_weekend_or_holiday(self, date_str: str) -> bool:
        """
        Check if the given date is a weekend or holiday (no disclosures).
        For simplicity, we'll just check weekends. In production, you'd want to check against a holiday calendar.
        """
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.weekday() >= 5  # Saturday = 5, Sunday = 6
    
    def _fetch_disclosures_for_date(self, date_str: str) -> Optional[List[Dict]]:
        """
        Fetch disclosures for a specific date from Yanoshin API.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            List of disclosure dictionaries or None if error
        """
        # Convert date format from YYYY-MM-DD to YYYYMMDD
        date_formatted = date_str.replace("-", "")
        # Use limit=10000 to get all items in a single request
        url = f"{self.base_url.format(date=date_formatted)}?limit=10000"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            # Handle the correct response structure: {"total_count": 300, "items": [...]}
            if isinstance(data, dict) and 'items' in data:
                items = data['items']
                # Extract the actual disclosure data from the nested Tdnet object
                disclosures = []
                for item in items:
                    if 'Tdnet' in item:
                        # Add the original item for raw_json storage
                        disclosure = item['Tdnet'].copy()
                        disclosure['raw_item'] = item
                        disclosures.append(disclosure)
                return disclosures
            else:
                logger.warning(f"Unexpected response structure for date {date_str}: {type(data)}")
                return []
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching disclosures for {date_str}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response for {date_str}: {e}")
            return None
    
    def _insert_events_batch(self, conn: sqlite3.Connection, rows: list):
        """
        Batch-insert a day's worth of events using INSERT OR IGNORE.
        Duplicate source_doc_ids are silently skipped via the unique index.

        Args:
            conn: Open SQLite connection (kept alive across the day's work)
            rows: List of 9-tuples matching the INSERT column order
        """
        if not rows:
            return
        conn.executemany("""
            INSERT OR IGNORE INTO events (
                ticker, company_name, event_date, event_time, headline,
                source, source_url, source_doc_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
    
    def scrape_date_range(self, start_date: str, end_date: str) -> int:
        """
        Scrape TDnet disclosures for a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Number of events inserted (duplicates excluded)
        """
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        # Single connection for the entire run with performance PRAGMAs
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -64000;")

        total_events = 0

        try:
            current_date = start_dt
            while current_date <= end_dt:
                date_str = current_date.strftime("%Y-%m-%d")

                if self._is_weekend_or_holiday(date_str):
                    logger.info(f"Skipping {date_str} (weekend/holiday)")
                    current_date += timedelta(days=1)
                    continue

                logger.info(f"Fetching disclosures for {date_str}")
                disclosures = self._fetch_disclosures_for_date(date_str)

                if disclosures is None:
                    logger.warning(f"No data returned for {date_str}, continuing...")
                    current_date += timedelta(days=1)
                    continue

                # Parse all disclosures for the day into tuples
                rows = []
                for disclosure in disclosures:
                    try:
                        event_data = self._parse_disclosure(disclosure, date_str)
                        if event_data:
                            rows.append((
                                event_data.get('ticker'),
                                event_data.get('company_name'),
                                event_data.get('event_date'),
                                event_data.get('event_time'),
                                event_data.get('headline'),
                                'tdnet',
                                event_data.get('source_url'),
                                event_data.get('source_doc_id'),
                                json.dumps(event_data.get('raw_json', {}), ensure_ascii=False)
                            ))
                    except Exception as e:
                        logger.error(f"Error processing disclosure for {date_str}: {e}")
                        continue

                # Single batch insert + commit for the entire day
                before = conn.execute("SELECT changes()").fetchone()[0]
                self._insert_events_batch(conn, rows)
                inserted = conn.execute("SELECT changes()").fetchone()[0]
                total_events += inserted

                logger.info(f"Processed {date_str}: {len(rows)} parsed, {inserted} inserted")

                time.sleep(TDNET_DELAY)
                current_date += timedelta(days=1)

        finally:
            conn.close()

        logger.info(f"TDnet scraping completed. Total events inserted: {total_events}")
        return total_events
    
    def _parse_disclosure(self, disclosure: Dict, date_str: str) -> Optional[Dict]:
        """
        Parse a disclosure dictionary and extract relevant fields.
        
        Args:
            disclosure: Raw disclosure dictionary from API (nested Tdnet object)
            date_str: Date string in YYYY-MM-DD format
            
        Returns:
            Parsed event data dictionary or None if parsing fails
        """
        try:
            # Extract fields from the correct API response structure
            ticker = disclosure.get('company_code', '')[:4]  # Use company_code from Tdnet object
            if not ticker or ticker == '0000':
                ticker = disclosure.get('sec_code', '')[:4]
            
            company_name = disclosure.get('company_name', '')  # Use company_name from Tdnet object
            headline = disclosure.get('title', '')  # Use title from Tdnet object
            
            # Skip if essential fields are missing
            if not ticker or not headline:
                logger.debug(f"Skipping disclosure with missing ticker or headline: {disclosure.get('title', 'No title')}")
                return None
            
            # Extract time from pubdate — API returns "YYYY-MM-DD HH:MM:SS" (space-separated)
            pubdate = disclosure.get('pubdate', '') or ''
            event_time = None
            try:
                if ' ' in pubdate:          # "2016-01-04 15:30:00"
                    event_time = pubdate.split(' ')[1][:5]
                elif 'T' in pubdate:        # ISO fallback "2016-01-04T15:30:00+09:00"
                    dt = datetime.fromisoformat(pubdate.replace('Z', '+00:00'))
                    event_time = dt.strftime('%H:%M')
            except Exception:
                pass
            
            event_data = {
                'ticker': ticker,
                'company_name': company_name,
                'event_date': date_str,
                'event_time': event_time,
                'headline': headline,
                'source_url': disclosure.get('pdf_url') or disclosure.get('url') or disclosure.get('link'),
                'source_doc_id': disclosure.get('id') or disclosure.get('doc_id') or disclosure.get('seq_no'),
                'raw_json': disclosure
            }
            
            return event_data
            
        except Exception as e:
            logger.error(f"Error parsing disclosure: {e}")
            return None

if __name__ == "__main__":
    # Example usage
    import argparse
    from config import SCRAPE_START_DATE, SCRAPE_END_DATE
    
    parser = argparse.ArgumentParser(description="Scrape TDnet disclosures")
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    scraper = TDnetScraper()
    events_count = scraper.scrape_date_range(args.start_date, args.end_date)
    
    print(f"Scraping completed. Added {events_count} events to the database.")