#!/usr/bin/env python3
"""
Script to fetch and store all Kalshi markets in the database.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.markets_etl import MarketsETL
from config import setup_logging

def main():
    """Fetch and store all markets."""
    setup_logging()
    
    print("🔄 Starting markets ETL job...")
    
    # Create ETL instance
    etl = MarketsETL()
    
    # Fetch all markets
    print("📊 Fetching all markets from Kalshi API...")
    result = etl.fetch_and_store_all_markets(limit=1000)
    
    if result['success']:
        print(f"✅ Successfully fetched and stored {result['markets_stored']} out of {result['markets_fetched']} markets")
    else:
        print(f"❌ Error: {result['error']}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
