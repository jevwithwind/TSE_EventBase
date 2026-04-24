#!/usr/bin/env python3
"""
Validation script for TSE_EventBase project.
Tests that all components are properly set up and can run.
"""

import os
import sys
import subprocess
import logging
from pathlib import Path
from config import DB_PATH, EDINET_API_KEY, OPENAI_API_KEY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_dependencies():
    """Check if required dependencies are installed."""
    logger.info("Checking dependencies...")
    
    required_packages = [
        'python-dotenv',
        'pandas',
        'sqlite3',
        'requests',
        'openai',
        'openbb',
        'edinet-tools'
    ]
    
    missing_packages = []
    for package in required_packages:
        try:
            if package == 'sqlite3':
                import sqlite3
            elif package == 'edinet-tools':
                import edinet_tools
            elif package == 'openbb':
                import openbb
            elif package == 'python-dotenv':
                import dotenv
            else:
                __import__(package.replace('-', '_'))
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        logger.error(f"Missing packages: {missing_packages}")
        logger.info("Install them with: pip install -r requirements.txt")
        return False
    
    logger.info("All dependencies are available.")
    return True

def check_environment_variables():
    """Check if required environment variables are set."""
    logger.info("Checking environment variables...")
    
    checks = [
        ("EDINET_API_KEY", EDINET_API_KEY),
        ("OPENAI_API_KEY", OPENAI_API_KEY)
    ]
    
    missing_vars = []
    for var_name, var_value in checks:
        if not var_value:
            missing_vars.append(var_name)
    
    if missing_vars:
        logger.warning(f"Missing environment variables: {missing_vars}")
        logger.info("Some functionality may be limited without these variables.")
        return False
    
    logger.info("Environment variables are set.")
    return True

def check_database():
    """Check if database exists and is accessible."""
    logger.info("Checking database...")
    
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check if tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        logger.info(f"Database is accessible. Found tables: {tables}")
        
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False

def test_config():
    """Test if config loads properly."""
    logger.info("Testing configuration...")
    
    try:
        from config import (
            SCRAPE_START_DATE, SCRAPE_END_DATE, DB_PATH, 
            EXPORT_DIR, EVENT_TYPES, DIRECTIONS, MAGNITUDES
        )
        
        logger.info(f"Config loaded successfully.")
        logger.info(f"Date range: {SCRAPE_START_DATE} to {SCRAPE_END_DATE}")
        logger.info(f"DB path: {DB_PATH}")
        logger.info(f"Export dir: {EXPORT_DIR}")
        logger.info(f"Event types: {len(EVENT_TYPES)} available")
        
        return True
    except Exception as e:
        logger.error(f"Config error: {e}")
        return False

def test_scripts():
    """Test if main scripts can be imported without errors."""
    logger.info("Testing main scripts...")
    
    scripts_to_test = [
        'run_scrape.py',
        'run_categorize.py',
        'run_ai_classify.py',
        'run_prices.py',
        'run_export.py',
        'db/init_db.py',
        'scrapers/tdnet_scraper.py',
        'scrapers/edinet_scraper.py',
        'classifier/event_classifier.py',
        'prices/price_fetcher.py',
        'export/exporter.py'
    ]
    
    failed_imports = []
    for script in scripts_to_test:
        try:
            # Import the module to test if it has syntax errors
            if script == 'db/init_db.py':
                from db import init_db
            elif script == 'scrapers/tdnet_scraper.py':
                from scrapers import tdnet_scraper
            elif script == 'scrapers/edinet_scraper.py':
                from scrapers import edinet_scraper
            elif script == 'classifier/event_classifier.py':
                from classifier import event_classifier
            elif script == 'prices/price_fetcher.py':
                from prices import price_fetcher
            elif script == 'export/exporter.py':
                from export import exporter
            elif script == 'run_scrape.py':
                import run_scrape
            elif script == 'run_classify.py':
                import run_classify
            elif script == 'run_prices.py':
                import run_prices
            elif script == 'run_export.py':
                import run_export
        except Exception as e:
            failed_imports.append((script, str(e)))
    
    if failed_imports:
        logger.error(f"Script import failures: {failed_imports}")
        return False
    
    logger.info("All scripts imported successfully.")
    return True

def main():
    """Run all validation checks."""
    logger.info("Starting TSE_EventBase validation...")
    
    all_checks = [
        ("Dependencies", check_dependencies),
        ("Environment Variables", check_environment_variables),
        ("Database", check_database),
        ("Configuration", test_config),
        ("Scripts", test_scripts)
    ]
    
    passed_checks = 0
    total_checks = len(all_checks)
    
    for check_name, check_func in all_checks:
        logger.info(f"\n--- Testing {check_name} ---")
        if check_func():
            passed_checks += 1
            logger.info(f"✓ {check_name} PASSED")
        else:
            logger.error(f"✗ {check_name} FAILED")
    
    logger.info(f"\n=== Validation Summary ===")
    logger.info(f"Passed: {passed_checks}/{total_checks}")
    
    if passed_checks == total_checks:
        logger.info("🎉 All validations passed! TSE_EventBase is ready to use.")
        print("\n✅ TSE_EventBase validation completed successfully!")
        print("All components are properly set up and ready to use.")
        return 0
    else:
        logger.error(f"❌ {total_checks - passed_checks} validation(s) failed.")
        print(f"\n❌ Validation failed. {total_checks - passed_checks} check(s) did not pass.")
        return 1

if __name__ == "__main__":
    exit(main())