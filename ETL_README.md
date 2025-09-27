# ETL Architecture for Kalshi Dashboard

This document describes the ETL (Extract, Transform, Load) architecture implemented for the Kalshi dashboard to improve performance and reliability.

## 🏗️ Architecture Overview

The ETL architecture separates data fetching from data presentation:

1. **Offline ETL Job**: Fetches data from Kalshi API and stores it in a local database
2. **Dashboard**: Reads from the local database instead of calling the API directly
3. **Pre-computed Aggregations**: Daily and hourly summaries for faster queries

## 📁 File Structure

```
├── database/
│   ├── __init__.py
│   └── models.py          # Database models and operations
├── etl/
│   ├── __init__.py
│   └── trades_etl.py      # ETL job implementation
├── scripts/
│   ├── run_etl.sh         # ETL job runner script
│   └── setup_cron.sh      # Cron job setup helper
├── data/
│   └── trades.db          # SQLite database (created automatically)
└── test_etl.py            # ETL pipeline test script
```

## 🚀 Quick Start

### 1. Run ETL Job Manually

```bash
# Run ETL job to fetch and store trades data
python etl/trades_etl.py

# Or use the shell script
bash scripts/run_etl.sh
```

**Safety Checks**: The ETL job includes comprehensive safety checks to prevent duplicate data and ensure data integrity:
- **Duplicate Prevention**: Checks if data already exists for the target date
- **Data Continuity**: Verifies that data exists for the previous day
- **Data Quality**: Validates that previous day's data has reasonable trade counts (>1000 trades)
- **Time Window**: Only runs between 2 AM and 6 AM Eastern Time
- **Historical Coverage**: Ensures sufficient historical data exists (minimum 3 days)

If safety checks fail, the job exits with code 0 (success) so cron won't send error emails for expected failures.

### 2. Test the Pipeline

```bash
# Run comprehensive ETL test
python test_etl.py
```

### 3. Set Up Automated ETL

```bash
# Set up cron job (choose frequency)
bash scripts/setup_cron.sh

# Example: Run every hour
echo "0 * * * * /Users/nikhilmalkani/Documents/event_betting/scripts/run_etl.sh >> /Users/nikhilmalkani/Documents/event_betting/logs/etl.log 2>&1" | crontab -
```

## 📊 Database Schema

### Trades Table
- `trade_id`: Unique trade identifier
- `market_ticker`: Market ticker symbol
- `created_time`: Trade timestamp
- `created_time_ts`: Unix timestamp (for indexing)
- `count`: Number of shares
- `yes_price`, `no_price`: Prices in dollars
- `volume`: Calculated volume
- `side`: Trade side (buy/sell)
- `raw_data`: Full trade data as JSON

### Daily Aggregations Table
- `date`: Date (YYYY-MM-DD)
- `total_volume`: Total volume for the day
- `total_trades`: Number of trades
- `avg_volume_per_trade`: Average volume per trade

### Hourly Aggregations Table
- `date`: Date (YYYY-MM-DD)
- `hour`: Hour of day (0-23)
- `total_volume`: Total volume for the hour
- `total_trades`: Number of trades
- `avg_volume_per_trade`: Average volume per trade

## 🔧 Configuration

### ETL Job Parameters

```python
# In etl/trades_etl.py
etl.run_etl_job(
    hours_back=24,      # How many hours back to fetch
    max_trades=10000    # Maximum trades per run
)
```

### Dashboard Configuration

```python
# In dashboard/dashboard.py
@st.cache_data(ttl=60)  # Cache for 1 minute
def _get_trades_data(_self):
    return _self.trades_db.get_trades_for_analytics(days=14)
```

## 📈 Performance Benefits

### Before ETL (Direct API)
- **Page Load Time**: 2-3 minutes (500,000 trades)
- **API Dependency**: Dashboard breaks if Kalshi API is down
- **Rate Limits**: Risk of hitting API limits
- **Data Processing**: Real-time calculation on every page load

### After ETL (Database)
- **Page Load Time**: <1 second (pre-computed data)
- **Reliability**: Dashboard works even if Kalshi API is down
- **No Rate Limits**: Data fetched offline
- **Pre-computed**: Aggregations calculated once, reused many times

## 🛠️ Maintenance

### Monitor ETL Jobs

```bash
# View ETL logs
tail -f logs/etl.log

# Check database size
sqlite3 data/trades.db "SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size();"
```

### Database Maintenance

```bash
# Vacuum database to reclaim space
sqlite3 data/trades.db "VACUUM;"

# Check database integrity
sqlite3 data/trades.db "PRAGMA integrity_check;"
```

### Troubleshooting

1. **ETL Job Fails**: Check API credentials and network connectivity
2. **Dashboard Shows No Data**: Ensure ETL job has run successfully
3. **Slow Queries**: Check database indexes and consider vacuuming

## 🔄 Data Freshness

- **ETL Frequency**: Configurable (recommended: every 15-60 minutes)
- **Data Lag**: Up to ETL frequency (e.g., 15 minutes if running every 15 minutes)
- **Historical Data**: Can backfill up to 30 days of data

## 📝 Logging

ETL jobs log to:
- **Console**: Real-time output
- **File**: `logs/etl.log` (if using cron)
- **Database**: Timestamps in aggregation tables

## 🚨 Error Handling

- **API Failures**: Exponential backoff with retry
- **Database Errors**: Graceful degradation
- **Data Validation**: Skip invalid trades, log warnings
- **Monitoring**: Check logs and database stats regularly

## 🗂️ Data Retention

The ETL system includes automatic data retention management to keep database size manageable:

### **Retention Policies**
- **Raw Trades**: 30 days (configurable via `TRADES_RETENTION_DAYS`)
- **Aggregated Data**: 800 days (configurable via `AGGREGATIONS_RETENTION_DAYS`)

### **Automatic Cleanup**
- Runs automatically after each ETL job (if `ENABLE_AUTOMATIC_CLEANUP=true`)
- Deletes old trades data while preserving long-term analytics
- Maintains data continuity and dashboard performance

### **Manual Cleanup**
```bash
# Run manual data retention cleanup
python scripts/cleanup_retention.py
```

### **Configuration**
Set environment variables to customize retention periods:
```bash
export TRADES_RETENTION_DAYS=30        # Keep raw trades for 30 days
export AGGREGATIONS_RETENTION_DAYS=800  # Keep aggregations for 800 days
export ENABLE_AUTOMATIC_CLEANUP=true    # Enable automatic cleanup in ETL
```

## 🔮 Future Enhancements

1. **PostgreSQL**: Migrate from SQLite for better performance
2. **Real-time Updates**: WebSocket integration for live data
3. **Monitoring**: Health checks and alerting
4. **Backup**: Automated database backups
