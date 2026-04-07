#!/usr/bin/env python3
"""
Initialize the SQLite database for TSE_EventBase project.
This script creates all required tables based on the schema.
"""

import sys
import os
# Add the project root directory to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import sqlite3
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def init_database(db_path):
    """
    Initialize the database with all required tables.
    
    Args:
        db_path (str): Path to the SQLite database file
    """
    # Ensure the data directory exists
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    
    # Connect to the database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Read the schema SQL file
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    
    # Execute the schema
    cursor.executescript(schema_sql)
    
    # Commit and close
    conn.commit()
    conn.close()
    
    logger.info(f"Database initialized successfully at {db_path}")

def reset_database(db_path):
    """
    Reset the database by deleting the existing file and recreating it.
    
    Args:
        db_path (str): Path to the SQLite database file
    """
    if os.path.exists(db_path):
        os.remove(db_path)
        logger.info(f"Existing database file removed: {db_path}")
    
    init_database(db_path)

if __name__ == "__main__":
    from config import DB_PATH
    
    import argparse
    parser = argparse.ArgumentParser(description="Initialize TSE_EventBase database")
    parser.add_argument("--reset", action="store_true", help="Reset the database (delete and recreate)")
    args = parser.parse_args()
    
    if args.reset:
        reset_database(DB_PATH)
    else:
        init_database(DB_PATH)
    
    logger.info("Database setup completed.")