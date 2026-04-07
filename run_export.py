#!/usr/bin/env python3
"""
Main entry point for exporting data.
This script runs the exporter to save database tables to CSV/Parquet formats.
"""

import argparse
import logging
from export.exporter import DataExporter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Export database tables to CSV/Parquet formats")
    parser.add_argument("--format", choices=["csv", "parquet", "all"], default="all",
                       help="Export format(s) to generate")
    parser.add_argument("--tables", nargs="+",
                       help="Specific tables to export (default: all tables)")
    parser.add_argument("--merge-window", type=int, default=30,
                       help="Window size (in days) for merged event-price export")
    parser.add_argument("--exclude-other", action="store_true",
                       help="Exclude events with event_type='other' from export (creates filtered datasets)")
    args = parser.parse_args()
    
    logger.info("Starting data export process...")
    
    exporter = DataExporter()
    
    if args.tables:
        # Export specific tables
        logger.info(f"Exporting specific tables: {args.tables}")
        for table in args.tables:
            if args.format in ["csv", "all"]:
                exporter.export_table_to_csv(table)
            if args.format in ["parquet", "all"]:
                exporter.export_table_to_parquet(table)
    else:
        # Export all tables
        logger.info("Exporting all tables")
        if args.format == "all":
            results = exporter.export_all_tables()
            logger.info(f"Exported all tables. Results: {len(results)} tables exported")
    
    # Always export merged data and summary
    logger.info(f"Exporting merged data with {args.merge_window}-day window around events")
    merged_path = exporter.export_merged_data(event_window_days=args.merge_window)
    
    # Export filtered data if requested
    if args.exclude_other:
        logger.info("Exporting filtered data (excluding event_type='other')")
        filtered_path = exporter.export_filtered_data(event_window_days=args.merge_window)
    
    logger.info("Exporting summary statistics")
    summary_path = exporter.export_summary_statistics()
    
    logger.info("Data export completed successfully!")
    print(f"\nData export completed!")
    print(f"Merged data exported to: {merged_path}")
    print(f"Summary statistics exported to: {summary_path}")
    if args.exclude_other:
        print(f"Filtered data (excluding 'other' events) exported to: {filtered_path}")
    
    return 0

if __name__ == "__main__":
    exit(main())