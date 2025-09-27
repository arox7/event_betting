#!/usr/bin/env python3
"""
Migration script to convert all existing UTC timestamps to ET timestamps.
This script will:
1. Backup existing data
2. Clear all tables
3. Re-process all trades with ET timestamps
4. Re-generate all aggregations
"""

import os
import sys
import shutil
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, setup_logging
from database.models import TradesDatabase
from kalshi import KalshiAPIClient

def backup_database():
    """Create a backup of the current database."""
    db_path = "data/trades.db"
    backup_path = f"data/trades_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
        print(f"✅ Database backed up to: {backup_path}")
        return backup_path
    else:
        print("⚠️  No existing database found")
        return None

def clear_all_tables():
    """Clear all data from all tables."""
    db = TradesDatabase()
    
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        
        # Clear all tables
        tables = ['trades', 'daily_aggregations', 'hourly_aggregations', 'daily_category_aggregations']
        
        for table in tables:
            cursor.execute(f"DELETE FROM {table}")
            print(f"✅ Cleared {table} table")
        
        conn.commit()
        print("✅ All tables cleared")

def reprocess_all_trades():
    """Re-process all trades with ET timestamps."""
    config = Config()
    kalshi_client = KalshiAPIClient(config)
    db = TradesDatabase()
    
    # Get all available dates from the backup or from a reasonable range
    # For now, let's process the last 30 days
    eastern_tz = timezone(timedelta(hours=-5))
    end_date = datetime.now(eastern_tz)
    start_date = end_date - timedelta(days=30)
    
    print(f"🔄 Re-processing trades from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    
    current_date = start_date
    total_trades = 0
    
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        print(f"📅 Processing {date_str}...")
        
        try:
            # Fetch trades for this date
            start_of_day = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            
            # Convert to UTC for API calls
            start_time_utc = start_of_day.astimezone(timezone.utc)
            end_time_utc = end_of_day.astimezone(timezone.utc)
            
            min_ts = int(start_time_utc.timestamp())
            max_ts = int(end_time_utc.timestamp())
            
            trades = kalshi_client.get_trades(
                limit=1000,
                min_ts=min_ts,
                max_ts=max_ts,
                max_trades=1000000
            )
            
            if trades:
                # Insert trades with ET timestamps
                inserted_count = db.insert_trades(trades)
                total_trades += inserted_count
                print(f"  ✅ Inserted {inserted_count} trades")
            else:
                print(f"  ℹ️  No trades found for {date_str}")
                
        except Exception as e:
            print(f"  ❌ Error processing {date_str}: {e}")
        
        current_date += timedelta(days=1)
    
    print(f"✅ Total trades re-processed: {total_trades}")

def regenerate_aggregations():
    """Re-generate all aggregations from the re-processed trades."""
    from etl.trades_etl import TradesETL
    
    config = Config()
    etl = TradesETL(config)
    
    print("🔄 Re-generating aggregations...")
    
    # Get all dates that have trades
    db = TradesDatabase()
    eastern_tz = timezone(timedelta(hours=-5))
    
    # Get all unique dates from trades
    with sqlite3.connect(db.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT DATE(datetime(created_time_ts, 'unixepoch')) as trade_date
            FROM trades 
            ORDER BY trade_date
        """)
        dates = [row[0] for row in cursor.fetchall()]
    
    print(f"📅 Found trades for {len(dates)} dates: {dates}")
    
    # Process each date
    for date_str in dates:
        try:
            print(f"🔄 Processing aggregations for {date_str}...")
            result = etl.process_date(date_str)
            print(f"  ✅ Processed {date_str}: {result.get('trades_inserted', 0)} trades")
        except Exception as e:
            print(f"  ❌ Error processing {date_str}: {e}")

def main():
    """Main migration function."""
    setup_logging(level=logging.INFO, include_filename=True)
    logger = logging.getLogger(__name__)
    
    print("🚀 Starting migration to ET timestamps...")
    print("=" * 50)
    
    try:
        # Step 1: Backup existing database
        backup_path = backup_database()
        
        # Step 2: Clear all tables
        clear_all_tables()
        
        # Step 3: Re-process all trades with ET timestamps
        reprocess_all_trades()
        
        # Step 4: Re-generate all aggregations
        regenerate_aggregations()
        
        print("=" * 50)
        print("✅ Migration completed successfully!")
        print(f"📁 Backup available at: {backup_path}")
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        print(f"❌ Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
