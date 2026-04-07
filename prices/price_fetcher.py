#!/usr/bin/env python3
"""
Price fetcher module for TSE_EventBase project.
Uses OpenBB to fetch historical stock prices for TSE tickers.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import sqlite3
import pandas as pd
import logging
from typing import List, Dict, Optional
from datetime import datetime
from openbb import obb
from config import DB_PATH, SCRAPE_START_DATE, SCRAPE_END_DATE

logger = logging.getLogger(__name__)

class PriceFetcher:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
    
    def get_unique_tickers(self) -> List[str]:
        """
        Get all unique tickers from the events table.
        
        Returns:
            List of unique ticker symbols
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get unique tickers from events table
        cursor.execute("SELECT DISTINCT ticker FROM events WHERE ticker IS NOT NULL AND ticker != ''")
        tickers = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return tickers
    
    def fetch_price_data(self, ticker: str, start_date: str = None, end_date: str = None) -> Optional[pd.DataFrame]:
        """
        Fetch historical price data for a single ticker.
        
        Args:
            ticker: TSE ticker symbol (e.g., '7203')
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            DataFrame with price data or None if error
        """
        if not start_date:
            start_date = SCRAPE_START_DATE
        if not end_date:
            end_date = SCRAPE_END_DATE
        
        try:
            # Format ticker for TSE (append .T)
            tse_ticker = f"{ticker}.T"
            
            logger.info(f"Fetching price data for {tse_ticker} from {start_date} to {end_date}")
            
            # Fetch historical data using OpenBB with yfinance provider
            data = obb.equity.price.historical(
                tse_ticker,
                start_date=start_date,
                end_date=end_date,
                provider="yfinance"
            ).to_dataframe()
            
            if data is None or data.empty:
                logger.warning(f"No price data found for {tse_ticker}")
                return None
            
            # Add ticker column
            data['ticker'] = ticker
            
            # Rename columns to match our database schema
            column_mapping = {
                'open': 'open',
                'high': 'high', 
                'low': 'low',
                'close': 'close',
                'adj_close': 'adj_close',
                'volume': 'volume'
            }
            
            # Handle cases where adjusted close might not be available
            if 'Adj Close' in data.columns:
                data = data.rename(columns={'Adj Close': 'adj_close'})
            elif 'adj_close' in data.columns:
                pass  # Already in the right format
            else:
                data['adj_close'] = data['close']  # Use close as adj_close if not available
            
            # Select and reorder columns to match our schema
            required_cols = ['ticker', 'open', 'high', 'low', 'close', 'adj_close', 'volume']
            available_cols = [col for col in required_cols if col in data.columns]
            data = data[available_cols].reset_index()  # Move date index to column
            
            # Rename date column to 'date' to match our schema
            if 'date' not in data.columns:
                # The date is likely the index, which becomes the first column after reset_index
                if len(data.columns) > 0:
                    data = data.rename(columns={data.columns[0]: 'date'})
            
            return data
            
        except Exception as e:
            logger.error(f"Error fetching price data for {ticker}: {e}")
            return None
    
    def insert_price_data(self, price_df: pd.DataFrame):
        """
        Insert price data into the database.
        
        Args:
            price_df: DataFrame with price data
        """
        if price_df is None or price_df.empty:
            return
        
        conn = sqlite3.connect(self.db_path)
        
        # Insert the data
        price_df.to_sql('prices', conn, if_exists='append', index=False)
        
        conn.close()
    
    def fetch_all_prices(self, start_date: str = None, end_date: str = None, 
                         tickers: List[str] = None) -> int:
        """
        Fetch price data for all unique tickers in the database.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            tickers: Specific list of tickers to fetch (if None, fetch all from DB)
            
        Returns:
            Number of tickers processed
        """
        if not tickers:
            tickers = self.get_unique_tickers()
        
        if not tickers:
            logger.warning("No tickers found in database")
            return 0
        
        logger.info(f"Starting to fetch price data for {len(tickers)} tickers")
        
        processed_count = 0
        failed_count = 0
        
        for i, ticker in enumerate(tickers):
            try:
                logger.info(f"Processing {ticker} ({i+1}/{len(tickers)})")
                
                price_data = self.fetch_price_data(ticker, start_date, end_date)
                
                if price_data is not None and not price_data.empty:
                    self.insert_price_data(price_data)
                    logger.info(f"Successfully inserted {len(price_data)} price records for {ticker}")
                else:
                    logger.warning(f"No price data retrieved for {ticker}")
                
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Failed to process ticker {ticker}: {e}")
                failed_count += 1
                continue
        
        logger.info(f"Price fetching completed. Processed: {processed_count}, Failed: {failed_count}")
        return processed_count

if __name__ == "__main__":
    import argparse
    from config import SCRAPE_START_DATE, SCRAPE_END_DATE
    
    parser = argparse.ArgumentParser(description="Fetch historical stock prices for TSE tickers")
    parser.add_argument("--start-date", default=SCRAPE_START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=SCRAPE_END_DATE, help="End date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    fetcher = PriceFetcher()
    processed = fetcher.fetch_all_prices(start_date=args.start_date, end_date=args.end_date)
    
    print(f"\nPrice fetching completed. Processed {processed} tickers.")