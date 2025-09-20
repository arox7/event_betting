"""
Kalshi WebSocket client for real-time data streaming.
"""
import asyncio
import json
import logging
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

from config import Config

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
            asyncio.create_task(self._listen())
            
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
    
    async def _send_subscription(self, subscription: Dict[str, Any]):
        """Send a subscription message."""
        if not self.ws:
            logger.error("WebSocket not connected")
            return
        
        try:
            message = json.dumps(subscription)
            await self.ws.send(message)
            logger.debug(f"Sent subscription: {message}")
        except Exception as e:
            logger.error(f"Failed to send subscription: {e}")
    
    def subscribe_orderbook_updates(self, market_tickers: List[str], callback: Optional[Callable] = None):
        """Subscribe to orderbook updates for specified markets."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_ticker": market_tickers[0] if market_tickers else None
            }
        }
        
        self.subscriptions.add(json.dumps(subscription))
        self._register_callback("orderbook_delta", callback)
        
        if self.running and self.ws:
            # Create task and store it to prevent garbage collection
            task = asyncio.create_task(self._send_subscription(subscription))
            # Don't await here since this is a sync method
    
    def subscribe_market_ticker(self, market_tickers: List[str], callback: Optional[Callable] = None):
        """Subscribe to ticker updates for specified markets."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"],
                "market_tickers": market_tickers
            }
        }
        
        self.subscriptions.add(json.dumps(subscription))
        self._register_callback("ticker", callback)
        
        if self.running and self.ws:
            # Create task and store it to prevent garbage collection
            task = asyncio.create_task(self._send_subscription(subscription))
            # Don't await here since this is a sync method
    
    def subscribe_public_trades(self, market_tickers: List[str], callback: Optional[Callable] = None):
        """Subscribe to public trade updates for specified markets."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["trades"],
                "market_ticker": market_tickers[0] if market_tickers else None
            }
        }
        
        self.subscriptions.add(json.dumps(subscription))
        self._register_callback("trades", callback)
        
        if self.running and self.ws:
            # Create task and store it to prevent garbage collection
            task = asyncio.create_task(self._send_subscription(subscription))
            # Don't await here since this is a sync method
    
    def subscribe_fills(self, callback: Optional[Callable] = None):
        """Subscribe to fills (trade confirmations) for authenticated user."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["fill"]
            }
        }
        
        self.subscriptions.add(json.dumps(subscription))
        self._register_callback("fill", callback)
        
        if self.running and self.ws:
            # Create task and store it to prevent garbage collection
            task = asyncio.create_task(self._send_subscription(subscription))
            # Don't await here since this is a sync method
    
    def subscribe_market_positions(self, callback: Optional[Callable] = None):
        """Subscribe to market positions updates for authenticated user."""
        msg_id = self._get_next_message_id()
        subscription = {
            "id": msg_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["market_positions"]
            }
        }
        
        self.subscriptions.add(json.dumps(subscription))
        self._register_callback("market_positions", callback)
        
        if self.running and self.ws:
            # Create task and store it to prevent garbage collection
            task = asyncio.create_task(self._send_subscription(subscription))
            # Don't await here since this is a sync method
    
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
                logger.info(f"Subscribed to {channel} with SID {sid}")
                if channel:
                    self.subscription_ids[channel] = sid
                    
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
                logger.error(f"WebSocket error: {error_msg}")
                
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
                await self._listen()
                return  # Successfully reconnected
            except Exception as e:
                logger.error(f"Reconnection attempt {self.reconnect_attempts} failed: {e}")
        
        if self.running:
            logger.error("Max reconnection attempts reached. WebSocket will not reconnect.")
    
    async def start(self):
        """Start the WebSocket client."""
        try:
            await self.connect()
            await self._listen()
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
            self.ws_client.subscribe_orderbook_updates(market_tickers)
            
            # Subscribe to market ticker updates
            self.ws_client.subscribe_market_ticker(market_tickers)
            
            # Subscribe to public trades
            self.ws_client.subscribe_public_trades(market_tickers)
            
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
    
    async def _async_subscribe_user_data(self):
        """Async method to subscribe to user data."""
        try:
            # Subscribe to fills
            self.ws_client.subscribe_fills()
            
            # Subscribe to market positions
            self.ws_client.subscribe_market_positions()
            
            logger.info("Subscribed to user data (fills, positions)")
        except Exception as e:
            logger.error(f"Error subscribing to user data: {e}")
    
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
    
    async def _async_subscribe_position_tickers(self, market_tickers: List[str]):
        """Async method to subscribe to position tickers."""
        try:
            self.ws_client.subscribe_market_ticker(market_tickers)
            logger.info(f"Subscribed to ticker updates for {len(market_tickers)} markets with positions")
        except Exception as e:
            logger.error(f"Error subscribing to position tickers: {e}")
    
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
