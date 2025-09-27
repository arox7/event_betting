#!/usr/bin/env python3
"""
Test script for the ETL pipeline.
"""
import os
import sys
import logging
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import setup_logging
from etl.trades_etl import TradesETL
from database.models import TradesDatabase

def test_etl_pipeline():
    """Test the ETL pipeline end-to-end."""
    setup_logging()
    logger = logging.getLogger(__name__)
    
    print("🧪 Testing ETL Pipeline")
    print("=" * 50)
    
    # Create data directory
    os.makedirs('data', exist_ok=True)
    
    # Initialize ETL
    etl = TradesETL()
    
    # Test 1: Run ETL job
    print("\n1. Running ETL job...")
    result = etl.run_etl_job()
    
    if result['success']:
        print(f"   ✅ ETL job successful")
        print(f"   📅 Target date: {result['target_date']}")
        print(f"   📊 Trades fetched: {result['trades_fetched']:,}")
        print(f"   💾 Trades inserted: {result['trades_inserted']:,}")
        print(f"   🗄️  Database size: {result['database_stats']['database_size_mb']} MB")
    else:
        print(f"   ❌ ETL job failed: {result['error']}")
        return False
    
    # Test 2: Check database
    print("\n2. Checking database...")
    db = TradesDatabase()
    stats = db.get_database_stats()
    
    print(f"   📈 Total trades: {stats['total_trades']:,}")
    print(f"   🗄️  Database size: {stats['database_size_mb']} MB")
    print(f"   ⏰ Latest trade: {datetime.fromtimestamp(stats['latest_trade_timestamp'])}")
    
    # Test 3: Test analytics queries
    print("\n3. Testing analytics queries...")
    
    # Get trades for analytics
    trades = db.get_trades_for_analytics(days=7)
    print(f"   📊 Trades for analytics (7 days): {len(trades)}")
    
    # Get daily aggregations
    daily_agg = db.get_daily_aggregations(days=7)
    print(f"   📅 Daily aggregations: {len(daily_agg)}")
    
    # Get hourly aggregations
    hourly_agg = db.get_hourly_aggregations(days=7)
    print(f"   ⏰ Hourly aggregations: {len(hourly_agg)}")
    
    # Test 4: Performance test
    print("\n4. Performance test...")
    import time
    
    start_time = time.time()
    trades = db.get_trades_for_analytics(days=14)
    end_time = time.time()
    
    print(f"   ⚡ Query time: {(end_time - start_time)*1000:.2f}ms")
    print(f"   📊 Records returned: {len(trades)}")
    
    print("\n✅ ETL Pipeline Test Complete!")
    return True

if __name__ == "__main__":
    success = test_etl_pipeline()
    sys.exit(0 if success else 1)
