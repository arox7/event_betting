#!/usr/bin/env python3
"""
Backfill script to fetch historical trades data for the last 5 days.
"""
import os
import sys
import logging
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import setup_logging
from etl.trades_etl import TradesETL

def main():
    """Run backfill for the last 5 days."""
    setup_logging()
    logger = logging.getLogger(__name__)
    
    print("🔄 Starting 5-Day Backfill")
    print("=" * 50)
    
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Initialize ETL
    etl = TradesETL()
    
    # Run backfill for last 5 days
    result = etl.run_backfill(days_back=5)
    
    if result['success']:
        print(f"\n✅ Backfill Completed Successfully!")
        print(f"   Days processed: {result['days_processed']}")
        print(f"   Dates: {', '.join(result['processed_dates'])}")
        print(f"   Total trades fetched: {result['total_trades_fetched']:,}")
        print(f"   Total trades inserted: {result['total_trades_inserted']:,}")
        print(f"   Database size: {result['database_stats']['database_size_mb']} MB")
        print(f"   Successful runs: {result['successful_runs']}")
        print(f"   Failed runs: {result['failed_runs']}")
    else:
        print(f"\n❌ Backfill Failed!")
        print(f"   Successful runs: {result['successful_runs']}")
        print(f"   Failed runs: {result['failed_runs']}")
        print(f"   Total trades fetched: {result['total_trades_fetched']:,}")
        print(f"   Total trades inserted: {result['total_trades_inserted']:,}")
        sys.exit(1)

if __name__ == "__main__":
    main()
