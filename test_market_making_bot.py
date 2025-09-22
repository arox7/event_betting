#!/usr/bin/env python3
"""
Test script for Kalshi Market Making Bot

This script validates the bot setup and configuration without placing actual orders.
"""

import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, setup_logging
from bot_config import BotConfigManager, TradingMode
from market_making_bot import KalshiMarketMakingBot
from kalshi import KalshiAPIClient

def test_configuration():
    """Test configuration loading and validation."""
    print("🔧 Testing Configuration System...")
    
    try:
        # Test configuration manager
        config_manager = BotConfigManager()
        
        # Test preset configurations
        for mode in [TradingMode.CONSERVATIVE, TradingMode.MODERATE, TradingMode.AGGRESSIVE]:
            config = config_manager.get_preset_config(mode)
            print(f"  ✅ {mode.value} mode: max_position={config.risk_limits.max_position_per_market}, max_exposure={config.risk_limits.max_total_exposure}")
        
        # Test environment variable loading
        config = config_manager.load_config()
        print(f"  ✅ Loaded configuration: trading_mode={config.trading_mode.value}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Configuration test failed: {e}")
        return False

def test_api_connection():
    """Test Kalshi API connection."""
    print("🌐 Testing API Connection...")
    
    try:
        config = Config()
        client = KalshiAPIClient(config)
        
        # Test basic API calls
        balance = client.get_balance_dollars()
        if balance is not None:
            print(f"  ✅ API connection successful, balance: ${balance:.2f}")
        else:
            print("  ⚠️  API connection successful but no balance data")
        
        # Test market data
        markets = client.get_markets(limit=5)
        if markets:
            print(f"  ✅ Market data retrieved: {len(markets)} markets")
        else:
            print("  ⚠️  No market data retrieved")
        
        return True
        
    except Exception as e:
        print(f"  ❌ API connection test failed: {e}")
        return False

def test_websocket_connection():
    """Test websocket connection."""
    print("🔌 Testing WebSocket Connection...")
    
    try:
        from kalshi.websocket import KalshiWebSocketClient
        
        config = Config()
        ws_client = KalshiWebSocketClient(config)
        
        # Test websocket connection (without actually connecting)
        print(f"  ✅ WebSocket client created: {ws_client.ws_url}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ WebSocket test failed: {e}")
        return False

def test_bot_initialization():
    """Test bot initialization."""
    print("🤖 Testing Bot Initialization...")
    
    try:
        config = Config()
        config_manager = BotConfigManager()
        bot_config = config_manager.get_preset_config(TradingMode.CONSERVATIVE)
        
        # Create bot instance
        bot = KalshiMarketMakingBot(config, bot_config)
        print(f"  ✅ Bot initialized successfully")
        print(f"  📊 Configuration: {bot_config.trading_mode.value} mode")
        print(f"  🎯 Market side: {bot_config.market_side.value}")
        print(f"  💰 Max daily loss: ${bot_config.risk_limits.max_daily_loss}")
        print(f"  📈 Max position per market: {bot_config.risk_limits.max_position_per_market}")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Bot initialization test failed: {e}")
        return False

def test_monitoring_system():
    """Test monitoring system."""
    print("📊 Testing Monitoring System...")
    
    try:
        from bot_monitoring import BotMonitor
        
        monitor = BotMonitor()
        print("  ✅ Monitoring system initialized")
        
        # Test alert system
        monitor.alert_manager.send_alert("INFO", "TEST", "Test alert message")
        print("  ✅ Alert system working")
        
        # Test metrics tracking
        monitor.record_order_placed({"ticker": "TEST", "side": "yes"})
        monitor.record_order_filled({"ticker": "TEST", "side": "yes", "volume": 1, "fees": 2})  # Fees in cents
        print("  ✅ Metrics tracking working")
        
        # Get status report
        report = monitor.get_status_report()
        print(f"  ✅ Status report generated: {len(report)} sections")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Monitoring system test failed: {e}")
        return False

def test_environment():
    """Test environment setup."""
    print("🔍 Testing Environment Setup...")
    
    # Check required environment variables
    required_vars = ['KALSHI_API_KEY_ID', 'KALSHI_PRIVATE_KEY_PATH']
    missing_vars = []
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
        else:
            print(f"  ✅ {var} is set")
    
    if missing_vars:
        print(f"  ❌ Missing required environment variables: {', '.join(missing_vars)}")
        return False
    
    # Check private key file
    private_key_path = os.getenv('KALSHI_PRIVATE_KEY_PATH')
    if private_key_path and os.path.exists(private_key_path):
        print(f"  ✅ Private key file exists: {private_key_path}")
    else:
        print(f"  ❌ Private key file not found: {private_key_path}")
        return False
    
    # Check demo mode
    demo_mode = os.getenv('KALSHI_DEMO_MODE', 'true').lower() == 'true'
    print(f"  ✅ Demo mode: {'enabled' if demo_mode else 'disabled'}")
    
    return True

def main():
    """Run all tests."""
    print("🧪 Kalshi Market Making Bot - Test Suite")
    print("=" * 50)
    
    # Setup logging
    setup_logging(level=logging.INFO, include_filename=True)
    
    tests = [
        ("Environment Setup", test_environment),
        ("Configuration System", test_configuration),
        ("API Connection", test_api_connection),
        ("WebSocket Connection", test_websocket_connection),
        ("Bot Initialization", test_bot_initialization),
        ("Monitoring System", test_monitoring_system),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"  ❌ Test failed with exception: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 50)
    print("📋 Test Results Summary:")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
        if result:
            passed += 1
    
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The bot is ready to run.")
        print("\nNext steps:")
        print("1. Review and customize bot_config.yaml")
        print("2. Run in dry-run mode: python run_market_making_bot.py --dry-run")
        print("3. Start with conservative mode: python run_market_making_bot.py --mode conservative")
    else:
        print("⚠️  Some tests failed. Please fix the issues before running the bot.")
        print("\nCommon issues:")
        print("- Missing environment variables (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH)")
        print("- Invalid API credentials")
        print("- Network connectivity issues")
        print("- Missing dependencies (pip install -r requirements.txt)")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
