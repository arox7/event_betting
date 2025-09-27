#!/usr/bin/env python3
"""
Cron script for Events ETL - runs daily to refresh events data.
"""

import sys
import os
import logging
import time
from datetime import datetime, timedelta, timezone

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.events_etl import EventsETL
from config import setup_logging

def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    
    etl = EventsETL()
    
    eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
    now_eastern = datetime.now(eastern_tz)
    
    logger.info(f"Starting events ETL job at {now_eastern.strftime('%Y-%m-%d %H:%M:%S')} EST")
    
    start_time = time.time()
    results = etl.fetch_and_store_all_events()  # Fetch all events without limit
    end_time = time.time()
    duration = round(end_time - start_time, 2)
    
    if results['success']:
        if results.get('skipped', False):
            logger.info("Events ETL skipped - no new events expected")
            logger.info(f"  Duration: {duration} seconds")
        else:
            logger.info("Events ETL completed successfully:")
            logger.info(f"  Events fetched: {results['events_fetched']:,}")
            logger.info(f"  Events stored: {results['events_stored']:,}")
            logger.info(f"  Pages fetched: {results.get('pages_fetched', 0):,}")
            logger.info(f"  Duration: {duration} seconds")
    else:
        logger.error(f"Events ETL failed: {results['error']}")
        # Exit with a non-zero code to indicate failure in cron
        exit(1)
    
    logger.info(f"ETL job completed at {datetime.now(eastern_tz).strftime('%Y-%m-%d %H:%M:%S')} EST")
    logger.info(f"Total duration: {duration} seconds")

if __name__ == "__main__":
    main()
