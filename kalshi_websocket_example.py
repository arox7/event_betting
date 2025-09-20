#!/usr/bin/env python3
"""
Kalshi WebSocket Example - Based on official documentation
https://docs.kalshi.com/getting_started/quick_start_websockets
"""
import asyncio
import base64
import json
import time
import websockets
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
import argparse
import sys

from config import Config

def sign_pss_text(private_key, text: str) -> str:
    """Sign message using RSA-PSS"""
    message = text.encode('utf-8')
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode('utf-8')

def create_headers(private_key, method: str, path: str, api_key_id: str) -> dict:
    """Create authentication headers"""
    timestamp = str(int(time.time() * 1000))
    msg_string = timestamp + method + path.split('?')[0]
    signature = sign_pss_text(private_key, msg_string)

    return {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }

async def orderbook_websocket(market_ticker: str, config: Config):
    """Connect to WebSocket and subscribe to orderbook"""
    # Get WebSocket URL based on demo mode
    if config.KALSHI_DEMO_MODE:
        ws_url = "wss://demo-api.kalshi.co/trade-api/ws/v2"
    else:
        ws_url = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    
    print(f"🔌 Connecting to: {ws_url}")
    print(f"🎯 Market: {market_ticker}")
    print(f"🔑 Demo Mode: {config.KALSHI_DEMO_MODE}")
    print()
    
    # Load private key
    try:
        with open(config.KALSHI_PRIVATE_KEY_PATH, 'rb') as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None
            )
    except Exception as e:
        print(f"❌ Error loading private key: {e}")
        return

    # Create WebSocket headers
    ws_headers = create_headers(private_key, "GET", "/trade-api/ws/v2", config.KALSHI_API_KEY_ID)
    print(f"🔑 Using API Key: {config.KALSHI_API_KEY_ID[:8]}...")
    print()

    try:
        async with websockets.connect(ws_url, additional_headers=ws_headers) as websocket:
            print(f"✅ Connected! Subscribing to orderbook for {market_ticker}")

            # Subscribe to multiple channels for comprehensive market data
            subscriptions = [
                # {
                #     "id": 1,
                #     "cmd": "subscribe",
                #     "params": {
                #         "channels": ["orderbook_delta"],
                #         "market_ticker": market_ticker
                #     }
                # },
                # {
                #     "id": 2,
                #     "cmd": "subscribe",
                #     "params": {
                #         "channels": ["ticker"],
                #         "market_ticker": market_ticker
                #     }
                # },
                # Note: public_trades may not be available for all markets
                # Uncomment below to try public trades subscription
                # {
                #     "id": 3,
                #     "cmd": "subscribe",
                #     "params": {
                #         "channels": ["trade"],
                #         "market_tickers": [market_ticker]
                #     }
                # },
                # Subscribe to user fills (requires authentication)
                {
                    "id": 4,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["fill"]
                    }
                },
                # Subscribe to market positions (requires authentication)
                {
                    "id": 5,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["market_positions"]
                    }
                }
            ]
            
            for subscription in subscriptions:
                await websocket.send(json.dumps(subscription))
                print(f"📡 Subscription sent: {subscription['params']['channels'][0]}")
            
            print("📡 All subscriptions sent...")
            print("🎧 Listening for messages... (Press Ctrl+C to stop)")
            print("=" * 80)

            # Process messages continuously
            message_count = 0
            last_message_time = time.time()
            status_time = time.time()
            
            async for message in websocket:
                message_count += 1
                last_message_time = time.time()
                
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    
                    # Format timestamp
                    timestamp = time.strftime("%H:%M:%S")
                    
                    # Debug info (uncomment for troubleshooting)
                    # print(f"🔍 DEBUG: Received message #{message_count} at {timestamp}")
                    # print(f"🔍 Message type: {msg_type}")
                    # print(f"🔍 Raw message length: {len(message)} chars")
                    
                except json.JSONDecodeError as e:
                    print(f"❌ JSON decode error: {e}")
                    print(f"❌ Raw message: {message[:200]}...")
                    continue

                if msg_type == "subscribed":
                    print(f"✅ Subscribed: {data}")
                    print("🎧 Listening for messages... (Press Ctrl+C to stop)")
                    print("=" * 80)

                elif msg_type == "orderbook_snapshot":
                    market = data.get('msg', {}).get('market_ticker', 'Unknown')
                    print(f"📊 [{timestamp}] #{message_count} Orderbook snapshot for {market}")
                    
                    # Debug: Show the full raw data
                    print(f"🔍 RAW SNAPSHOT DATA:")
                    print(f"   {json.dumps(data, indent=2)}")
                    
                    # Show orderbook data in a readable format
                    msg_data = data.get('msg', {})
                    yes_orders = msg_data.get('yes', [])
                    no_orders = msg_data.get('no', [])
                    
                    print(f"🔍 YES orders found: {len(yes_orders) if yes_orders else 0}")
                    print(f"🔍 NO orders found: {len(no_orders) if no_orders else 0}")
                    
                    # Check if market might be closed/suspended
                    market_id = msg_data.get('market_id', '')
                    if not market_id:
                        print("⚠️  WARNING: Market ID is empty - market might be closed or suspended")
                    
                    if not yes_orders and not no_orders:
                        print("⚠️  WARNING: Empty orderbook - market might be:")
                        print("   • Closed or suspended")
                        print("   • Very new with no orders yet")
                        print("   • Paused for maintenance")
                    
                    if yes_orders:
                        print(f"   YES Orders: {len(yes_orders)} levels")
                        for i, order in enumerate(yes_orders[:3]):  # Show top 3
                            price = order[0] / 100 if len(order) > 0 else 0
                            size = order[1] if len(order) > 1 else 0
                            print(f"     ${price:.2f} x {size}")
                    else:
                        print("   ❌ No YES orders in snapshot")
                    
                    if no_orders:
                        print(f"   NO Orders: {len(no_orders)} levels")
                        for i, order in enumerate(no_orders[:3]):  # Show top 3
                            price = order[0] / 100 if len(order) > 0 else 0
                            size = order[1] if len(order) > 1 else 0
                            print(f"     ${price:.2f} x {size}")
                    else:
                        print("   ❌ No NO orders in snapshot")
                    print("-" * 80)

                elif msg_type == "orderbook_delta":
                    # Try different data structures for orderbook delta
                    delta_data = data.get('data', {}) or data.get('msg', {})
                    market = delta_data.get('market_ticker', 'Unknown')
                    print(f"📈 [{timestamp}] #{message_count} Orderbook update for {market}")
                    
                    # Show delta information
                    if 'client_order_id' in delta_data:
                        print(f"   🔄 Your order {delta_data['client_order_id']} caused this change")
                    
                    # Show changes in readable format
                    yes_changes = delta_data.get('yes', [])
                    no_changes = delta_data.get('no', [])
                    
                    if yes_changes:
                        print(f"   YES Changes: {len(yes_changes)} updates")
                        for change in yes_changes[:2]:  # Show first 2 changes
                            if len(change) >= 3:
                                action = "ADD" if change[2] == "add" else "REMOVE" if change[2] == "remove" else "UPDATE"
                                price = change[0] / 100
                                size = change[1]
                                print(f"     {action}: ${price:.2f} x {size}")
                    elif yes_changes is not None:
                        print(f"   YES Changes: {len(yes_changes)} updates (empty)")
                    
                    if no_changes:
                        print(f"   NO Changes: {len(no_changes)} updates")
                        for change in no_changes[:2]:  # Show first 2 changes
                            if len(change) >= 3:
                                action = "ADD" if change[2] == "add" else "REMOVE" if change[2] == "remove" else "UPDATE"
                                price = change[0] / 100
                                size = change[1]
                                print(f"     {action}: ${price:.2f} x {size}")
                    elif no_changes is not None:
                        print(f"   NO Changes: {len(no_changes)} updates (empty)")
                    
                    # Debug: Show raw orderbook delta data
                    print(f"🔍 RAW ORDERBOOK DELTA:")
                    print(f"   {json.dumps(data, indent=2)}")
                    print("-" * 80)

                elif msg_type == "ticker":
                    # Try different data structures for ticker
                    ticker_data = data.get('data', {}) or data.get('msg', {})
                    market = ticker_data.get('market_ticker', 'Unknown')
                    bid = ticker_data.get('bid', 0)
                    ask = ticker_data.get('ask', 0)
                    
                    # Convert from cents to dollars if needed
                    if bid > 100:  # If it looks like cents
                        bid = bid / 100
                    if ask > 100:  # If it looks like cents
                        ask = ask / 100
                    
                    print(f"💹 [{timestamp}] #{message_count} Ticker update for {market}")
                    print(f"   Bid: ${bid:.2f} | Ask: ${ask:.2f} | Spread: ${ask-bid:.2f}")
                    
                    # Debug: Show raw ticker data
                    print(f"🔍 RAW TICKER DATA:")
                    print(f"   {json.dumps(data, indent=2)}")
                    print("-" * 80)

                elif msg_type == "trade":
                    # Trade data is in the 'msg' field
                    trade_data = data.get('msg', {})
                    market = trade_data.get('market_ticker', 'Unknown')
                    taker_side = trade_data.get('taker_side', 'unknown')
                    count = trade_data.get('count', 0)
                    yes_price_dollars = trade_data.get('yes_price_dollars', '0.00')
                    no_price_dollars = trade_data.get('no_price_dollars', '0.00')
                    
                    print(f"💰 [{timestamp}] #{message_count} Trade on {market}")
                    print(f"   {taker_side.upper()} TAKER: {count} shares")
                    print(f"   YES Price: ${yes_price_dollars} | NO Price: ${no_price_dollars}")
                    
                    # Show trade ID for reference
                    trade_id = trade_data.get('trade_id', 'N/A')
                    print(f"   Trade ID: {trade_id[:8]}...")
                    print("-" * 80)

                elif msg_type == "fill":
                    # Fill data is in the 'msg' field
                    fill_data = data.get('msg', {})
                    market = fill_data.get('market_ticker', 'Unknown')
                    order_id = fill_data.get('order_id', 'N/A')
                    trade_id = fill_data.get('trade_id', 'N/A')
                    side = fill_data.get('side', 'unknown')
                    action = fill_data.get('action', 'unknown')
                    count = fill_data.get('count', 0)
                    yes_price = fill_data.get('yes_price', 0)
                    no_price = fill_data.get('no_price', 0)
                    is_taker = fill_data.get('is_taker', False)
                    post_position = fill_data.get('post_position', 0)
                    
                    print(f"🎯 [{timestamp}] #{message_count} YOUR FILL on {market}")
                    print(f"   Order ID: {order_id[:8]}... | Trade ID: {trade_id[:8]}...")
                    print(f"   {action.upper()} {side.upper()}: {count} shares")
                    print(f"   YES Price: ${yes_price/100:.2f} | NO Price: ${no_price/100:.2f}")
                    print(f"   Taker: {is_taker} | New Position: {post_position}")
                    print("-" * 80)

                elif msg_type == "market_position":
                    # Market position data is in the 'msg' field
                    pos_data = data.get('msg', {})
                    market = pos_data.get('market_ticker', 'Unknown')
                    position = pos_data.get('position', 0)
                    position_cost = pos_data.get('position_cost', 0) / 10000  # Convert from centi-cents
                    realized_pnl = pos_data.get('realized_pnl', 0) / 10000   # Convert from centi-cents
                    fees_paid = pos_data.get('fees_paid', 0) / 10000         # Convert from centi-cents
                    volume = pos_data.get('volume', 0)
                    
                    print(f"📊 [{timestamp}] #{message_count} POSITION UPDATE for {market}")
                    print(f"   Position: {position} shares")
                    print(f"   Cost: ${position_cost:.2f} | P&L: ${realized_pnl:.2f}")
                    print(f"   Fees Paid: ${fees_paid:.2f} | Volume: {volume}")
                    print("-" * 80)

                elif msg_type == "error":
                    error_data = data.get('msg', {})
                    error_code = error_data.get('code', 'Unknown')
                    error_msg = error_data.get('msg', 'Unknown error')
                    print(f"❌ [{timestamp}] #{message_count} Error {error_code}: {error_msg}")
                    print("-" * 80)
                    
                else:
                    print(f"📨 [{timestamp}] #{message_count} Unknown message type: {msg_type}")
                    # Show first 200 chars of data for debugging
                    data_str = json.dumps(data, indent=2)
                    if len(data_str) > 200:
                        data_str = data_str[:200] + "..."
                    print(f"   {data_str}")
                    print("-" * 80)
                
                # Show status every 60 seconds
                if time.time() - status_time > 60:
                    print(f"💓 Status: {message_count} messages received, connection alive")
                    status_time = time.time()
                
                # Check for timeout (no messages for 30 seconds)
                if time.time() - last_message_time > 30:
                    print(f"⏰ No messages received for 30 seconds. Checking connection...")
                    last_message_time = time.time()

    except KeyboardInterrupt:
        print(f"\n🛑 Interrupted by user after {message_count} messages")
        print("👋 Goodbye!")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        print(f"❌ Connection may have been lost or market may be inactive")

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Kalshi WebSocket Example - Official Documentation Implementation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 kalshi_websocket_example.py KXHARRIS24-LSV
  python3 kalshi_websocket_example.py KXFUT24-LSV
  python3 kalshi_websocket_example.py KXELECTION-24
        """
    )
    
    parser.add_argument(
        'market_ticker',
        help='Market ticker to monitor (e.g., KXHARRIS24-LSV)'
    )
    
    return parser.parse_args()

async def main():
    """Main function."""
    args = parse_arguments()
    
    print("🧪 Kalshi WebSocket Example - Complete Market Data Stream")
    print("Based on official documentation: https://docs.kalshi.com/getting_started/quick_start_websockets")
    print(f"Market: {args.market_ticker}")
    print()
    print("📡 Subscribing to:")
    print("   • User Fills (your order executions)")
    print("   • Market Positions (your portfolio updates)")
    print("   • All authenticated channels require valid API credentials")
    print()
    print("💡 Tips for more active markets:")
    print("   • Try popular markets like: KXHARRIS24-LSV, KXFUT24-LSV, KXELECTION-24")
    print("   • Markets are more active during business hours (9 AM - 5 PM EST)")
    print("   • Some markets may have low activity - this is normal!")
    print()
    
    # Load configuration
    config = Config()
    
    if not config.KALSHI_API_KEY_ID or not config.KALSHI_PRIVATE_KEY_PATH:
        print("❌ Error: Missing API credentials")
        print("Please set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in your .env file")
        sys.exit(1)
    
    try:
        await orderbook_websocket(args.market_ticker, config)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
