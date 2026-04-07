#!/usr/bin/env python3
"""
EDINET scraper module for TSE_EventBase project.
Fetches securities reports and major filings from EDINET.
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
from edinet_tools import documents
from config import DB_PATH, EDINET_API_KEY, EDINET_RATE_LIMIT_CALLS, EDINET_RATE_LIMIT_SLEEP

logger = logging.getLogger(__name__)

class EdinetScraper:
    def __init__(self, api_key: str, db_path: str = DB_PATH):
        self.api_key = api_key
        self.db_path = db_path
        self.base_url = "https://disclosure.edinet-fsa.go.jp/api/v1"
        
        # Track API calls to respect rate limits
        self.daily_calls = 0
        self.last_call_date = None
        
    def _check_rate_limit(self):
        """
        Check and enforce EDINET API rate limits.
        Free tier allows 100 calls per day.
        """
        today = datetime.now().date()
        
        if self.last_call_date != today:
            self.daily_calls = 0
            self.last_call_date = today
        
        if self.daily_calls >= EDINET_RATE_LIMIT_CALLS:
            logger.info(f"Daily rate limit reached ({EDINET_RATE_LIMIT_CALLS} calls). Sleeping for {EDINET_RATE_LIMIT_SLEEP}s.")
            time.sleep(EDINET_RATE_LIMIT_SLEEP)
            self.daily_calls = 0  # Reset after sleep
        
        # Increment call counter
        self.daily_calls += 1
        
        # Sleep to respect rate limits
        time.sleep(EDINET_RATE_LIMIT_SLEEP)
    
    def _fetch_filings_for_date(self, date_str: str) -> Optional[List[Dict]]:
        """
        Fetch filings for a specific date from EDINET.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            List of filing dictionaries or None if error
        """
        self._check_rate_limit()
        
        try:
            # Use edinet_tools to fetch documents for the date
            docs = documents(date_str)
            
            if docs is None:
                logger.warning(f"No documents returned for {date_str}")
                return []
            
            # Filter for relevant document types
            relevant_docs = []
            for doc in docs:
                # Focus on document types: securities reports, large holding reports, tender offer reports, treasury stock reports
                doc_type = doc.get('docType', '').lower()
                sec_code = doc.get('secCode', '')
                
                # Only include relevant document types
                if any(keyword in doc_type for keyword in [
                    'securities', 'report', '有価証券', '報告書', '持分', 'large holding', 
                    'tender', 'offer', 'treasury', 'stock', '株式', '買付', '公開', '譲渡'
                ]):
                    relevant_docs.append(doc)
            
            return relevant_docs
            
        except Exception as e:
            logger.error(f"Error fetching filings for {date_str}: {e}")
            return None
    
    def _insert_event(self, event_data: Dict):
        """
        Insert a single event into the database.
        
        Args:
            event_data: Dictionary containing event information
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if event already exists (idempotency)
        cursor.execute(
            "SELECT COUNT(*) FROM events WHERE source_doc_id = ? AND source = 'edinet'",
            (event_data.get('source_doc_id'),)
        )
        exists = cursor.fetchone()[0] > 0
        
        if exists:
            logger.debug(f"Event with doc_id {event_data.get('source_doc_id')} already exists, skipping")
            conn.close()
            return
        
        # Insert the event
        cursor.execute("""
            INSERT INTO events (
                ticker, company_name, event_date, headline, 
                source, source_url, source_doc_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_data.get('ticker'),
            event_data.get('company_name'),
            event_data.get('event_date'),
            event_data.get('headline'),
            'edinet',
            event_data.get('source_url'),
            event_data.get('source_doc_id'),
            json.dumps(event_data.get('raw_json', {}), ensure_ascii=False)
        ))
        
        conn.commit()
        conn.close()
    
    def _insert_financial(self, financial_data: Dict):
        """
        Insert financial data into the financials table.
        
        Args:
            financial_data: Dictionary containing financial information
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Insert the financial data
        cursor.execute("""
            INSERT INTO financials (
                ticker, company_name, fiscal_year, fiscal_period,
                accounting_standard, net_sales, operating_income, ordinary_income,
                net_income, total_assets, total_equity, eps, bps, roe,
                source_doc_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            financial_data.get('ticker'),
            financial_data.get('company_name'),
            financial_data.get('fiscal_year'),
            financial_data.get('fiscal_period'),
            financial_data.get('accounting_standard'),
            financial_data.get('net_sales'),
            financial_data.get('operating_income'),
            financial_data.get('ordinary_income'),
            financial_data.get('net_income'),
            financial_data.get('total_assets'),
            financial_data.get('total_equity'),
            financial_data.get('eps'),
            financial_data.get('bps'),
            financial_data.get('roe'),
            financial_data.get('source_doc_id'),
            json.dumps(financial_data.get('raw_json', {}), ensure_ascii=False)
        ))
        
        conn.commit()
        conn.close()
    
    def scrape_date_range(self, start_date: str, end_date: str) -> tuple[int, int]:
        """
        Scrape EDINET filings for a date range.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            Tuple of (events_count, financials_count)
        """
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        total_events = 0
        total_financials = 0
        
        current_date = start_dt
        while current_date <= end_dt:
            date_str = current_date.strftime("%Y-%m-%d")
            
            logger.info(f"Fetching EDINET filings for {date_str}")
            
            filings = self._fetch_filings_for_date(date_str)
            
            if filings is None:
                logger.warning(f"No data returned for {date_str}, continuing...")
                current_date += timedelta(days=1)
                continue
            
            events_count = 0
            financials_count = 0
            
            for filing in filings:
                try:
                    # Extract event information
                    event_data = self._parse_filing_event(filing, date_str)
                    
                    if event_data:
                        self._insert_event(event_data)
                        events_count += 1
                        total_events += 1
                    
                    # Try to extract financial data if possible
                    financial_data = self._parse_financial_data(filing, date_str)
                    
                    if financial_data:
                        self._insert_financial(financial_data)
                        financials_count += 1
                        total_financials += 1
                        
                except Exception as e:
                    logger.error(f"Error processing filing for {date_str}: {e}")
                    continue
            
            logger.info(f"Processed {events_count} events and {financials_count} financial records for {date_str}")
            
            current_date += timedelta(days=1)
        
        logger.info(f"EDINET scraping completed. Total events: {total_events}, Financials: {total_financials}")
        return total_events, total_financials
    
    def _parse_filing_event(self, filing: Dict, date_str: str) -> Optional[Dict]:
        """
        Parse a filing dictionary and extract event information.
        
        Args:
            filing: Raw filing dictionary from API
            date_str: Date string in YYYY-MM-DD format
            
        Returns:
            Parsed event data dictionary or None if parsing fails
        """
        try:
            # Extract relevant information from filing
            sec_code = filing.get('secCode', '')
            if sec_code and sec_code != '000000':
                # Extract first 4 digits for TSE code
                ticker = sec_code[:4]
            else:
                ticker = filing.get('filerName', '')  # Fallback to name if no code
            
            company_name = filing.get('filerName', '') or filing.get('company_name', '')
            headline = filing.get('docDescription', '') or filing.get('title', '') or filing.get('subject', '')
            
            # Skip if essential fields are missing
            if not ticker or not headline:
                logger.debug(f"Skipping filing with missing ticker or headline: {filing.get('docDescription', 'No description')}")
                return None
            
            event_data = {
                'ticker': ticker,
                'company_name': company_name,
                'event_date': date_str,
                'headline': headline,
                'source_url': filing.get('pdfUrl') or filing.get('attachDocUrl') or filing.get('xbrlUrl'),
                'source_doc_id': filing.get('docId') or filing.get('docID') or filing.get('id'),
                'raw_json': filing
            }
            
            return event_data
            
        except Exception as e:
            logger.error(f"Error parsing filing event: {e}")
            return None
    
    def _parse_financial_data(self, filing: Dict, date_str: str) -> Optional[Dict]:
        """
        Attempt to parse financial data from a filing.
        
        Args:
            filing: Raw filing dictionary from API
            date_str: Date string in YYYY-MM-DD format
            
        Returns:
            Parsed financial data dictionary or None if not applicable
        """
        try:
            # Only attempt to parse financial data for certain document types
            doc_type = filing.get('docType', '').lower()
            
            if not any(keyword in doc_type for keyword in [
                'securities', 'report', '有価証券', '報告書', 'financial', '財務'
            ]):
                return None
            
            # Extract basic info
            sec_code = filing.get('secCode', '')
            ticker = sec_code[:4] if sec_code and sec_code != '000000' else None
            company_name = filing.get('filerName', '')
            
            if not ticker:
                return None
            
            # For now, return minimal financial data - in a real implementation,
            # you would extract XBRL data from the filing
            financial_data = {
                'ticker': ticker,
                'company_name': company_name,
                'fiscal_year': filing.get('ediDate', date_str)[:4],  # Extract year from filing date
                'fiscal_period': 'annual',  # Default, would be determined from document
                'accounting_standard': 'JP GAAP',  # Default assumption
                'net_sales': None,
                'operating_income': None,
                'ordinary_income': None,
                'net_income': None,
                'total_assets': None,
                'total_equity': None,
                'eps': None,
                'bps': None,
                'roe': None,
                'source_doc_id': filing.get('docId') or filing.get('docID') or filing.get('id'),
                'raw_json': filing
            }
            
            return financial_data
            
        except Exception as e:
            logger.error(f"Error parsing financial data: {e}")
            return None

if __name__ == "__main__":
    # Example usage
    import argparse
    from config import SCRAPE_START_DATE, SCRAPE_END_DATE, EDINET_API_KEY
    
    if not EDINET_API_KEY:
        raise ValueError("EDINET_API_KEY environment variable is required")
    
    parser = argparse.ArgumentParser(description="Scrape EDINET filings")
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    scraper = EdinetScraper(api_key=EDINET_API_KEY)
    events_count, financials_count = scraper.scrape_date_range(args.start_date, args.end_date)
    
    print(f"Scraping completed. Added {events_count} events and {financials_count} financial records to the database.")