# Kalshi Market Making Bot

A real-time WebSocket listener for Kalshi prediction markets that monitors orderbook updates, market tickers, public trades, and user fills. This bot provides a foundation for building market making strategies by giving you live access to market data streams.

## Features

### 📊 Real-time Market Data
- **Orderbook Updates**: Live orderbook snapshots and incremental deltas
- **Market Ticker**: Current bid/ask prices, volume, and open interest
- **Public Trades**: Real-time trade notifications with price and volume data
- **User Fills**: Your personal trade confirmations and position updates

### 🔐 Authentication Support
- Uses existing Kalshi API credentials from your `.env` file
- Supports both demo and production environments
- Automatic private key loading and signature generation

### 📈 Market Analysis
- Live spread calculation and monitoring
- Orderbook depth analysis
- Trade volume tracking
- Position monitoring

## Quick Start

### Prerequisites
- Python 3.8+
- Kalshi API credentials (see [Authentication](#authentication))
- All dependencies from the main project

### Installation
The bot uses the existing project dependencies. Make sure you're in the project root and have activated your conda environment:

```bash
source ~/.bash_profile
conda activate event_betting
```

### Basic Usage

**Monitor a specific market (public data only):**
```bash
python market_making_bot/mm_ws_listener.py --ticker KXEPSTEINLIST-26-HKIS
```

**Monitor with private data (fills, positions):**
```bash
python market_making_bot/mm_ws_listener.py --ticker KXEPSTEINLIST-26-HKIS --with-private
```

## Command Line Options

| Option | Description | Required |
|--------|-------------|----------|
| `--ticker` | Market ticker to monitor (e.g., `KXEPSTEINLIST-26-HKIS`) | Yes |
| `--with-private` | Subscribe to private streams (fills, positions) | No |

## Authentication

The bot automatically uses your existing authentication setup:

1. **Environment Variables**: Loads credentials from your `.env` file
2. **Demo Mode**: Respects `KALSHI_DEMO_MODE` setting
3. **Private Keys**: Uses your existing private key files

### Required Environment Variables
```bash
# Production
KALSHI_API_KEY_ID=your-api-key-id
KALSHI_PRIVATE_KEY_PATH=path/to/private_key.pem

# Demo (optional)
KALSHI_DEMO_API_KEY=your-demo-key-id
KALSHI_DEMO_PRIVATE_KEY_PATH=path/to/demo_private_key.pem
KALSHI_DEMO_MODE=false  # Set to true for demo mode
```

## WebSocket Streams

### 1. Orderbook Updates
Based on the [Kalshi Orderbook API](https://docs.kalshi.com/api-reference/websockets/orderbook-updates):

**Orderbook Snapshot:**
```json
{
  "type": "orderbook_snapshot",
  "sid": 2,
  "seq": 2,
  "msg": {
    "market_ticker": "FED-23DEC-T3.00",
    "yes": [[8, 300], [22, 333]],
    "no": [[54, 20], [56, 146]]
  }
}
```

**Orderbook Delta:**
```json
{
  "type": "orderbook_delta",
  "sid": 2,
  "seq": 3,
  "msg": {
    "market_ticker": "FED-23DEC-T3.00",
    "price": 96,
    "delta": -54,
    "side": "yes"
  }
}
```

### 2. Market Ticker
Based on the [Kalshi Market Ticker API](https://docs.kalshi.com/api-reference/websockets/market-ticker):

```json
{
  "type": "ticker",
  "sid": 11,
  "msg": {
    "market_ticker": "FED-23DEC-T3.00",
    "price": 48,
    "yes_bid": 45,
    "yes_ask": 53,
    "volume": 33896,
    "open_interest": 20422,
    "dollar_volume": 16948,
    "dollar_open_interest": 10211,
    "ts": 1669149841
  }
}
```

### 3. Public Trades
Based on the [Kalshi Public Trades API](https://docs.kalshi.com/api-reference/websockets/public-trades):

```json
{
  "type": "trade",
  "sid": 11,
  "msg": {
    "market_ticker": "HIGHNY-22DEC23-B53.5",
    "yes_price": 36,
    "no_price": 64,
    "count": 136,
    "taker_side": "no",
    "ts": 1669149841
  }
}
```

### 4. User Fills
Based on the [Kalshi User Fills API](https://docs.kalshi.com/api-reference/websockets/user-fills):

```json
{
  "type": "fill",
  "sid": 6,
  "msg": {
    "trade_id": "6b1c6b1c-6b1c-6b1c-6b1c-6b1c6b1c6b1c",
    "order_id": "6b1c6b1c-6b1c-6b1c-6b1c-6b1c6b1c6b1c",
    "market_ticker": "FED-23DEC-T3.00",
    "side": "buy",
    "count": 100,
    "price_dollars": 0.45,
    "fee_dollars": 0.045,
    "rebate_dollars": 0.0,
    "post_position": 100,
    "ts": 1669149841
  }
}
```

## Sample Output

```
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:164 - [ORDERBOOK SNAPSHOT] KXEPSTEINLIST-26-HKIS
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:171 -   YES Orders: 12 levels
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:172 -     Level 1: 45¢ x 1000
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:172 -     Level 2: 44¢ x 500
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:175 -   NO Orders: 8 levels
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:176 -     Level 1: 55¢ x 750
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:190 - [ORDERBOOK DELTA] KXEPSTEINLIST-26-HKIS | YES | Price: 45¢ | Delta: -100
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:231 -   Current YES: 44¢ / 46¢ (spread: 2¢)
2024-01-15 10:30:15 - INFO - mm_ws_listener.py:235 -   Current NO: 54¢ / 56¢ (spread: 2¢)
2024-01-15 10:30:16 - INFO - mm_ws_listener.py:258 - [TRADE] KXEPSTEINLIST-26-HKIS | BUY | YES: 45¢ | NO: 55¢ | Size: 50
2024-01-15 10:30:16 - INFO - mm_ws_listener.py:274 - [TICKER] KXEPSTEINLIST-26-HKIS | Bid: 44¢ | Ask: 46¢ | Last: 45¢ | Volume: 1234
```

## Architecture

The bot is built on top of your existing Kalshi infrastructure:

- **Config**: Uses your existing `Config` class for credential management
- **WebSocket Client**: Leverages `KalshiWebSocketClient` with proper authentication
- **HTTP Client**: Can use `KalshiAPIClient` for additional market data
- **Logging**: Integrates with your existing logging setup

## Data Storage

The bot maintains in-memory storage for:

- **Current Orderbook**: Real-time orderbook state
- **Recent Trades**: Last 100 public trades
- **Recent Fills**: Last 50 user fills
- **Current Positions**: Live position data

## Error Handling

- **Automatic Reconnection**: Handles WebSocket disconnections
- **Graceful Degradation**: Continues running if some streams fail
- **Comprehensive Logging**: Detailed error messages and debugging info
- **Connection Monitoring**: Tracks connection health and retry attempts

## Extending the Bot

This bot provides a foundation for building more sophisticated market making strategies:

1. **Add Trading Logic**: Implement order placement and cancellation
2. **Risk Management**: Add position limits and exposure controls
3. **Strategy Implementation**: Build mean reversion, momentum, or arbitrage strategies
4. **Portfolio Management**: Monitor multiple markets simultaneously
5. **Performance Tracking**: Add P&L calculation and performance metrics

## Troubleshooting

### Common Issues

**Authentication Errors:**
- Verify your `.env` file contains valid credentials
- Check that private key files exist and are readable
- Ensure `KALSHI_DEMO_MODE` is set correctly

**Connection Issues:**
- Check your internet connection
- Verify the ticker symbol is valid and active
- Try running in demo mode first

**No Data Received:**
- Ensure the market is active and has trading volume
- Check that you're subscribed to the correct ticker
- Verify WebSocket connection is established

### Debug Mode
Enable debug logging by modifying the logging level in the script:
```python
setup_logging(level=logging.DEBUG, include_filename=True)
```

## License

This bot is part of the larger event betting project and follows the same licensing terms.

## Contributing

When extending this bot:
1. Follow the existing code style and patterns
2. Add comprehensive logging for new features
3. Update this README with new functionality
4. Test with both demo and production environments
5. Ensure error handling is robust

## Related Documentation

- [Kalshi WebSocket API Documentation](https://docs.kalshi.com/api-reference/websockets/)
- [Kalshi REST API Documentation](https://docs.kalshi.com/api-reference/)
- [Project Main README](../README.md)
- [Setup Guide](../SETUP.md)
