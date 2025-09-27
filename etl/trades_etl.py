"""
ETL job for fetching and storing trades data from Kalshi API.
"""
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, setup_logging
from kalshi.client import KalshiAPIClient
from database.models import TradesDatabase

logger = logging.getLogger(__name__)

class TradesETL:
    """ETL job for processing trades data."""
    
    def __init__(self, config: Config = None):
        """Initialize ETL job."""
        self.config = config or Config()
        self.kalshi_client = KalshiAPIClient(self.config)
        self.db = TradesDatabase()
        
    def run_etl_job(self, target_date: str = None) -> Dict[str, Any]:
        """
        Run the ETL job to fetch and store trades data for a specific calendar day.
        Includes safety checks to prevent duplicate data and ensure data integrity.
        
        Args:
            target_date: Date to fetch trades for (YYYY-MM-DD format). 
                        If None, fetches previous calendar day with safety checks.
            
        Returns:
            Dictionary with job results
        """
        try:
            # Determine target date
            if target_date is None:
                # Get previous calendar day in Eastern Time
                eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
                now_eastern = datetime.now(eastern_tz)
                previous_day = now_eastern - timedelta(days=1)
                target_date = previous_day.strftime('%Y-%m-%d')
            
            logger.info(f"Starting ETL job for calendar day: {target_date}")
            
            # Safety checks for automated runs (only when target_date is None)
            if target_date is None:
                safety_check_result = self._perform_safety_checks(target_date)
                if not safety_check_result['safe_to_proceed']:
                    logger.warning(f"Safety checks failed: {safety_check_result['reason']}")
                    return {
                        'success': False,
                        'error': f"Safety check failed: {safety_check_result['reason']}",
                        'target_date': target_date,
                        'trades_fetched': 0,
                        'trades_inserted': 0
                    }
                logger.info("Safety checks passed - proceeding with ETL job")
            
            # Calculate time range for the entire calendar day (Eastern Time)
            eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
            start_of_day = datetime.strptime(target_date, '%Y-%m-%d').replace(tzinfo=eastern_tz)
            end_of_day = start_of_day + timedelta(days=1)
            
            # Convert to UTC for API calls (Kalshi API requires UTC timestamps)
            start_time_utc = start_of_day.astimezone(timezone.utc)
            end_time_utc = end_of_day.astimezone(timezone.utc)
            
            min_ts = int(start_time_utc.timestamp())
            max_ts = int(end_time_utc.timestamp())
            
            logger.info(f"Fetching trades from {start_time_utc} to {end_time_utc} (UTC for API)")
            logger.info(f"Eastern Time range: {start_of_day} to {end_of_day}")
            
            # Fetch ALL trades for the day (no max limit)
            trades = self.kalshi_client.get_trades(
                limit=1000,
                min_ts=min_ts,
                max_ts=max_ts,
                max_trades=1000000  # Very high limit to get all trades
            )
            
            logger.info(f"Fetched {len(trades)} trades from API for {target_date}")
            
            # Store in database
            inserted_count = self.db.insert_trades(trades)
            
            # Update aggregations
            self._update_aggregations(trades)
            
            # Run data retention cleanup if enabled
            retention_cleanup = None
            if self.config.ENABLE_AUTOMATIC_CLEANUP:
                retention_cleanup = self.db.run_data_retention_cleanup(
                    trades_retention_days=self.config.TRADES_RETENTION_DAYS,
                    aggregations_retention_days=self.config.AGGREGATIONS_RETENTION_DAYS
                )
                logger.info(f"Data retention cleanup: {retention_cleanup['trades_cleanup']['trades_deleted']} trades, {retention_cleanup['aggregations_cleanup']['daily_deleted']} daily + {retention_cleanup['aggregations_cleanup']['hourly_deleted']} hourly aggregations deleted")
            
            # Get database stats
            db_stats = self.db.get_database_stats()
            
            result = {
                'success': True,
                'target_date': target_date,
                'trades_fetched': len(trades),
                'trades_inserted': inserted_count,
                'time_range': {
                    'start': start_time_utc.isoformat(),
                    'end': end_time_utc.isoformat(),
                    'eastern_start': start_of_day.isoformat(),
                    'eastern_end': end_of_day.isoformat()
                },
                'database_stats': db_stats,
                'retention_cleanup': retention_cleanup
            }
            
            logger.info(f"ETL job completed successfully: {result}")
            return result
            
        except Exception as e:
            logger.error(f"ETL job failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'target_date': target_date,
                'trades_fetched': 0,
                'trades_inserted': 0
            }
    
    def _perform_safety_checks(self, target_date: str) -> Dict[str, Any]:
        """
        Perform safety checks to ensure it's safe to run the ETL job.
        
        Args:
            target_date: The date we're planning to fetch data for
            
        Returns:
            Dictionary with 'safe_to_proceed' boolean and 'reason' string
        """
        try:
            logger.info("Performing safety checks...")
            
            # Check 1: Ensure we don't already have data for this date
            existing_data = self.db.get_daily_aggregations(days=1)
            if existing_data:
                latest_date = max(agg['date'] for agg in existing_data)
                if latest_date >= target_date:
                    return {
                        'safe_to_proceed': False,
                        'reason': f"Data already exists for {target_date} (latest: {latest_date})"
                    }
            
            # Check 1.5: Log current database status
            db_stats = self.db.get_database_stats()
            logger.info(f"Database status: {db_stats['total_trades']:,} trades, {db_stats['database_size_mb']} MB")
            
            # Check 2: Ensure we have data for the previous day (data continuity)
            eastern_tz = timezone(timedelta(hours=-5))
            target_dt = datetime.strptime(target_date, '%Y-%m-%d')
            previous_day = (target_dt - timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Check if we have data for the previous day
            all_daily_data = self.db.get_daily_aggregations(days=7)  # Get last 7 days
            previous_day_data = [agg for agg in all_daily_data if agg['date'] == previous_day]
            
            if not previous_day_data:
                return {
                    'safe_to_proceed': False,
                    'reason': f"No data found for previous day {previous_day} - data continuity check failed"
                }
            
            # Check 3: Ensure the previous day's data looks reasonable
            prev_day_agg = previous_day_data[0]
            if prev_day_agg['total_trades'] < 1000:  # Minimum expected trades per day
                return {
                    'safe_to_proceed': False,
                    'reason': f"Previous day {previous_day} has suspiciously low trade count: {prev_day_agg['total_trades']}"
                }
            
            # Check 4: Ensure we're not running too early (should be after 2 AM ET)
            now_eastern = datetime.now(eastern_tz)
            if now_eastern.hour < 2:
                return {
                    'safe_to_proceed': False,
                    'reason': f"Too early to run ETL job (current time: {now_eastern.strftime('%H:%M')} ET, minimum: 02:00 ET)"
                }
            
            # Check 5: Ensure we're not running too late (should be before 6 AM ET)
            if now_eastern.hour > 6:
                return {
                    'safe_to_proceed': False,
                    'reason': f"Too late to run ETL job (current time: {now_eastern.strftime('%H:%M')} ET, maximum: 06:00 ET)"
                }
            
            # Check 6: Verify we have reasonable data coverage
            all_daily_data = self.db.get_daily_aggregations(days=7)
            if len(all_daily_data) < 3:
                return {
                    'safe_to_proceed': False,
                    'reason': f"Insufficient historical data (only {len(all_daily_data)} days available)"
                }
            
            logger.info("All safety checks passed")
            return {
                'safe_to_proceed': True,
                'reason': "All safety checks passed"
            }
            
        except Exception as e:
            logger.error(f"Error during safety checks: {e}")
            return {
                'safe_to_proceed': False,
                'reason': f"Safety check error: {str(e)}"
            }
    
    def _update_aggregations(self, trades: List[Dict[str, Any]]):
        """Update daily, hourly, and category aggregations from trades data."""
        if not trades:
            return
            
        logger.info("Updating aggregations...")
        
        # Get events for categorization
        events = self.db.get_all_events()
        event_categories = {}
        for event in events:
            if event.get('category'):
                event_categories[event['event_ticker']] = event['category']
        
        # Group trades by date, hour, and category
        daily_data = {}
        hourly_data = {}
        category_data = {}
        
        for trade in trades:
            try:
                # Parse timestamp
                trade_time = datetime.fromisoformat(trade['created_time'].replace('Z', '+00:00'))
                
                # Convert to Eastern Time for date grouping
                eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
                trade_time_eastern = trade_time.astimezone(eastern_tz)
                date_key = trade_time_eastern.strftime('%Y-%m-%d')
                hour = trade_time_eastern.hour
                
                # Calculate volume
                count = trade.get('count', 0)
                yes_price = trade.get('yes_price', 0) / 100.0
                no_price = trade.get('no_price', 0) / 100.0
                volume = count * (yes_price + no_price) / 2
                
                # Get category for this trade
                market_ticker = trade.get('ticker', '')
                category = 'Unknown'
                if '-' in market_ticker:
                    parts = market_ticker.split('-')
                    event_ticker = '-'.join(parts[:-1])
                    category = event_categories.get(event_ticker, 'Unknown')
                else:
                    category = event_categories.get(market_ticker, 'Unknown')
                
                # Daily aggregations
                if date_key not in daily_data:
                    daily_data[date_key] = {'total_volume': 0, 'total_trades': 0}
                daily_data[date_key]['total_volume'] += volume
                daily_data[date_key]['total_trades'] += 1
                
                # Hourly aggregations
                hour_key = (date_key, hour)
                if hour_key not in hourly_data:
                    hourly_data[hour_key] = {'total_volume': 0, 'total_trades': 0}
                hourly_data[hour_key]['total_volume'] += volume
                hourly_data[hour_key]['total_trades'] += 1
                
                # Category aggregations
                category_key = (date_key, category)
                if category_key not in category_data:
                    category_data[category_key] = {'total_volume': 0, 'total_trades': 0}
                category_data[category_key]['total_volume'] += volume
                category_data[category_key]['total_trades'] += 1
                
            except Exception as e:
                logger.warning(f"Failed to process trade for aggregations: {e}")
                continue
        
        # Update daily aggregations
        for date, data in daily_data.items():
            avg_volume_per_trade = data['total_volume'] / data['total_trades'] if data['total_trades'] > 0 else 0
            self.db.update_daily_aggregations(
                date, 
                data['total_volume'], 
                data['total_trades'], 
                avg_volume_per_trade
            )
        
        # Update hourly aggregations
        for (date, hour), data in hourly_data.items():
            avg_volume_per_trade = data['total_volume'] / data['total_trades'] if data['total_trades'] > 0 else 0
            self.db.update_hourly_aggregations(
                date, 
                hour, 
                data['total_volume'], 
                data['total_trades'], 
                avg_volume_per_trade
            )
        
        # Update category aggregations
        for (date, category), data in category_data.items():
            avg_volume_per_trade = data['total_volume'] / data['total_trades'] if data['total_trades'] > 0 else 0
            self.db.update_daily_category_aggregations(
                date, 
                category, 
                data['total_volume'], 
                data['total_trades'], 
                avg_volume_per_trade
            )
        
        logger.info(f"Updated {len(daily_data)} daily, {len(hourly_data)} hourly, and {len(category_data)} category aggregations")
    
    def run_backfill(self, days_back: int = 5) -> Dict[str, Any]:
        """
        Run a backfill of historical data for the last N calendar days.
        
        Args:
            days_back: How many calendar days back to fetch
            
        Returns:
            Dictionary with backfill results
        """
        logger.info(f"Starting backfill: {days_back} calendar days back")
        
        total_trades_fetched = 0
        total_trades_inserted = 0
        successful_runs = 0
        failed_runs = 0
        processed_dates = []
        
        # Calculate dates to process
        eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
        today_eastern = datetime.now(eastern_tz)
        
        for day_offset in range(1, days_back + 1):
            target_date = (today_eastern - timedelta(days=day_offset)).strftime('%Y-%m-%d')
            processed_dates.append(target_date)
            
            try:
                logger.info(f"Processing day {day_offset}/{days_back}: {target_date}")
                result = self.run_etl_job(target_date=target_date)
                
                if result['success']:
                    total_trades_fetched += result['trades_fetched']
                    total_trades_inserted += result['trades_inserted']
                    successful_runs += 1
                    logger.info(f"✅ {target_date}: {result['trades_fetched']} trades fetched, {result['trades_inserted']} inserted")
                else:
                    failed_runs += 1
                    logger.error(f"❌ {target_date}: {result['error']}")
                    
            except Exception as e:
                logger.error(f"Failed to process {target_date}: {e}")
                failed_runs += 1
                continue
        
        # Get final database stats
        db_stats = self.db.get_database_stats()
        
        return {
            'success': failed_runs == 0,
            'days_processed': len(processed_dates),
            'processed_dates': processed_dates,
            'total_trades_fetched': total_trades_fetched,
            'total_trades_inserted': total_trades_inserted,
            'successful_runs': successful_runs,
            'failed_runs': failed_runs,
            'database_stats': db_stats
        }

def main():
    """Main function to run ETL job with safety checks."""
    setup_logging()
    
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Initialize ETL
    etl = TradesETL()
    
    # Run ETL job for previous calendar day with safety checks
    result = etl.run_etl_job()
    
    if result['success']:
        logger.info("ETL job completed successfully")
        print(f"✅ ETL Job Success!")
        print(f"   Target date: {result['target_date']}")
        print(f"   Trades fetched: {result['trades_fetched']:,}")
        print(f"   Trades inserted: {result['trades_inserted']:,}")
        print(f"   Database size: {result['database_stats']['database_size_mb']} MB")
        if 'time_range' in result:
            print(f"   Time range: {result['time_range']['eastern_start']} to {result['time_range']['eastern_end']}")
        
        # Display retention cleanup info if available
        if result.get('retention_cleanup'):
            cleanup = result['retention_cleanup']
            trades_cleanup = cleanup['trades_cleanup']
            agg_cleanup = cleanup['aggregations_cleanup']
            print(f"   Data retention cleanup:")
            print(f"     Trades deleted: {trades_cleanup['trades_deleted']:,} (older than {trades_cleanup['retention_days']} days)")
            print(f"     Aggregations deleted: {agg_cleanup['daily_deleted']:,} daily + {agg_cleanup['hourly_deleted']:,} hourly (older than {agg_cleanup['retention_days']} days)")
    else:
        logger.error("ETL job failed")
        print(f"❌ ETL Job Failed: {result['error']}")
        
        # For safety check failures, don't exit with error code (this is expected behavior)
        if "Safety check failed" in result['error']:
            print("ℹ️  This is a safety check failure - the job will retry later")
            sys.exit(0)
        else:
            print("💥 This is an actual error - check logs for details")
            sys.exit(1)

if __name__ == "__main__":
    main()
