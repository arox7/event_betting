# Kalshi Market Making Bot

A sophisticated single-sided market making bot for Kalshi prediction markets, designed to provide liquidity while managing risk through intelligent position limits and dynamic pricing strategies.

## 🚀 Features

### Core Strategy
- **Single-Sided Market Making**: Only makes markets on one side (Yes or No) to avoid directional risk
- **Order Group Management**: Uses Kalshi's order groups to limit total exposure per market
- **Dynamic Pricing**: Adjusts bid/ask prices based on current positions and market conditions
- **Real-Time Data**: Uses WebSockets for live market data and order updates
- **Risk Management**: Comprehensive risk controls with position limits and stop-losses

### Advanced Features
- **Configurable Trading Modes**: Conservative, Moderate, and Aggressive presets
- **Performance Monitoring**: Real-time tracking of P&L, fill rates, and risk metrics
- **Alert System**: Automated alerts for risk breaches and system issues
- **Comprehensive Logging**: Detailed logs for debugging and performance analysis
- **Dry-Run Mode**: Test strategies without placing actual orders

## 📋 Prerequisites

1. **Kalshi Account**: You need a Kalshi account with API access
2. **API Credentials**: Generate API key and private key from your Kalshi account
3. **Python Environment**: Python 3.8+ with required dependencies
4. **Sufficient Balance**: Recommended minimum $100 for testing

## 🛠️ Installation

1. **Clone the repository** (if not already done):
```bash
git clone <repository-url>
cd event_betting
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Environment variables are already configured**:
   - Your existing `.env` file will be used automatically
   - The bot will load all environment variables from your `.env` file
   - No additional setup needed for basic configuration

4. **Optional: Create bot configuration file**:
```bash
cp bot_config_example.yaml bot_config.yaml
# Edit bot_config.yaml with your preferred settings
```

## 🚀 Quick Start

### Basic Usage

```bash
# Run with default configuration
python run_market_making_bot.py

# Run in conservative mode
python run_market_making_bot.py --mode conservative

# Run with custom config file
python run_market_making_bot.py --config my_config.yaml

# Run in dry-run mode (no actual orders)
python run_market_making_bot.py --dry-run

# Run with debug logging
python run_market_making_bot.py --log-level DEBUG
```

### Command Line Options

```bash
python run_market_making_bot.py --help
```

Key options:
- `--config`: Path to configuration file
- `--mode`: Trading mode (conservative/moderate/aggressive)
- `--dry-run`: Run without placing actual orders
- `--log-level`: Set logging level
- `--max-daily-loss`: Override maximum daily loss
- `--max-position-per-market`: Override position limit per market

## ⚙️ Configuration

### Trading Modes

#### Conservative Mode
- **Max Position**: 5 contracts per market
- **Max Exposure**: 25 total contracts
- **Max Daily Loss**: $25
- **Spread**: 3 cents default
- **Markets**: High volume, tight spreads only

#### Moderate Mode (Default)
- **Max Position**: 10 contracts per market
- **Max Exposure**: 50 total contracts
- **Max Daily Loss**: $50
- **Spread**: 2 cents default
- **Markets**: Good volume and liquidity

#### Aggressive Mode
- **Max Position**: 20 contracts per market
- **Max Exposure**: 100 total contracts
- **Max Daily Loss**: $100
- **Spread**: 1 cent default
- **Markets**: Any market meeting basic criteria

### Configuration File

The bot uses YAML configuration files. See `bot_config_example.yaml` for a complete example.

Key sections:
- **risk_limits**: Position and loss limits
- **market_selection**: Criteria for market selection
- **pricing_strategy**: How to set bid/ask prices
- **order_management**: Order timing and retry logic

### Environment Variables

You can override any configuration using environment variables:

```bash
export BOT_MAX_DAILY_LOSS=25.0
export BOT_MAX_POSITION_PER_MARKET=5
export BOT_MIN_VOLUME=2000
export BOT_DEFAULT_SPREAD_CENTS=3
```

## 📊 Monitoring and Alerts

### Real-Time Monitoring

The bot provides comprehensive monitoring through:

1. **Console Logs**: Real-time status updates
2. **Log Files**: Detailed logs in `logs/` directory
3. **Performance Metrics**: Fill rates, P&L, exposure tracking
4. **Alert System**: Automated alerts for risk breaches

### Key Metrics Tracked

- **Fill Rate**: Percentage of orders that get filled
- **Profit per Trade**: Average profit/loss per filled order
- **Total Exposure**: Current position across all markets
- **Drawdown**: Current and maximum drawdown from peak
- **API Errors**: System health monitoring

### Alert Categories

- **RISK**: Position limits, daily loss limits, drawdown alerts
- **PERFORMANCE**: Low fill rates, negative profit per trade
- **SYSTEM**: API errors, websocket disconnections
- **ORDER**: Order rejections, failed cancellations

## 🛡️ Risk Management

### Position Limits
- **Per Market**: Maximum contracts per individual market
- **Total Exposure**: Maximum contracts across all markets
- **Order Size**: Maximum contracts per individual order

### Loss Limits
- **Daily Loss**: Maximum loss per day before stopping
- **Stop Loss**: Percentage-based stop loss
- **Emergency Stop**: Critical loss threshold

### Market Selection
- **Volume Requirements**: Minimum daily trading volume
- **Spread Limits**: Maximum acceptable bid-ask spread
- **Liquidity Requirements**: Minimum market liquidity
- **Time to Close**: Markets closing too soon are avoided

## 🔧 Advanced Usage

### Custom Strategies

You can implement custom strategies by modifying the bot code:

1. **Market Selection**: Override `_is_market_suitable()` method
2. **Pricing Logic**: Modify `_calculate_target_prices()` method
3. **Risk Controls**: Customize `_check_emergency_conditions()` method

### Integration with Dashboard

The bot integrates with the existing Kalshi dashboard:

```bash
# Run dashboard alongside bot
streamlit run dashboard/dashboard.py

# Bot logs will appear in dashboard logs
# Portfolio changes will be reflected in real-time
```

### API Integration

The bot uses the same Kalshi API client as the dashboard:

```python
from kalshi import KalshiAPIClient
from bot_config import BotConfigManager

# Use existing API client
client = KalshiAPIClient(config)

# Load bot configuration
config_manager = BotConfigManager()
bot_config = config_manager.load_config()
```

## 📈 Performance Optimization

### Best Practices

1. **Start Conservative**: Begin with conservative settings and gradually increase
2. **Monitor Closely**: Watch fill rates and adjust spreads accordingly
3. **Market Selection**: Focus on high-volume markets with tight spreads
4. **Risk Management**: Never exceed your risk tolerance
5. **Regular Monitoring**: Check logs and performance metrics regularly

### Troubleshooting

#### Low Fill Rates
- Increase spread (widen bid-ask gap)
- Focus on higher volume markets
- Check if prices are competitive

#### High API Errors
- Check internet connection
- Verify API credentials
- Monitor Kalshi API status

#### Unexpected Positions
- Review market selection criteria
- Check for news events affecting markets
- Adjust position limits if needed

## 🚨 Safety Features

### Emergency Stops
- **Daily Loss Limit**: Automatic stop if daily loss exceeds limit
- **Position Limits**: Stop if total exposure exceeds limit
- **API Errors**: Stop if too many API errors occur
- **Manual Stop**: Ctrl+C for graceful shutdown

### Order Management
- **Order Groups**: Automatic position limiting per market
- **Order Timeouts**: Automatic cancellation of stale orders
- **Batch Orders**: Efficient order placement and management
- **Error Handling**: Robust error handling and recovery

## 📝 Logging and Debugging

### Log Files
- **Bot Logs**: `logs/market_making_bot_YYYYMMDD_HHMMSS.log`
- **Monitoring Logs**: `logs/bot_monitoring_YYYYMMDD_HHMMSS.log`
- **API Logs**: Integrated with existing logging system

### Debug Mode
```bash
python run_market_making_bot.py --log-level DEBUG
```

### Performance Analysis
```bash
# Export metrics to CSV
python -c "
from bot_monitoring import BotMonitor
monitor = BotMonitor()
monitor.export_metrics('performance_metrics.csv')
"
```

## 🔒 Security Considerations

### API Security
- **Private Keys**: Store private keys securely
- **Environment Variables**: Use environment variables for sensitive data
- **Access Control**: Limit API key permissions
- **Regular Rotation**: Rotate API keys regularly

### Risk Management
- **Position Limits**: Always set appropriate position limits
- **Loss Limits**: Set conservative daily loss limits
- **Monitoring**: Monitor bot performance continuously
- **Testing**: Test strategies in demo mode first

## 📚 API Reference

### Key Classes

- **KalshiMarketMakingBot**: Main bot class
- **MarketMakingConfig**: Configuration management
- **BotMonitor**: Performance monitoring and alerts
- **BotConfigManager**: Configuration loading and validation

### Key Methods

- **start()**: Start the bot
- **stop()**: Stop the bot gracefully
- **_find_suitable_markets()**: Market selection logic
- **_calculate_target_prices()**: Pricing strategy
- **_place_batch_orders()**: Order management

## 🤝 Contributing

1. **Fork the repository**
2. **Create a feature branch**
3. **Make your changes**
4. **Add tests if applicable**
5. **Submit a pull request**

## 📄 License

This project is for educational and research purposes. Please ensure compliance with Kalshi's terms of service and applicable regulations.

## ⚠️ Disclaimer

This software is provided as-is for educational and analysis purposes. This is a market making tool, not a guaranteed profit system. Trading involves risk, and you should:

- **Test thoroughly** in demo mode before using real money
- **Start with small amounts** to understand the bot's behavior
- **Monitor continuously** and be prepared to stop the bot
- **Understand the risks** of market making and prediction markets
- **Comply with regulations** and platform terms of service

**Use at your own risk. The authors are not responsible for any financial losses.**

## 🆘 Support

For issues and questions:

1. **Check the logs** for error messages
2. **Review the configuration** for incorrect settings
3. **Test in dry-run mode** to isolate issues
4. **Check Kalshi API status** for platform issues
5. **Create an issue** in the repository with detailed information

## 📖 Additional Resources

- [Kalshi API Documentation](https://docs.kalshi.com/)
- [Kalshi Python SDK](https://github.com/Kalshi/kalshi-python)
- [Prediction Market Strategies](https://docs.kalshi.com/guides/)
- [Risk Management Best Practices](https://docs.kalshi.com/guides/risk-management)
