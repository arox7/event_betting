#!/usr/bin/env python3
"""
Standalone script for running data retention cleanup.
This can be used to manually clean up old data or as part of maintenance routines.
"""
import os
import sys
import logging
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, setup_logging
from database.models import TradesDatabase

def main():
    """Main function to run data retention cleanup."""
    setup_logging()
    
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Initialize database and config
    config = Config()
    db = TradesDatabase()
    
    print("🧹 Starting Data Retention Cleanup")
    print(f"   Trades retention: {config.TRADES_RETENTION_DAYS} days")
    print(f"   Aggregations retention: {config.AGGREGATIONS_RETENTION_DAYS} days")
    print()
    
    # Get initial database stats
    initial_stats = db.get_database_stats()
    print(f"📊 Initial Database Stats:")
    print(f"   Total trades: {initial_stats['total_trades']:,}")
    print(f"   Database size: {initial_stats['database_size_mb']} MB")
    print()
    
    # Run cleanup
    try:
        result = db.run_data_retention_cleanup(
            trades_retention_days=config.TRADES_RETENTION_DAYS,
            aggregations_retention_days=config.AGGREGATIONS_RETENTION_DAYS
        )
        
        # Display results
        trades_cleanup = result['trades_cleanup']
        agg_cleanup = result['aggregations_cleanup']
        final_stats = result['final_database_stats']
        
        print("✅ Data Retention Cleanup Completed!")
        print()
        print("📋 Cleanup Summary:")
        print(f"   Trades deleted: {trades_cleanup['trades_deleted']:,} (older than {trades_cleanup['retention_days']} days)")
        print(f"   Daily aggregations deleted: {agg_cleanup['daily_deleted']:,} (older than {agg_cleanup['retention_days']} days)")
        print(f"   Hourly aggregations deleted: {agg_cleanup['hourly_deleted']:,} (older than {agg_cleanup['retention_days']} days)")
        print()
        print("📊 Final Database Stats:")
        print(f"   Total trades: {final_stats['total_trades']:,}")
        print(f"   Database size: {final_stats['database_size_mb']} MB")
        print()
        print("💾 Space Saved:")
        space_saved = initial_stats['database_size_mb'] - final_stats['database_size_mb']
        print(f"   Database size reduction: {space_saved:.2f} MB")
        
        if space_saved > 0:
            print(f"   Percentage reduction: {(space_saved / initial_stats['database_size_mb'] * 100):.1f}%")
        
        return 0
        
    except Exception as e:
        logging.error(f"Data retention cleanup failed: {e}")
        print(f"❌ Data Retention Cleanup Failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
