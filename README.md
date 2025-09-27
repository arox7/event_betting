# Kalshi Market Analysis Dashboard

A Python-based dashboard for analyzing Kalshi prediction markets with AI-powered screening capabilities and interactive visualizations.

## Features

- **Interactive Dashboard**: Streamlit-based web interface for market analysis
- **AI-Powered Screening**: Natural language queries using Gemini AI for custom market filtering
- **Configurable Criteria**: Customizable screening parameters (volume, spread, liquidity, etc.)
- **Market Analysis**: Detailed scoring system based on multiple factors
- **On-Demand Data**: Fetch and analyze current market data with manual refresh
- **Portfolio Tracking**: Complete portfolio management with accurate P&L calculations
- **API Integration**: Full integration with Kalshi's Python SDK
- **Clean Architecture**: Streamlined codebase without real-time websocket dependencies

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd event_betting
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
# Create .env file with your Kalshi API credentials
# See SETUP.md for detailed instructions
```

## Configuration

Create a `.env` file with your Kalshi API credentials. See `SETUP.md` for detailed instructions:

```env
# Kalshi API Configuration
KALSHI_API_KEY_ID=your-api-key-id-here
KALSHI_PRIVATE_KEY_PATH=path/to/private_key.pem

# Environment (demo or production)
KALSHI_DEMO_MODE=true
KALSHI_API_HOST=https://api.elections.kalshi.com/trade-api/v2

# Dashboard Configuration
DASHBOARD_PORT=8501
DASHBOARD_HOST=localhost

# Optional: Gemini AI Configuration
GEMINI_API_KEY=your-gemini-api-key-here
```

**Important**: This dashboard uses proper Kalshi API authentication following the [official documentation](https://docs.kalshi.com/getting_started/api_keys). You need to:
1. Generate API credentials from your Kalshi account
2. Save your private key as a `.pem` file
3. Configure the environment variables
4. (Optional) Get a Gemini API key for AI features

## Usage

### Running the Dashboard

Use the startup script (recommended):
```bash
./run_dashboard.sh
```

Or run directly with Streamlit:
```bash
streamlit run dashboard/dashboard.py
```

The dashboard will be available at `http://localhost:8501`

## Dashboard Features

### **Site Analytics**
- **Daily Trade Volume Chart**: Interactive line chart showing the latest 7 days of trading volume
- **Hourly Volume Analysis**: Average hourly trading volume patterns across the latest 7 days
- **Trade Volume by Category**: Stacked bar chart showing percentage breakdown by market categories
- **Analytics Metrics**: Key performance indicators including total volume, trade count, and average trade size
- **Real-time Data**: All charts automatically show the most recent 7 days of available data
- **Eastern Time Display**: All timestamps and data are displayed in Eastern Time (ET) for consistency

### **Market Analytics**
- **Top Traded Markets by Volume**: Ranked list of the top 5 markets by total trading volume
- **Top Traded Markets by Trade Count**: Ranked list of the top 5 markets by number of trades
- **Market Details**: Each market shows volume, trade count, and average trade size
- **Readable Market Names**: Displays full market titles instead of cryptic ticker symbols
- **7-Day Lookback**: Rankings based on the latest 7 days of trading activity
- **API-Enhanced Data**: Market titles fetched from Kalshi API for better readability

### **Portfolio Management**
- **Complete Portfolio Tracking**: Cash balance, positions, and accurate P&L calculations
- **Real-time Market Values**: Current market prices and position values
- **Performance Analytics**: Realized and unrealized P&L with detailed breakdowns
- **Position Management**: View all active and closed positions with trading history
- **P&L Charts**: Visual representation of trading performance over time

### **Market Analysis**
- **AI-Powered Screening**: Use natural language to create custom market filters
- **Interactive Tables**: Sortable and filterable market data
- **Visual Analytics**: Charts and graphs for market distribution and trends
- **Market Details**: Detailed view of individual markets with full information
- **Custom Filtering**: Advanced filtering options for market characteristics
- **On-Demand Data**: Manual refresh for current market data

## Analysis Criteria

The dashboard can filter and analyze markets based on:

- **Volume**: Trading volume requirements (total and 24h)
- **Spread**: Bid-ask spread analysis (percentage and cents)
- **Liquidity**: Market liquidity requirements
- **Time to Close**: Days until market close
- **Category**: Market categories (politics, economics, etc.)
- **Price Range**: Market price ranges and volatility
- **AI Queries**: Natural language filtering with Gemini AI

## AI-Powered Analysis

The dashboard includes advanced AI features powered by Google Gemini:

- **Natural Language Queries**: Ask questions like "Show me markets closing soon with high volume" or "Find undervalued political markets"
- **Custom Screening Functions**: AI generates Python code to filter markets based on your specific criteria
- **Interactive Code Editing**: Review and modify AI-generated screening logic
- **Smart Market Insights**: Get AI-powered analysis of market trends and opportunities

### Example AI Queries
- "Markets with tight spreads and high liquidity"
- "Election markets closing in the next 30 days"
- "Undervalued markets with recent volume spikes"
- "Markets where the probability seems disconnected from fundamentals"

## Portfolio Tracking

The dashboard provides comprehensive portfolio management:

### **Balance Overview**
- **Total Portfolio Value**: Cash + current market value of all positions
- **Cash Balance**: Available funds for trading
- **Position Value**: Current market value of all holdings
- **Position Count**: Number of active positions

### **Performance Tracking**
- **24-Hour P&L**: Realized gains/losses from completed trades
- **Trading Activity**: Number of trades and total volume
- **Return Percentage**: Performance relative to portfolio size
- **Top Positions**: Largest holdings by value

### **P&L Calculation**
The dashboard uses a **realized P&L approach** for performance tracking:
- Tracks actual gains/losses from completed trades
- Calculates based on trade fills from the last 24 hours
- Shows trading volume and activity metrics
- Simple and transparent - no complex historical tracking required

### **Data Sources**
All portfolio data comes directly from the [Kalshi Portfolio API](https://docs.kalshi.com/python-sdk/api/PortfolioApi):
- `/portfolio/balance` - Cash balance
- `/portfolio/positions` - Current positions  
- `/portfolio/fills` - Trading history for P&L calculation

## Project Structure

```
event_betting/
├── main.py                    # Main application entry point
├── config.py                  # Configuration settings
├── requirements.txt           # Python dependencies
├── run_dashboard.sh          # Quick start script
├── README.md                 # This file
├── SETUP.md                  # Detailed setup instructions
├── dashboard/                # Dashboard application
│   ├── __init__.py
│   ├── dashboard.py          # Main dashboard interface with Site Analytics
│   ├── main.py              # Alternative dashboard entry point
│   ├── portfolio.py         # Portfolio management page
│   ├── screener.py          # Market screening page
│   └── constants.py         # Dashboard constants
├── database/                 # Database models and operations
│   ├── __init__.py
│   └── models.py            # SQLite database schema and operations
├── etl/                     # Extract, Transform, Load processes
│   ├── __init__.py
│   ├── trades_etl.py        # Trades data ETL pipeline
│   ├── events_etl.py        # Events data ETL pipeline
│   └── markets_etl.py       # Markets data ETL pipeline
├── scripts/                 # Utility scripts
│   ├── run_etl.sh          # ETL execution script
│   ├── fetch_all_markets.py # Market data fetching
│   └── cleanup_retention.py # Data retention cleanup
├── kalshi/                   # Kalshi API integration
│   ├── __init__.py
│   ├── client.py            # Kalshi API client
│   ├── models.py            # Data models and classes
│   ├── portfolio_functions.py # Portfolio calculations
│   ├── market_functions.py  # Market data functions
│   ├── websocket.py         # WebSocket functionality (optional)
│   └── ...
├── screening/                # Market screening logic
│   ├── __init__.py
│   ├── market_screener.py   # Rule-based screening
│   └── gemini_screener.py   # AI-powered screening
└── tests/                    # Test utilities
    ├── test_setup.py
    └── ...
```

## API Requirements

To use the dashboard, you'll need:

### Required
1. **Kalshi API Key ID**: Get from your Kalshi account profile
2. **RSA Private Key**: Generated by Kalshi (saved as .pem file)

### Optional
3. **Gemini API Key**: For AI-powered features (get from [Google AI Studio](https://makersuite.google.com/app/apikey))

The application uses proper RSA-PSS signature authentication as specified in the [Kalshi API documentation](https://docs.kalshi.com/getting_started/api_keys). This provides better security and follows Kalshi's recommended authentication method.

**Note**: Portfolio features require authentication. The dashboard will show "Login required for portfolio data" if not properly authenticated.

## Logging

The application logs to console:

- Dashboard startup and status
- API connection status
- Error messages
- User interactions

## Development

### Adding New Analysis Features

1. Update `ScreeningCriteria` in `kalshi/models.py`
2. Add filtering logic in `screening/market_screener.py`
3. Update dashboard UI in `dashboard/dashboard.py`
4. Add AI prompts in `screening/gemini_screener.py` for natural language support

### Customizing the Dashboard

The dashboard is built with Streamlit and can be customized by modifying files in the `dashboard/` directory:

- **Main Interface**: `dashboard/dashboard.py` - Primary dashboard layout
- **Portfolio Page**: `dashboard/portfolio.py` - Portfolio management features
- **Screener Page**: `dashboard/screener.py` - Market screening interface
- **Constants**: `dashboard/constants.py` - UI configuration and defaults

**Customization Options:**
- Add new charts and visualizations
- Modify the layout and styling
- Add new filtering and analysis options
- Integrate additional AI capabilities
- Extend portfolio tracking features

## Data Management

### **ETL Pipeline**
The dashboard includes a comprehensive ETL (Extract, Transform, Load) system for managing historical data:

- **Trades ETL**: Fetches and processes historical trade data from Kalshi API
- **Events ETL**: Processes market event data and categories
- **Markets ETL**: Handles market metadata and information
- **Automated Aggregations**: Pre-computed daily, hourly, and category-level aggregations
- **Data Retention**: Configurable retention policies for raw and aggregated data

### **Database Schema**
- **Raw Tables**: `trades`, `events`, `markets` - Individual records from Kalshi API
- **Aggregate Tables**: `daily_aggregations`, `hourly_aggregations`, `daily_category_aggregations`
- **Eastern Time Storage**: All timestamps stored in Eastern Time (ET) for consistency
- **SQLite Database**: Local storage in `data/trades.db`

### **Data Backfill**
Use the ETL scripts to backfill missing data:
```bash
# Backfill specific dates
python scripts/run_backfill.py

# Run ETL for specific date
python etl/trades_etl.py --date 2025-09-26
```

## Troubleshooting

### Common Issues

1. **API Connection Failed**: Check your API credentials and network connection
2. **No Markets Found**: Verify the API is returning data and check filters  
3. **Dashboard Not Loading**: Ensure Streamlit is installed and port is available
4. **AI Features Not Working**: Verify your Gemini API key is set in the `.env` file
5. **Portfolio Data Missing**: Check that your Kalshi API credentials are valid and authenticated
6. **Permission Errors**: Make sure the startup script is executable: `chmod +x run_dashboard.sh`
7. **Portfolio Values Incorrect**: Use the refresh button to update portfolio data manually
8. **Charts Showing Old Data**: Run ETL to backfill missing data or check cron job status
9. **Import Errors**: Ensure you're running from the project root directory

### Debug Mode

Enable debug logging by setting the environment variable:

```bash
export PYTHONPATH=. && python -c "import logging; logging.basicConfig(level=logging.DEBUG)" && streamlit run dashboard/dashboard.py
```

## License

This project is for educational and research purposes. Please ensure compliance with Kalshi's terms of service and applicable regulations.

## Disclaimer

This software is provided as-is for educational and analysis purposes. This is a market analysis tool, not a trading bot. Any trading decisions should be made carefully with proper research and risk management. Always comply with applicable regulations and platform terms of service.
