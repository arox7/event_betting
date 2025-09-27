"""
Database models for trades data storage.
"""
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class TradesDatabase:
    """SQLite database for storing trades data with ETL architecture."""
    
    def __init__(self, db_path: str = "data/trades.db"):
        """Initialize database connection and create tables."""
        self.db_path = db_path
        self._create_tables()
    
    def _create_tables(self):
        """Create database tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT UNIQUE NOT NULL,
                    market_ticker TEXT NOT NULL,
                    created_time TEXT NOT NULL,
                    created_time_ts INTEGER NOT NULL,  -- ET timestamp
                    count INTEGER NOT NULL,
                    yes_price INTEGER NOT NULL,
                    no_price INTEGER NOT NULL,
                    volume REAL NOT NULL,
                    side TEXT NOT NULL,
                    raw_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create daily aggregations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_aggregations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE NOT NULL,
                    total_volume REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    avg_volume_per_trade REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create hourly aggregations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hourly_aggregations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    total_volume REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    avg_volume_per_trade REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, hour)
                )
            """)
            
            # Create daily category aggregations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_category_aggregations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    category TEXT NOT NULL,
                    total_volume REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    avg_volume_per_trade REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, category)
                )
            """)
            
            # Create events table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_ticker TEXT UNIQUE NOT NULL,
                    series_ticker TEXT,
                    sub_title TEXT,
                    title TEXT,
                    collateral_return_type TEXT,
                    mutually_exclusive BOOLEAN,
                    category TEXT,
                    price_level_structure TEXT,
                    available_on_brokers BOOLEAN,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for better performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_time_ts ON trades(created_time_ts)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_market_ticker ON trades(market_ticker)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_agg_date ON daily_aggregations(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_hourly_agg_date_hour ON hourly_aggregations(date, hour)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_category_agg_date ON daily_category_aggregations(date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_category_agg_category ON daily_category_aggregations(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_event_ticker ON events(event_ticker)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_series_ticker ON events(series_ticker)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_category ON events(category)")
            
            conn.commit()
            logger.info("Database tables created successfully")
    
    def insert_trades(self, trades: List[Dict[str, Any]]) -> int:
        """Insert trades into database, skipping duplicates."""
        if not trades:
            return 0
            
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            inserted_count = 0
            
            for trade in trades:
                try:
                    # Parse timestamp and convert to Eastern Time
                    created_time = trade['created_time']
                    eastern_tz = timezone(timedelta(hours=-5))  # EST (UTC-5)
                    created_time_utc = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                    created_time_et = created_time_utc.astimezone(eastern_tz)
                    created_time_ts = int(created_time_et.timestamp())
                    
                    # Calculate volume
                    count = trade.get('count', 0)
                    yes_price = trade.get('yes_price', 0) / 100.0
                    no_price = trade.get('no_price', 0) / 100.0
                    volume = count * (yes_price + no_price) / 2
                    
                    cursor.execute("""
                        INSERT OR IGNORE INTO trades 
                        (trade_id, market_ticker, created_time, created_time_ts, count, 
                         yes_price, no_price, volume, side, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        trade.get('trade_id', ''),
                        trade.get('ticker', ''),
                        created_time,
                        created_time_ts,
                        count,
                        yes_price,
                        no_price,
                        volume,
                        trade.get('taker_side', ''),
                        json.dumps(trade)
                    ))
                    
                    if cursor.rowcount > 0:
                        inserted_count += 1
                        
                except Exception as e:
                    logger.warning(f"Failed to insert trade {trade.get('id', 'unknown')}: {e}")
                    continue
            
            conn.commit()
            logger.info(f"Inserted {inserted_count} new trades")
            return inserted_count
    
    def get_existing_event_tickers(self) -> set:
        """Get all existing event tickers from database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT event_ticker FROM events")
            return {row[0] for row in cursor.fetchall()}
    
    def insert_events_batch(self, events_data: List[Dict[str, Any]]) -> int:
        """Insert multiple events in batch, skipping duplicates."""
        if not events_data:
            return 0
            
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get existing tickers to avoid duplicates
            existing_tickers = self.get_existing_event_tickers()
            
            # Filter out events that already exist
            new_events = [event for event in events_data 
                         if event.get('event_ticker', '') not in existing_tickers]
            
            if not new_events:
                logger.info("No new events to insert")
                return 0
            
            # Batch insert new events
            cursor.executemany("""
                INSERT INTO events 
                (event_ticker, series_ticker, sub_title, title, collateral_return_type, 
                 mutually_exclusive, category, price_level_structure, available_on_brokers)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    event.get('event_ticker', ''),
                    event.get('series_ticker', ''),
                    event.get('sub_title', ''),
                    event.get('title', ''),
                    event.get('collateral_return_type', ''),
                    event.get('mutually_exclusive', False),
                    event.get('category', ''),
                    event.get('price_level_structure', ''),
                    event.get('available_on_brokers', False)
                ) for event in new_events
            ])
            
            conn.commit()
            logger.info(f"Batch inserted {len(new_events)} new events (skipped {len(events_data) - len(new_events)} duplicates)")
            return len(new_events)
    
    def get_event_by_ticker(self, event_ticker: str) -> Optional[Dict[str, Any]]:
        """Get event data by ticker."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT event_ticker, series_ticker, sub_title, title, collateral_return_type,
                       mutually_exclusive, category, price_level_structure, available_on_brokers,
                       created_at, updated_at
                FROM events 
                WHERE event_ticker = ?
            """, (event_ticker,))
            
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_all_events(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all events, optionally filtered by category."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if category:
                cursor.execute("""
                    SELECT event_ticker, series_ticker, sub_title, title, collateral_return_type,
                           mutually_exclusive, category, price_level_structure, available_on_brokers,
                           created_at, updated_at
                    FROM events 
                    WHERE category = ?
                    ORDER BY created_at DESC
                """, (category,))
            else:
                cursor.execute("""
                    SELECT event_ticker, series_ticker, sub_title, title, collateral_return_type,
                           mutually_exclusive, category, price_level_structure, available_on_brokers,
                           created_at, updated_at
                    FROM events 
                    ORDER BY created_at DESC
                """)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_trades_for_analytics(self, days: int = 14) -> List[Dict[str, Any]]:
        """Get trades data for analytics (last N days)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate cutoff timestamp (timestamps are already in ET)
            cutoff_ts = int((datetime.now(timezone(timedelta(hours=-5))).timestamp() - (days * 24 * 3600)))
            
            cursor.execute("""
                SELECT trade_id, market_ticker, created_time, created_time_ts, 
                       count, yes_price, no_price, volume, side, raw_data
                FROM trades 
                WHERE created_time_ts >= ?
                ORDER BY created_time_ts DESC
            """, (cutoff_ts,))
            
            trades = []
            for row in cursor.fetchall():
                trade = {
                    'id': row['trade_id'],
                    'market_ticker': row['market_ticker'],
                    'created_time': row['created_time'],
                    'count': row['count'],
                    'yes_price': int(row['yes_price'] * 100),  # Convert back to cents
                    'no_price': int(row['no_price'] * 100),    # Convert back to cents
                    'side': row['side']
                }
                trades.append(trade)
            
            logger.info(f"Retrieved {len(trades)} trades for analytics")
            return trades
    
    def get_top_traded_markets(self, days: int = 7, limit: int = 5, kalshi_client=None) -> Dict[str, List[Dict[str, Any]]]:
        """Get top traded markets by volume and number of trades."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate cutoff timestamp (timestamps are already in ET)
            cutoff_ts = int((datetime.now(timezone(timedelta(hours=-5))).timestamp() - (days * 24 * 3600)))
            
            # Get top markets by volume
            cursor.execute("""
                SELECT market_ticker, 
                       SUM(volume) as total_volume,
                       COUNT(*) as total_trades,
                       AVG(volume) as avg_volume_per_trade
                FROM trades 
                WHERE created_time_ts >= ?
                GROUP BY market_ticker
                ORDER BY total_volume DESC
                LIMIT ?
            """, (cutoff_ts, limit))
            
            top_by_volume = []
            for row in cursor.fetchall():
                top_by_volume.append({
                    'market_ticker': row['market_ticker'],
                    'total_volume': row['total_volume'],
                    'total_trades': row['total_trades'],
                    'avg_volume_per_trade': row['avg_volume_per_trade']
                })
            
            # Get top markets by number of trades
            cursor.execute("""
                SELECT market_ticker, 
                       SUM(volume) as total_volume,
                       COUNT(*) as total_trades,
                       AVG(volume) as avg_volume_per_trade
                FROM trades 
                WHERE created_time_ts >= ?
                GROUP BY market_ticker
                ORDER BY total_trades DESC
                LIMIT ?
            """, (cutoff_ts, limit))
            
            top_by_trades = []
            for row in cursor.fetchall():
                top_by_trades.append({
                    'market_ticker': row['market_ticker'],
                    'total_volume': row['total_volume'],
                    'total_trades': row['total_trades'],
                    'avg_volume_per_trade': row['avg_volume_per_trade']
                })
            
            # Fetch market titles if kalshi_client is provided
            if kalshi_client:
                # Get all unique tickers from both lists
                all_tickers = set()
                for market in top_by_volume + top_by_trades:
                    all_tickers.add(market['market_ticker'])
                
                # Fetch market data to get titles
                market_titles = {}
                try:
                    for ticker in all_tickers:
                        try:
                            market_data = kalshi_client.get_market_by_ticker(ticker)
                            if market_data and hasattr(market_data, 'title') and market_data.title:
                                market_titles[ticker] = market_data.title
                            else:
                                market_titles[ticker] = ticker  # Fallback to ticker
                        except Exception as e:
                            logger.warning(f"Failed to fetch title for market {ticker}: {e}")
                            market_titles[ticker] = ticker  # Fallback to ticker
                except Exception as e:
                    logger.warning(f"Failed to fetch market titles: {e}")
                    # If we can't fetch titles, use tickers as fallback
                    for ticker in all_tickers:
                        market_titles[ticker] = ticker
                
                # Add titles to the results
                for market in top_by_volume:
                    market['market_title'] = market_titles.get(market['market_ticker'], market['market_ticker'])
                
                for market in top_by_trades:
                    market['market_title'] = market_titles.get(market['market_ticker'], market['market_ticker'])
            
            return {
                'by_volume': top_by_volume,
                'by_trades': top_by_trades
            }
    
    def update_daily_aggregations(self, date: str, total_volume: float, total_trades: int, avg_volume_per_trade: float):
        """Update daily aggregations table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO daily_aggregations 
                (date, total_volume, total_trades, avg_volume_per_trade, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (date, total_volume, total_trades, avg_volume_per_trade))
            
            conn.commit()
    
    def update_hourly_aggregations(self, date: str, hour: int, total_volume: float, total_trades: int, avg_volume_per_trade: float):
        """Update hourly aggregations table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO hourly_aggregations 
                (date, hour, total_volume, total_trades, avg_volume_per_trade, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (date, hour, total_volume, total_trades, avg_volume_per_trade))
            
            conn.commit()
    
    def update_daily_category_aggregations(self, date: str, category: str, total_volume: float, total_trades: int, avg_volume_per_trade: float):
        """Update daily category aggregations table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO daily_category_aggregations 
                (date, category, total_volume, total_trades, avg_volume_per_trade, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (date, category, total_volume, total_trades, avg_volume_per_trade))
            
            conn.commit()
    
    def get_daily_aggregations(self, days: int = 14) -> List[Dict[str, Any]]:
        """Get daily aggregations for the last N days."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate the cutoff date (N days ago)
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            cursor.execute("""
                SELECT date, total_volume, total_trades, avg_volume_per_trade
                FROM daily_aggregations 
                WHERE date >= ?
                ORDER BY date ASC
            """, (cutoff_date,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_hourly_aggregations(self, days: int = 14) -> List[Dict[str, Any]]:
        """Get hourly aggregations for the last N days."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate the cutoff date (N days ago)
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            cursor.execute("""
                SELECT date, hour, total_volume, total_trades, avg_volume_per_trade
                FROM hourly_aggregations 
                WHERE date >= ?
                ORDER BY date ASC, hour ASC
            """, (cutoff_date,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_daily_category_aggregations(self, days: int = 14) -> List[Dict[str, Any]]:
        """Get daily category aggregations for the last N days."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Calculate the cutoff date (N days ago)
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            cursor.execute("""
                SELECT date, category, total_volume, total_trades, avg_volume_per_trade
                FROM daily_category_aggregations 
                WHERE date >= ?
                ORDER BY date ASC, total_volume DESC
            """, (cutoff_date,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get total trades count
            cursor.execute("SELECT COUNT(*) FROM trades")
            total_trades = cursor.fetchone()[0]
            
            # Get latest trade timestamp
            cursor.execute("SELECT MAX(created_time_ts) FROM trades")
            latest_trade_ts = cursor.fetchone()[0]
            
            # Get database size using file system
            import os
            db_size_bytes = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            
            return {
                'total_trades': total_trades,
                'latest_trade_timestamp': latest_trade_ts,
                'database_size_bytes': db_size_bytes,
                'database_size_mb': round(db_size_bytes / (1024 * 1024), 2)
            }
    
    def cleanup_trades_retention(self, retention_days: int = 30) -> Dict[str, Any]:
        """
        Clean up old trades data based on retention policy.
        
        Args:
            retention_days: Number of days to retain trades data (default: 30)
            
        Returns:
            Dictionary with cleanup statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Calculate cutoff timestamp (timestamps are already in ET)
            cutoff_ts = int((datetime.now(timezone(timedelta(hours=-5))).timestamp() - (retention_days * 24 * 3600)))
            
            # Get count of trades to be deleted
            cursor.execute("SELECT COUNT(*) FROM trades WHERE created_time_ts < ?", (cutoff_ts,))
            trades_to_delete = cursor.fetchone()[0]
            
            if trades_to_delete == 0:
                logger.info(f"No trades older than {retention_days} days found for cleanup")
                return {
                    'trades_deleted': 0,
                    'retention_days': retention_days,
                    'cutoff_timestamp': cutoff_ts
                }
            
            # Delete old trades
            cursor.execute("DELETE FROM trades WHERE created_time_ts < ?", (cutoff_ts,))
            deleted_count = cursor.rowcount
            
            conn.commit()
            
            logger.info(f"Deleted {deleted_count} trades older than {retention_days} days")
            
            return {
                'trades_deleted': deleted_count,
                'retention_days': retention_days,
                'cutoff_timestamp': cutoff_ts
            }
    
    def cleanup_aggregations_retention(self, retention_days: int = 800) -> Dict[str, Any]:
        """
        Clean up old aggregated data based on retention policy.
        
        Args:
            retention_days: Number of days to retain aggregated data (default: 800)
            
        Returns:
            Dictionary with cleanup statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Calculate cutoff date
            cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d')
            
            # Get count of daily aggregations to be deleted
            cursor.execute("SELECT COUNT(*) FROM daily_aggregations WHERE date < ?", (cutoff_date,))
            daily_to_delete = cursor.fetchone()[0]
            
            # Get count of hourly aggregations to be deleted
            cursor.execute("SELECT COUNT(*) FROM hourly_aggregations WHERE date < ?", (cutoff_date,))
            hourly_to_delete = cursor.fetchone()[0]
            
            if daily_to_delete == 0 and hourly_to_delete == 0:
                logger.info(f"No aggregations older than {retention_days} days found for cleanup")
                return {
                    'daily_deleted': 0,
                    'hourly_deleted': 0,
                    'retention_days': retention_days,
                    'cutoff_date': cutoff_date
                }
            
            # Delete old daily aggregations
            cursor.execute("DELETE FROM daily_aggregations WHERE date < ?", (cutoff_date,))
            daily_deleted = cursor.rowcount
            
            # Delete old hourly aggregations
            cursor.execute("DELETE FROM hourly_aggregations WHERE date < ?", (cutoff_date,))
            hourly_deleted = cursor.rowcount
            
            conn.commit()
            
            logger.info(f"Deleted {daily_deleted} daily and {hourly_deleted} hourly aggregations older than {retention_days} days")
            
            return {
                'daily_deleted': daily_deleted,
                'hourly_deleted': hourly_deleted,
                'retention_days': retention_days,
                'cutoff_date': cutoff_date
            }
    
    def run_data_retention_cleanup(self, trades_retention_days: int = 30, aggregations_retention_days: int = 800) -> Dict[str, Any]:
        """
        Run complete data retention cleanup for both trades and aggregations.
        
        Args:
            trades_retention_days: Days to retain trades data (default: 30)
            aggregations_retention_days: Days to retain aggregated data (default: 800)
            
        Returns:
            Dictionary with complete cleanup statistics
        """
        logger.info(f"Starting data retention cleanup: {trades_retention_days} days for trades, {aggregations_retention_days} days for aggregations")
        
        # Clean up trades
        trades_cleanup = self.cleanup_trades_retention(trades_retention_days)
        
        # Clean up aggregations
        aggregations_cleanup = self.cleanup_aggregations_retention(aggregations_retention_days)
        
        # Get final database stats
        final_stats = self.get_database_stats()
        
        result = {
            'success': True,
            'trades_cleanup': trades_cleanup,
            'aggregations_cleanup': aggregations_cleanup,
            'final_database_stats': final_stats
        }
        
        logger.info(f"Data retention cleanup completed: {trades_cleanup['trades_deleted']} trades deleted, {aggregations_cleanup['daily_deleted']} daily + {aggregations_cleanup['hourly_deleted']} hourly aggregations deleted")
        
        return result