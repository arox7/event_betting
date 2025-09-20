#!/usr/bin/env python3
"""
Simple test script for Kalshi WebSocket streaming.
This script will connect to the WebSocket, subscribe to various channels,
and stream all received messages to stdout for monitoring.

Usage: python3 test_websocket_streaming.py MARKET_TICKER
Example: python3 test_websocket_streaming.py KXPRESIDENT-24
"""
import asyncio
import json
import logging
import signal
import sys
import argparse
from datetime import datetime, timezone
from typing import Dict, Any

from config import Config
from kalshi_websocket import KalshiWebSocketClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WebSocketStreamTester:
    """Simple WebSocket stream tester."""
    
    def __init__(self, market_ticker: str):
        """Initialize the tester."""
        self.config = Config()
        self.ws_client = KalshiWebSocketClient(self.config)
        self.running = True
        self.message_count = 0
        self.shutdown_requested = False
        self.market_ticker = market_ticker
        
        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"\n🛑 Received signal {signum}. Shutting down gracefully...")
        self.shutdown_requested = True
        self.running = False
    
    def _format_message(self, message: Dict[str, Any]) -> str:
        """Format a WebSocket message for display."""
        timestamp = message.get('timestamp', datetime.now(timezone.utc))
        channel = message.get('channel', 'unknown')
        message_type = message.get('message_type', 'unknown')
        data = message.get('data', {})
        
        # Create a compact representation
        lines = [
            f"📡 [{timestamp.strftime('%H:%M:%S')}] {channel.upper()} - {message_type}",
        ]
        
        # Add key data fields based on channel
        if channel == 'market_positions':
            if 'position' in data:
                pos = data['position']
                lines.append(f"   Position: {pos.get('ticker', 'N/A')} | Qty: {pos.get('position', 0)} | Value: ${pos.get('market_exposure', 0) / 100:.2f}")
        
        elif channel == 'fills':
            if 'fill' in data:
                fill = data['fill']
                lines.append(f"   Fill: {fill.get('ticker', 'N/A')} | {fill.get('side', 'N/A')} | {fill.get('count', 0)} @ ${fill.get('price', 0) / 100:.2f}")
        
        elif channel == 'orderbook_delta':
            if 'market_ticker' in data:
                lines.append(f"   Market: {data['market_ticker']}")
            if 'yes_bid' in data:
                lines.append(f"   Yes: ${data.get('yes_bid', 0) / 100:.2f} / ${data.get('yes_ask', 0) / 100:.2f}")
                lines.append(f"   No:  ${data.get('no_bid', 0) / 100:.2f} / ${data.get('no_ask', 0) / 100:.2f}")
        
        elif channel == 'ticker':
            if 'market_ticker' in data:
                lines.append(f"   Market: {data['market_ticker']}")
                lines.append(f"   Bid: ${data.get('bid', 0) / 100:.2f} | Ask: ${data.get('ask', 0) / 100:.2f}")
        
        elif channel == 'trades':
            if 'market_ticker' in data:
                lines.append(f"   Trade: {data['market_ticker']} | {data.get('side', 'N/A')} | {data.get('count', 0)} @ ${data.get('price', 0) / 100:.2f}")
        
        else:
            # Generic display for unknown channels
            lines.append(f"   Data: {json.dumps(data, indent=2)[:200]}...")
        
        return "\n".join(lines)
    
    async def message_callback(self, data: Dict[str, Any]):
        """Callback for handling WebSocket messages."""
        # Check if shutdown was requested
        if self.shutdown_requested:
            return
            
        self.message_count += 1
        
        # Create message object
        message = {
            'timestamp': datetime.now(timezone.utc),
            'channel': data.get('channel', 'unknown'),
            'message_type': data.get('message_type', 'unknown'),
            'data': data
        }
        
        # Print formatted message
        print(self._format_message(message))
        print("-" * 80)
        
        # Print raw data occasionally for debugging
        if self.message_count % 10 == 0:
            print(f"🔍 RAW DATA (message #{self.message_count}):")
            print(json.dumps(data, indent=2))
            print("-" * 80)
    
    async def run_test(self):
        """Run the WebSocket streaming test."""
        print("🚀 Starting Kalshi WebSocket Streaming Test")
        print("=" * 80)
        print(f"📍 WebSocket URL: {self.ws_client.ws_url}")
        print(f"🔑 API Key ID: {self.config.KALSHI_API_KEY_ID[:8]}..." if self.config.KALSHI_API_KEY_ID else "❌ No API Key")
        print(f"🎯 Demo Mode: {self.config.KALSHI_DEMO_MODE}")
        print(f"🎯 Target Market: {self.market_ticker}")
        print("=" * 80)
        
        try:
            # Connect to WebSocket
            print("🔌 Connecting to WebSocket...")
            await self.ws_client.connect()
            print("✅ Connected successfully!")
            
            # Register callbacks
            self.ws_client._register_callback("market_positions", self.message_callback)
            self.ws_client._register_callback("fills", self.message_callback)
            self.ws_client._register_callback("orderbook_delta", self.message_callback)
            self.ws_client._register_callback("ticker", self.message_callback)
            self.ws_client._register_callback("trades", self.message_callback)
            
            print("\n📡 Subscribing to channels...")
            
            # Subscribe to user data (fills, positions) - these don't need market tickers
            print("   🔔 Subscribing to fills...")
            self.ws_client.subscribe_fills(self.message_callback)
            
            print("   🔔 Subscribing to market positions...")
            self.ws_client.subscribe_market_positions(self.message_callback)
            
            # Subscribe to market-specific data
            print(f"   🔔 Subscribing to market data for: {self.market_ticker}")
            self.ws_client.subscribe_orderbook_updates([self.market_ticker], self.message_callback)
            self.ws_client.subscribe_market_ticker([self.market_ticker], self.message_callback)
            self.ws_client.subscribe_public_trades([self.market_ticker], self.message_callback)
            
            print("\n🎧 Listening for messages... (Press Ctrl+C to stop)")
            print("=" * 80)
            
            # Keep the connection alive and wait for messages
            try:
                while self.running and not self.shutdown_requested:
                    # Just wait and let the WebSocket client handle messages
                    await asyncio.sleep(0.1)
                        
            except KeyboardInterrupt:
                print("\n🛑 Keyboard interrupt received")
                self.shutdown_requested = True
            
        except Exception as e:
            print(f"❌ Error: {e}")
            logger.error(f"WebSocket test error: {e}")
        finally:
            print("\n🔌 Disconnecting...")
            self.running = False
            await self.ws_client.disconnect()
            print(f"📊 Total messages received: {self.message_count}")
            print("👋 Test completed!")

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Test Kalshi WebSocket streaming for a specific market",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 test_websocket_streaming.py KXPRESIDENT-24
  python3 test_websocket_streaming.py KXELECTION-24
  python3 test_websocket_streaming.py KXSPOTIFYSONGRELEASETS-25B
        """
    )
    
    parser.add_argument(
        'market_ticker',
        help='Market ticker to monitor (e.g., KXPRESIDENT-24)'
    )
    
    return parser.parse_args()

async def main():
    """Main function."""
    args = parse_arguments()
    tester = WebSocketStreamTester(args.market_ticker)
    
    try:
        await tester.run_test()
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
        tester.shutdown_requested = True
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        logger.error(f"Unexpected error: {e}")
    finally:
        # Ensure cleanup
        tester.running = False

if __name__ == "__main__":
    args = parse_arguments()
    
    print("🧪 Kalshi WebSocket Streaming Test")
    print(f"This will connect to Kalshi WebSocket and stream messages for market: {args.market_ticker}")
    print("Press Ctrl+C to stop the test")
    print()
    
    try:
        # Set up signal handling for the main process
        def signal_handler(signum, frame):
            print(f"\n🛑 Received signal {signum}. Shutting down...")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Test interrupted by user")
    except Exception as e:
        print(f"❌ Failed to run test: {e}")
        sys.exit(1)
