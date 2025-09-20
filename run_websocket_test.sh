#!/bin/bash

# Simple script to run the WebSocket streaming test
# Usage: ./run_websocket_test.sh MARKET_TICKER
# Example: ./run_websocket_test.sh KXPRESIDENT-24

echo "🧪 Running Kalshi WebSocket Streaming Test"
echo "=========================================="
echo ""

# Check if we're in the right directory
if [ ! -f "test_websocket_streaming.py" ]; then
    echo "❌ Error: test_websocket_streaming.py not found in current directory"
    echo "Please run this script from the event_betting directory"
    exit 1
fi

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env file not found"
    echo "Make sure your Kalshi API credentials are configured"
    echo ""
fi

# Show usage if help requested or no arguments provided
if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ -z "$1" ]; then
    echo "Usage: $0 MARKET_TICKER"
    echo ""
    echo "Examples:"
    echo "  $0 KXPRESIDENT-24    # Monitor specific market"
    echo "  $0 KXELECTION-24     # Monitor election market"
    echo "  $0 KXSPOTIFYSONGRELEASETS-25B  # Monitor Spotify market"
    echo ""
    echo "Error: Market ticker is required"
    exit 1
fi

# Run the test with the specified market ticker
echo "🚀 Starting WebSocket test..."
echo "🎯 Monitoring market: $1"
echo "Press Ctrl+C to stop the test"
echo ""

python3 test_websocket_streaming.py "$1"
