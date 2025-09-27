#!/usr/bin/env python3
"""
Markets ETL Cron Job - Daily refresh of Kalshi markets data.
Runs at 2AM Eastern Time to fetch and store all available markets.
"""
import sys
import os
import logging
from datetime import datetime, timezone, timedelta

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.markets_etl import MarketsETL
from config import setup_logging

def main():
    """Main function for markets ETL cron job."""
    # Set up logging with timestamp
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Get Eastern Time
    eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
    now_eastern = datetime.now(eastern_tz)
    
    logger.info(f"Starting markets ETL job at {now_eastern.strftime('%Y-%m-%d %H:%M:%S')} EST")
    
    try:
        # Create ETL instance
        etl = MarketsETL()
        
        # Fetch all markets
        logger.info("Fetching all markets from Kalshi API...")
        result = etl.fetch_and_store_all_markets()
        
        if result['success']:
            logger.info(f"Markets ETL completed successfully:")
            logger.info(f"  Markets fetched: {result['markets_fetched']:,}")
            logger.info(f"  Markets stored: {result['markets_stored']:,}")
            
            # Log completion time
            end_time = datetime.now(eastern_tz)
            duration = end_time - now_eastern
            logger.info(f"ETL job completed at {end_time.strftime('%Y-%m-%d %H:%M:%S')} EST")
            logger.info(f"Total duration: {duration.total_seconds():.1f} seconds")
            
            return 0
        else:
            logger.error(f"Markets ETL failed: {result['error']}")
            return 1
            
    except Exception as e:
        logger.error(f"Markets ETL job failed with exception: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
