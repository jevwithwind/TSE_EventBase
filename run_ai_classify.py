#!/usr/bin/env python3
"""
[OPTIONAL] AI-powered event classification for TSE_EventBase project.
This script runs the event classifier on all unclassified events using any OpenAI-compatible API.
This is optional and not required for the core pipeline.
Supports any OpenAI-compatible API (OpenAI, Azure, Ollama, Qwen, etc.).
"""

import argparse
import logging
from config import OPENAI_API_KEY, OPENAI_BASE_URL, CLASSIFICATION_BATCH_SIZE
from classifier.event_classifier import EventClassifier
import sqlite3

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Classify events using Anthropic API")
    parser.add_argument("--batch-size", type=int, default=CLASSIFICATION_BATCH_SIZE,
                       help="Number of events to process in each batch")
    parser.add_argument("--filter", action="store_true",
                       help="Only classify events matching specific headline keywords")
    parser.add_argument("--exclude", action="store_true",
                       help="Exclude ETF daily disclosure events")
    parser.add_argument("--dry-run", action="store_true",
                       help="Print count of matching events without classifying")
    args = parser.parse_args()
    
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY environment variable is required for AI classification")
        return 1
    
    # Connect to database to get event counts
    conn = sqlite3.connect('data/tse_eventbase.db')
    cursor = conn.cursor()
    
    # Build query based on filters
    base_query = "SELECT COUNT(*) FROM events WHERE (event_type IS NULL OR event_type = '')"
    query_params = []
    
    if args.filter:
        # Keywords to include
        include_keywords = [
            '決算短信', '業績予想', '配当', '自己株式', '合併', '買収', '公開買付',
            '株式分割', '代表取締役', '大量保有', '第三者割当', '資本', 'MBO',
            'TOB', '主要株主の異動', '資金の借入', '役員の異動', '役員人事'
        ]
        include_conditions = " OR ".join(["headline LIKE ?" for _ in include_keywords])
        include_params = [f"%{keyword}%" for keyword in include_keywords]
        
        base_query += f" AND ({include_conditions})"
        query_params.extend(include_params)
    
    if args.exclude:
        # Keywords to exclude (ETF daily disclosures)
        exclude_keywords = [
            'ETFに関する日々の開示事項',
            'ＥＴＦに関する日々の開示事項',
            'ETFの収益分配'
        ]
        exclude_conditions = " OR ".join(["headline LIKE ?" for _ in exclude_keywords])
        exclude_params = [f"%{keyword}%" for keyword in exclude_keywords]
        
        base_query += f" AND NOT ({exclude_conditions})"
        query_params.extend(exclude_params)
    
    # Get count of matching events
    cursor.execute(base_query, query_params)
    matching_count = cursor.fetchone()[0]
    
    logger.info(f"Found {matching_count} events matching criteria")
    print(f"Found {matching_count} events matching criteria")
    
    if args.dry_run:
        logger.info("Dry run completed. No events were classified.")
        print("Dry run completed. No events were classified.")
        conn.close()
        return 0
    
    if matching_count == 0:
        logger.info("No events to classify.")
        print("No events to classify.")
        conn.close()
        return 0
    
    logger.info(f"Starting event classification with batch size: {args.batch_size}")
    
    # Define keywords for filtering
    if args.filter:
        include_keywords = [
            '決算短信', '業績予想', '配当', '自己株式', '合併', '買収', '公開買付',
            '株式分割', '代表取締役', '大量保有', '第三者割当', '資本', 'MBO',
            'TOB', '主要株主の異動', '資金の借入', '役員の異動', '役員人事'
        ]
    else:
        include_keywords = None
    
    if args.exclude:
        exclude_keywords = [
            'ETFに関する日々の開示事項',
            'ＥＴＦに関する日々の開示事項',
            'ETFの収益分配'
        ]
    else:
        exclude_keywords = None
    
    # Pass filter parameters to classifier
    classifier = EventClassifier(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL
    )
    
    # Modify the classifier to handle filtered classification
    classifier.classify_filtered_events(
        batch_size=args.batch_size,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords
    )
    
    conn.close()
    logger.info("Event classification completed successfully!")
    print("\nEvent classification completed successfully!")
    
    return 0

if __name__ == "__main__":
    exit(main())