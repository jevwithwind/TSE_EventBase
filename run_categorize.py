#!/usr/bin/env python3
"""
Keyword-based categorization script for TSE_EventBase project.
Updates the event_type column in the events table using SQL keyword matching.
This is the primary categorization method (no AI, no API calls required).
"""

import argparse
import logging
import sqlite3
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def categorize_events(db_path: str = "data/tse_eventbase.db"):
    """
    Updates the event_type column in the events table using SQL keyword matching.
    
    Args:
        db_path: Path to the SQLite database file
    """
    logger.info(f"Starting keyword-based categorization for database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Define the keyword-to-category mappings
    category_mappings = [
        ("earnings", ["%決算短信%"]),
        ("forecast_revision", ["%業績予想%"]),
        ("dividend", ["%配当%"]),
        ("buyback", ["%自己株式%"]),
        ("ma", ["%合併%", "%買収%"]),
        ("tender_offer", ["%公開買付%", "%TOB%", "%MBO%"]),
        ("ceo_change", ["%代表取締役%"]),
        ("executive_change", ["%役員の異動%", "%役員人事%"]),
        ("stock_split", ["%株式分割%"]),
        ("large_holding", ["%大量保有%", "%主要株主の異動%"]),
        ("third_party_allotment", ["%第三者割当%"]),
        ("borrowing", ["%資金の借入%"]),
        ("capital_change", ["%資本%"])
    ]
    
    # Update each category separately
    for category, keywords in category_mappings:
        for keyword in keywords:
            query = f"""
            UPDATE events 
            SET event_type = ?
            WHERE event_type IS NULL 
            AND (headline LIKE ? OR headline_en LIKE ?)
            """
            cursor.execute(query, (category, keyword, keyword))
            rows_affected = cursor.rowcount
            if rows_affected > 0:
                logger.info(f"Updated {rows_affected} events to category: {category}")
    
    # Set remaining uncategorized events to 'other'
    cursor.execute("""
        UPDATE events 
        SET event_type = 'other'
        WHERE event_type IS NULL
    """)
    other_count = cursor.rowcount
    if other_count > 0:
        logger.info(f"Set {other_count} remaining events to category: other")
    
    conn.commit()
    conn.close()
    
    logger.info("Keyword-based categorization completed.")

def print_category_summary(db_path: str = "data/tse_eventbase.db"):
    """
    Prints a summary table of counts per category.
    
    Args:
        db_path: Path to the SQLite database file
    """
    logger.info("Generating category summary...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT event_type, COUNT(*) as count
        FROM events
        GROUP BY event_type
        ORDER BY count DESC
    """)
    
    results = cursor.fetchall()
    conn.close()
    
    print("\nCategory Summary:")
    print("-" * 30)
    print(f"{'Category':<20} {'Count':<10}")
    print("-" * 30)
    
    total = 0
    for category, count in results:
        print(f"{category:<20} {count:<10}")
        total += count
    
    print("-" * 30)
    print(f"{'TOTAL':<20} {total:<10}")
    print()

def main():
    parser = argparse.ArgumentParser(description="Keyword-based categorization of events")
    parser.add_argument("--db-path", default="data/tse_eventbase.db", 
                       help="Path to the SQLite database file")
    args = parser.parse_args()
    
    logger.info("Starting keyword-based categorization process...")
    
    categorize_events(db_path=args.db_path)
    print_category_summary(db_path=args.db_path)
    
    logger.info("Categorization process completed successfully!")

if __name__ == "__main__":
    exit(main())