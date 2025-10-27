"""
Kalshi WebSocket client for real-time data streaming.
"""
import asyncio
import json
import logging
import contextlib
import websockets
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
import threading
import queue
import time
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from config import Config, setup_logging

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

class KalshiWebSocketClient:
    """WebSocket client for Kalshi real-time data streaming."""
    
    def __init__(self, config: Config):
        """Initialize the WebSocket client."""
        self.config = config
        self.ws_url = self._get_websocket_url()
        self.ws = None
        self.running = False
        self.subscriptions = set()
        self.message_queue = queue.Queue()
        self.callbacks = {}
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 2
        self.message_id_counter = 0
        self.subscription_ids = {}  # Track subscription IDs
        self.subscribed_channels = set()  # Track which channels are already subscribed
        self.pending_subscriptions = {}  # Map msg_id to subscription details for debugging
        self._listener_task = None
        
        # Initialize private key for authentication
        self._private_key = None
        self._load_private_key()
        
    def _get_websocket_url(self) -> str:
        """Get the appropriate WebSocket URL based on demo mode."""
        if self.config.KALSHI_DEMO_MODE:
            return "wss://demo-api.kalshi.co/trade-api/ws/v2"
        else:
            return "wss://api.elections.kalshi.com/trade-api/ws/v2"
    
    def _load_private_key(self):
        """Load the private key for authentication."""
        if not self.config.KALSHI_PRIVATE_KEY_PATH:
            logger.warning("No private key path provided - WebSocket authentication will not work")
            return
            
        try:
            with open(self.config.KALSHI_PRIVATE_KEY_PATH, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(), 
                    password=None, 
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            self._private_key = None
    
    def _create_signature(self, timestamp: str, method: str, path: str) -> str:
        """Create the request signature for Kalshi API authentication."""
        if not self._private_key:
            raise Exception("Private key not loaded")
            
        message = f"{timestamp}{method}{path}".encode('utf-8')
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode('utf-8')
    
    def _get_next_message_id(self) -> int:
        """Get the next message ID for commands."""
        self.message_id_counter += 1
        return self.message_id_counter
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for WebSocket connection."""
        if not (self.config.KALSHI_API_KEY_ID and self._private_key):
            logger.warning("No API credentials provided - WebSocket will not work for authenticated endpoints")
            return {}
        
        try:
            # Create timestamp
            timestamp = str(int(time.time() * 1000))
            
            # Create signature using the same method as REST API
            path = "/trade-api/ws/v2"
            signature = self._create_signature(timestamp, "GET", path)
            
            return {
                "KALSHI-ACCESS-KEY": self.config.KALSHI_API_KEY_ID,
                "KALSHI-ACCESS-SIGNATURE": signature,
                "KALSHI-ACCESS-TIMESTAMP": timestamp
            }
        except Exception as e:
            logger.error(f"Failed to create auth headers: {e}")
            return {}
    
    async def connect(self):
        """Connect to the Kalshi WebSocket."""
        try:
            auth_headers = self._get_auth_headers()
            
            logger.info(f"Connecting to Kalshi WebSocket: {self.ws_url}")
            logger.info(f"Using headers: {auth_headers}")
            
            self.ws = await websockets.connect(
                self.ws_url,
                additional_headers=auth_headers,  # Can pass dict directly
                ping_interval=20,
                ping_timeout=10
            )
            
            self.running = True
            self.reconnect_attempts = 0
            logger.info("Successfully connected to Kalshi WebSocket")
            
            # Start the message listener
            self._listener_task = asyncio.create_task(self._listen())
            
            # Resubscribe to all previous subscriptions
            for subscription in self.subscriptions:
                await self._send_subscription(json.loads(subscription))
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            raise
    
    async def disconnect(self):
        """Disconnect from the WebSocket."""
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from Kalshi WebSocket")
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(Exception):
                await self._listener_task
            self._listener_task = None
    
    async def _send_subscription(self, subscription: Dict[str, Any]):
        """Send a subscription message."""
        if not self.ws:
            logger.error("WebSocket not connected")
            return
        
        try:
            message = json.dumps(subscription)
            # Extract details for better logging
            params = subscription.get("params", {})
            channels = params.get("channels", [])
            ticker = params.get("market_ticker") or params.get("market_tickers", "N/A")
            msg_id = subscription.get('id')
            
            # Track this subscription for debugging
            self.pending_subscriptions[msg_id] = {
                "channels": channels,
                "ticker": ticker,
                "sent_at": datetime.now(timezone.utc)
            }
            
            logger.info(f"[SUBSCRIBE] Sending: channels={channels}, ticker={ticker}, msg_id={msg_id}")
            
            await self.ws.send(message)
            logger.debug(f"[SUBSCRIBE] Full message: {message}")
        except Exception as e:
            logger.error(f"[SUBSCRIBE] Failed to send: {e}")
    
    async def _update_subscription(self, channel: str, market_tickers: List[str]):
        """Update an existing subscription to add more tickers."""
        if not self.ws or not self.running:
            logger.warning("WebSocket not connected, cannot update subscription")
            return
        
        if not market_tickers:
            return
        
        # Get the SID for this channel
        sid = self.subscription_ids.get(channel)
        if not sid:
            logger.warning(f"No SID found for channel {channel}, cannot update")
            return
        
        # Send update command with all tickers at once
        msg_id = self._get_next_message_id()
        update_msg = {
            "id": msg_id,
            "cmd": "update_subscription",
            "params": {
                "sids": [sid],
                "market_tickers": market_tickers,
                "action": "add_markets"
            }
        }
        
        logger.info(f"[UPDATE] Adding {len(market_tickers)} tickers to {channel} (SID={sid}): {market_tickers}")
        
        try:
            message = json.dumps(update_msg)
            await self.ws.send(message)
        except Exception as e:
            logger.error(f"[UPDATE] Failed to update subscription: {e}")
    
    async def subscribe_orderbook_updates(self, market_tickers: List[str], callback: Optional[Callable] = None):
        """Subscribe to orderbook updates for specified markets.
        
        Kalshi's WebSocket API allows only one subscription per channel.
        For the first ticker, we subscribe. For additional tickers, we update the subscription.
        """
        if not market_tickers:
            logger.warning("No market tickers provided for orderbook subscription")
            return
            
        self._register_callback("orderbook_delta", callback)
        
        # Check if we already have an orderbook subscription
        channel_key = "orderbook_delta"
        has_subscription = channel_key in self.subscribed_channels
        
        if not has_subscription:
            # First subscription - subscribe to the first ticker
            ticker = market_tickers[0]
            msg_id = self._get_next_message_id()
            subscription = {
                "id": msg_id,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta"],
                    "market_ticker": ticker
                }
            }
            
            self.subscribed_channels.add(channel_key)
            
            if self.running and self.ws:
                await self._send_subscription(subscription)
            else:
                self.subscriptions.add(json.dumps(subscription))
            
            # If there are more tickers, update the subscription
            if len(market_tickers) > 1:
                await asyncio.sleep(0.2)  # Wait for subscription to complete
                await self._update_subscription("orderbook_delta", market_tickers[1:])
        else:
            # Already subscribed, just update with new tickers
            await self._update_subscription("orderbook_delta", market_tickers)
    
    async def subscribe_market_ticker(self, market_tickers: List[str], callback: Optional[Callable] = None):
        """Subscribe to ticker updates for specified markets.
        
        Note: The ticker channel supports subscribing to multiple markets in a single
        subscription request (unlike orderbook and trade channels).
        """
        if not market_tickers:
            logger.warning("No market tickers provided for ticker subscription")
            return
            
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],
                "market_tickers": market_tickers
            }
        }
        
        self._register_callback("ticker", callback)
        
        # Send immediately if connected, otherwise store for later
        if self.running and self.ws:
            await self._send_subscription(subscription)
        else:
            self.subscriptions.add(json.dumps(subscription))
    
    async def subscribe_public_trades(self, market_tickers: List[str], callback: Optional[Callable] = None):
        """Subscribe to public trade updates for specified markets.
        
        Kalshi's WebSocket API allows only one subscription per channel.
        For the first ticker, we subscribe. For additional tickers, we update the subscription.
        """
        if not market_tickers:
            logger.warning("No market tickers provided for trade subscription")
            return
            
        self._register_callback("trade", callback)
        
        # Check if we already have a trade subscription
        channel_key = "trade"
        has_subscription = channel_key in self.subscribed_channels
        
        if not has_subscription:
            # First subscription - subscribe to the first ticker
            ticker = market_tickers[0]
            msg_id = self._get_next_message_id()
            subscription = {
                "id": msg_id,
                "cmd": "subscribe",
                "params": {
                    "channels": ["trade"],
                    "market_ticker": ticker
                }
            }
            
            self.subscribed_channels.add(channel_key)
            
            if self.running and self.ws:
                await self._send_subscription(subscription)
            else:
                self.subscriptions.add(json.dumps(subscription))
            
            # If there are more tickers, update the subscription
            if len(market_tickers) > 1:
                await asyncio.sleep(0.2)  # Wait for subscription to complete
                await self._update_subscription("trade", market_tickers[1:])
        else:
            # Already subscribed, just update with new tickers
            await self._update_subscription("trade", market_tickers)
    
    async def subscribe_fills(self, callback: Optional[Callable] = None):
        """Subscribe to fills (trade confirmations) for authenticated user."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["fill"]
            }
        }
        
        self._register_callback("fill", callback)
        
        # Send immediately if connected, otherwise store for later
        if self.running and self.ws:
            await self._send_subscription(subscription)
        else:
            self.subscriptions.add(json.dumps(subscription))
    
    async def subscribe_market_positions(self, callback: Optional[Callable] = None):
        """Subscribe to market positions updates for authenticated user."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["market_positions"]
            }
        }
        
        self._register_callback("market_positions", callback)
        
        # Send immediately if connected, otherwise store for later
        if self.running and self.ws:
            await self._send_subscription(subscription)
        else:
            self.subscriptions.add(json.dumps(subscription))
    
    def _register_callback(self, channel: str, callback: Optional[Callable]):
        """Register a callback for a specific channel."""
        if callback:
            if channel not in self.callbacks:
                self.callbacks[channel] = []
            self.callbacks[channel].append(callback)
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            # Handle different message types according to Kalshi API
            if msg_type == "subscribed":
                # Subscription confirmation
                msg = data.get("msg", {})
                channel = msg.get("channel")
                sid = msg.get("sid")
                market_ticker = msg.get("market_ticker", "N/A")
                msg_id = data.get("id", "unknown")
                logger.info(f"[SUBSCRIBED] msg_id={msg_id}, channel={channel}, ticker={market_ticker}, SID={sid}")
                if channel:
                    self.subscription_ids[channel] = sid
                
                # Clean up pending subscription
                self.pending_subscriptions.pop(msg_id, None)
                    
            elif msg_type == "unsubscribed":
                # Unsubscription confirmation
                sid = data.get("sid")
                logger.info(f"Unsubscribed from SID {sid}")
                
            elif msg_type == "ok":
                # Update subscription confirmation
                logger.info(f"Subscription updated: {data.get('market_tickers', [])}")
                
            elif msg_type == "error":
                # Error response
                error_msg = data.get("msg", {})
                error_code = error_msg.get("code") if isinstance(error_msg, dict) else None
                msg_id = data.get("id", "unknown")
                
                # Look up what subscription this error is for
                sub_info = self.pending_subscriptions.get(msg_id, {})
                channels = sub_info.get("channels", "unknown")
                ticker = sub_info.get("ticker", "unknown")
                
                logger.error(
                    f"[WS ERROR] msg_id={msg_id}, channels={channels}, ticker={ticker}, "
                    f"code={error_code}, error={error_msg}"
                )
                
                # Clean up pending subscription
                self.pending_subscriptions.pop(msg_id, None)
                
            else:
                # This should be actual data messages
                # Extract channel from the message structure
                channel = None
                message_type = msg_type
                
                # Try to determine channel from message structure based on guide examples
                if msg_type == "orderbook_snapshot" or msg_type == "orderbook_delta":
                    channel = "orderbook_delta"
                elif msg_type == "ticker":
                    channel = "ticker"
                elif msg_type == "trade":
                    channel = "trade"
                elif msg_type == "fill":
                    channel = "fill"
                elif msg_type == "market_position":
                    channel = "market_positions"
                
                logger.debug(f"Received {channel} message: {message_type}")
                
                # Add to message queue for external processing
                self.message_queue.put({
                    'timestamp': datetime.now(timezone.utc),
                    'channel': channel,
                    'message_type': message_type,
                    'data': data
                })
                
                # Call registered callbacks
                if channel and channel in self.callbacks:
                    for callback in self.callbacks[channel]:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(data)
                            else:
                                callback(data)
                        except Exception as e:
                            logger.error(f"Error in callback for {channel}: {e}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WebSocket message: {e}")
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
    
    async def _listen(self):
        """Listen for WebSocket messages."""
        try:
            while self.running:
                try:
                    # Use timeout to allow periodic checks of self.running
                    message = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
                    await self._handle_message(message)
                except asyncio.TimeoutError:
                    # Timeout is normal, just continue checking
                    continue
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("WebSocket connection closed")
                    break
                except Exception as e:
                    logger.error(f"Error receiving message: {e}")
                    break
        except Exception as e:
            logger.error(f"Error in WebSocket listener: {e}")
    
    async def _reconnect(self):
        """Attempt to reconnect to the WebSocket."""
        while self.reconnect_attempts < self.max_reconnect_attempts and self.running:
            self.reconnect_attempts += 1
            delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1))  # Exponential backoff
            
            logger.info(f"Attempting to reconnect in {delay} seconds (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
            await asyncio.sleep(delay)
            
            try:
                await self.connect()
                if self._listener_task:
                    await self._listener_task
                return  # Successfully reconnected
            except Exception as e:
                logger.error(f"Reconnection attempt {self.reconnect_attempts} failed: {e}")
        
        if self.running:
            logger.error("Max reconnection attempts reached. WebSocket will not reconnect.")
    
    async def start(self):
        """Start the WebSocket client."""
        try:
            await self.connect()
            if self._listener_task:
                await self._listener_task
        except Exception as e:
            logger.error(f"WebSocket client error: {e}")
            if self.running:
                await self._reconnect()
    
    def stop(self):
        """Stop the WebSocket client."""
        self.running = False
        if self.ws:
            asyncio.create_task(self.disconnect())
    
    def get_messages(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Get messages from the queue (non-blocking)."""
        try:
            return self.message_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_all_messages(self) -> List[Dict[str, Any]]:
        """Get all messages from the queue."""
        messages = []
        while True:
            try:
                message = self.message_queue.get_nowait()
                messages.append(message)
            except queue.Empty:
                break
        return messages


class WebSocketManager:
    """Manager for WebSocket connections in a Streamlit context."""
    
    def __init__(self, config: Config):
        """Initialize the WebSocket manager."""
        self.config = config
        self.ws_client = KalshiWebSocketClient(config)
        self.loop = None
        self.thread = None
        self.running = False
        
    def start(self):
        """Start the WebSocket manager in a separate thread."""
        if self.running:
            logger.warning("WebSocket manager already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_websocket_loop, daemon=True)
        self.thread.start()
        logger.info("WebSocket manager started")
    
    def stop(self):
        """Stop the WebSocket manager."""
        self.running = False
        self.ws_client.stop()
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("WebSocket manager stopped")
    
    def _run_websocket_loop(self):
        """Run the WebSocket event loop in a separate thread."""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            # Connect and run the WebSocket client
            self.loop.run_until_complete(self._async_websocket_loop())
            
        except Exception as e:
            logger.error(f"WebSocket loop error: {e}")
        finally:
            if self.loop:
                self.loop.close()
    
    async def _async_websocket_loop(self):
        """Async WebSocket loop."""
        try:
            await self.ws_client.connect()
            # Keep the connection alive
            while self.running:
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Async WebSocket error: {e}")
            self.running = False
    
    def subscribe_to_market_data(self, market_tickers: List[str]):
        """Subscribe to market data for specified tickers."""
        if not self.running or not self.loop:
            logger.warning("WebSocket manager not running or no event loop")
            return
        
        # Schedule async subscription
        asyncio.run_coroutine_threadsafe(
            self._async_subscribe_market_data(market_tickers), 
            self.loop
        )
        logger.info(f"Scheduled subscription to market data for {len(market_tickers)} tickers")
    
    async def _async_subscribe_market_data(self, market_tickers: List[str]):
        """Async method to subscribe to market data."""
        try:
            # Subscribe to orderbook updates
            await self.ws_client.subscribe_orderbook_updates(market_tickers)
            
            # Subscribe to market ticker updates
            await self.ws_client.subscribe_market_ticker(market_tickers)
            
            # Subscribe to public trades
            await self.ws_client.subscribe_public_trades(market_tickers)
            
            logger.info(f"Subscribed to market data for {len(market_tickers)} tickers")
        except Exception as e:
            logger.error(f"Error subscribing to market data: {e}")
    
    def subscribe_to_user_data(self):
        """Subscribe to user-specific data (fills, positions)."""
        if not self.running or not self.loop:
            logger.warning("WebSocket manager not running or no event loop")
            return
        
        # Schedule async subscription
        asyncio.run_coroutine_threadsafe(self._async_subscribe_user_data(), self.loop)
        logger.info("Scheduled subscription to user data (fills, positions)")
    
    def subscribe_to_user_data_with_callbacks(self, fill_callback=None, position_callback=None):
        """Subscribe to user-specific data (fills, positions) with event callbacks."""
        if not self.running or not self.loop:
            logger.warning("WebSocket manager not running or no event loop")
            return
        
        # Schedule async subscription with callbacks
        asyncio.run_coroutine_threadsafe(
            self._async_subscribe_user_data_with_callbacks(fill_callback, position_callback), 
            self.loop
        )
        logger.info("Scheduled subscription to user data with event callbacks")
    
    async def _async_subscribe_user_data(self):
        """Async method to subscribe to user data."""
        try:
            # Subscribe to fills
            await self.ws_client.subscribe_fills()
            
            # Subscribe to market positions
            await self.ws_client.subscribe_market_positions()
            
            logger.info("Subscribed to user data (fills, positions)")
        except Exception as e:
            logger.error(f"Error subscribing to user data: {e}")
    
    async def _async_subscribe_user_data_with_callbacks(self, fill_callback, position_callback):
        """Async method to subscribe to user data with callbacks."""
        try:
            # Subscribe to fills with callback
            await self.ws_client.subscribe_fills(fill_callback)
            
            # Subscribe to market positions with callback
            await self.ws_client.subscribe_market_positions(position_callback)
            
            logger.info("Subscribed to user data (fills, positions) with event callbacks")
        except Exception as e:
            logger.error(f"Error subscribing to user data with callbacks: {e}")
    
    def subscribe_to_position_tickers(self, market_tickers: List[str]):
        """Subscribe to ticker updates for markets with open positions."""
        if not self.running or not self.loop:
            logger.warning("WebSocket manager not running or no event loop")
            return
        
        # Subscribe to all tickers in a single subscription
        if market_tickers:
            # Schedule async subscription
            asyncio.run_coroutine_threadsafe(
                self._async_subscribe_position_tickers(market_tickers), 
                self.loop
            )
            logger.info(f"Scheduled subscription to ticker updates for {len(market_tickers)} markets with positions")
    
    def subscribe_to_position_tickers_with_callback(self, market_tickers: List[str], callback=None):
        """Subscribe to ticker updates for markets with open positions with event callback."""
        if not self.running or not self.loop:
            logger.warning("WebSocket manager not running or no event loop")
            return
        
        # Subscribe to all tickers in a single subscription with callback
        if market_tickers:
            # Schedule async subscription with callback
            asyncio.run_coroutine_threadsafe(
                self._async_subscribe_position_tickers_with_callback(market_tickers, callback), 
                self.loop
            )
            logger.info(f"Scheduled subscription to ticker updates with callback for {len(market_tickers)} markets with positions")
    
    async def _async_subscribe_position_tickers(self, market_tickers: List[str]):
        """Async method to subscribe to position tickers."""
        try:
            await self.ws_client.subscribe_market_ticker(market_tickers)
            logger.info(f"Subscribed to ticker updates for {len(market_tickers)} markets with positions")
        except Exception as e:
            logger.error(f"Error subscribing to position tickers: {e}")
    
    async def _async_subscribe_position_tickers_with_callback(self, market_tickers: List[str], callback):
        """Async method to subscribe to position tickers with callback."""
        try:
            await self.ws_client.subscribe_market_ticker(market_tickers, callback)
            logger.info(f"Subscribed to ticker updates with callback for {len(market_tickers)} markets with positions")
        except Exception as e:
            logger.error(f"Error subscribing to position tickers with callback: {e}")
    
    def get_recent_messages(self) -> List[Dict[str, Any]]:
        """Get recent WebSocket messages."""
        return self.ws_client.get_all_messages()
    
    def get_ticker_data(self) -> Dict[str, Dict[str, Any]]:
        """Get current ticker data for all subscribed markets."""
        messages = self.ws_client.get_all_messages()
        ticker_data = {}
        
        for message in messages:
            if message.get('channel') == 'ticker':
                data = message.get('data', {})
                market_ticker = data.get('market_ticker')
                if market_ticker:
                    ticker_data[market_ticker] = {
                        'bid': data.get('bid', 0),
                        'ask': data.get('ask', 0),
                        'last_price': data.get('last_price', 0),
                        'volume': data.get('volume', 0),
                        'timestamp': message.get('timestamp')
                    }
        
        return ticker_data
    
    def register_ticker_callback(self, callback):
        """Register a callback for ticker updates."""
        self.ws_client._register_callback("ticker", callback)
    
    def get_message(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        """Get a single message from the queue."""
        return self.ws_client.get_messages(timeout=timeout)
