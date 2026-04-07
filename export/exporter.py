#!/usr/bin/env python3
"""
Exporter module for TSE_EventBase project.
Exports database tables to CSV and Parquet formats.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import sqlite3
import pandas as pd
import os
from pathlib import Path
import logging
from typing import Dict, List
from config import DB_PATH, EXPORT_DIR

logger = logging.getLogger(__name__)

class DataExporter:
    def __init__(self, db_path: str = DB_PATH, export_dir: str = EXPORT_DIR):
        self.db_path = db_path
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
    
    def export_table_to_csv(self, table_name: str, filename: str = None) -> str:
        """
        Export a database table to CSV format.
        
        Args:
            table_name: Name of the table to export
            filename: Output filename (without extension). If None, uses table_name
            
        Returns:
            Path to the exported file
        """
        if filename is None:
            filename = table_name
        
        # Read table from database
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        conn.close()
        
        # Create output path
        output_path = self.export_dir / f"{filename}.csv"
        
        # Export to CSV
        df.to_csv(output_path, index=False, encoding='utf-8')
        
        logger.info(f"Exported {len(df)} rows from '{table_name}' to {output_path}")
        return str(output_path)
    
    def export_table_to_parquet(self, table_name: str, filename: str = None) -> str:
        """
        Export a database table to Parquet format.
        
        Args:
            table_name: Name of the table to export
            filename: Output filename (without extension). If None, uses table_name
            
        Returns:
            Path to the exported file
        """
        if filename is None:
            filename = table_name
        
        # Read table from database
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        conn.close()
        
        # Create output path
        output_path = self.export_dir / f"{filename}.parquet"
        
        # Export to Parquet
        df.to_parquet(output_path, index=False)
        
        logger.info(f"Exported {len(df)} rows from '{table_name}' to {output_path}")
        return str(output_path)
    
    def export_all_tables(self) -> Dict[str, List[str]]:
        """
        Export all tables to both CSV and Parquet formats.
        
        Returns:
            Dictionary mapping table names to lists of exported file paths
        """
        # Get all table names from the database
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        # Exclude sqlite internal tables
        tables = [t for t in tables if not t.startswith('sqlite_')]
        
        results = {}
        
        for table in tables:
            logger.info(f"Exporting table: {table}")
            exported_files = []
            
            # Export to CSV
            csv_path = self.export_table_to_csv(table)
            exported_files.append(csv_path)
            
            # Export to Parquet
            parquet_path = self.export_table_to_parquet(table)
            exported_files.append(parquet_path)
            
            results[table] = exported_files
        
        return results
    
    def export_merged_data(self, event_window_days: int = 30) -> str:
        """
        Export a merged dataset with events and surrounding price data.
        
        Args:
            event_window_days: Number of days before/after each event to include price data
            
        Returns:
            Path to the exported file
        """
        conn = sqlite3.connect(self.db_path)
        
        # Query to join events with prices, including a window around each event
        query = f"""
        SELECT 
            e.id as event_id,
            e.ticker,
            e.company_name,
            e.event_date,
            e.headline,
            e.event_type,
            e.direction,
            e.magnitude,
            p.date as price_date,
            p.open,
            p.high,
            p.low,
            p.close,
            p.adj_close,
            p.volume,
            julianday(p.date) - julianday(e.event_date) as days_from_event
        FROM events e
        LEFT JOIN prices p ON e.ticker = p.ticker
            AND julianday(p.date) BETWEEN julianday(e.event_date) - {event_window_days} 
                                       AND julianday(e.event_date) + {event_window_days}
        ORDER BY e.ticker, e.event_date, p.date
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # Create output path
        output_path = self.export_dir / f"merged_events_prices_{event_window_days}days.parquet"
        
        # Export to Parquet
        df.to_parquet(output_path, index=False)
        
        logger.info(f"Exported merged data with {len(df)} rows to {output_path}")
        return str(output_path)
    
    def export_filtered_data(self, event_window_days: int = 30) -> str:
        """
        Export a merged dataset with events and surrounding price data, excluding event_type='other'.
        
        Args:
            event_window_days: Number of days before/after each event to include price data
            
        Returns:
            Path to the exported file
        """
        conn = sqlite3.connect(self.db_path)
        
        # Query to join events with prices, excluding event_type='other', including a window around each event
        query = f"""
        SELECT
            e.id as event_id,
            e.ticker,
            e.company_name,
            e.event_date,
            e.headline,
            e.event_type,
            e.direction,
            e.magnitude,
            p.date as price_date,
            p.open,
            p.high,
            p.low,
            p.close,
            p.adj_close,
            p.volume,
            julianday(p.date) - julianday(e.event_date) as days_from_event
        FROM events e
        LEFT JOIN prices p ON e.ticker = p.ticker
            AND julianday(p.date) BETWEEN julianday(e.event_date) - {event_window_days}
                                       AND julianday(e.event_date) + {event_window_days}
        WHERE e.event_type IS NOT NULL AND e.event_type != 'other'
        ORDER BY e.ticker, e.event_date, p.date
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # Create output path
        output_path = self.export_dir / f"merged_events_prices_filtered_{event_window_days}days.parquet"
        
        # Export to Parquet
        df.to_parquet(output_path, index=False)
        
        logger.info(f"Exported filtered merged data with {len(df)} rows to {output_path}")
        return str(output_path)

    def export_summary_statistics(self) -> str:
        """
        Export summary statistics about the data.
        
        Returns:
            Path to the exported file
        """
        conn = sqlite3.connect(self.db_path)
        
        # Get summary statistics
        summary_queries = {
            "event_counts_by_type": """
                SELECT event_type, COUNT(*) as count
                FROM events
                WHERE event_type IS NOT NULL
                GROUP BY event_type
                ORDER BY count DESC
            """,
            "event_counts_by_direction": """
                SELECT direction, COUNT(*) as count
                FROM events
                WHERE direction IS NOT NULL
                GROUP BY direction
            """,
            "event_counts_by_magnitude": """
                SELECT magnitude, COUNT(*) as count
                FROM events
                WHERE magnitude IS NOT NULL
                GROUP BY magnitude
            """,
            "events_by_date": """
                SELECT event_date, COUNT(*) as count
                FROM events
                GROUP BY event_date
                ORDER BY event_date
            """,
            "events_by_ticker": """
                SELECT ticker, company_name, COUNT(*) as count
                FROM events
                GROUP BY ticker, company_name
                ORDER BY count DESC
                LIMIT 50
            """,
            "price_data_coverage": """
                SELECT 
                    ticker,
                    MIN(date) as earliest_date,
                    MAX(date) as latest_date,
                    COUNT(*) as num_days
                FROM prices
                GROUP BY ticker
                ORDER BY num_days DESC
                LIMIT 50
            """
        }
        
        summary_dfs = {}
        for name, query in summary_queries.items():
            df = pd.read_sql_query(query, conn)
            summary_dfs[name] = df
        
        conn.close()
        
        # Combine all summaries into one Excel file or multiple CSVs
        output_path = self.export_dir / "data_summary.xlsx"
        
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            for name, df in summary_dfs.items():
                df.to_excel(writer, sheet_name=name[:31], index=False)  # Sheet names limited to 31 chars
        
        logger.info(f"Exported summary statistics to {output_path}")
        return str(output_path)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Export database tables to CSV/Parquet formats")
    parser.add_argument("--format", choices=["csv", "parquet", "all"], default="all", 
                       help="Export format(s) to generate")
    parser.add_argument("--tables", nargs="+", 
                       help="Specific tables to export (default: all tables)")
    parser.add_argument("--merge-window", type=int, default=30,
                       help="Window size (in days) for merged event-price export")
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    exporter = DataExporter()
    
    if args.tables:
        # Export specific tables
        for table in args.tables:
            if args.format in ["csv", "all"]:
                exporter.export_table_to_csv(table)
            if args.format in ["parquet", "all"]:
                exporter.export_table_to_parquet(table)
    else:
        # Export all tables
        if args.format == "all":
            results = exporter.export_all_tables()
            print(f"Exported all tables. Results: {len(results)} tables exported")
    
    # Always export merged data and summary
    merged_path = exporter.export_merged_data(event_window_days=args.merge_window)
    summary_path = exporter.export_summary_statistics()
    
    print(f"Merged data exported to: {merged_path}")
    print(f"Summary statistics exported to: {summary_path}")
    print("Export completed!")