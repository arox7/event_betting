"""
Main entry point for the market making bot.
"""

import argparse
import asyncio
import contextlib
import logging
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add parent directory to path to import from kalshi module
sys.path.append(str(Path(__file__).parent.parent))

from config import Config, setup_logging
from kalshi.client import KalshiAPIClient
from kalshi.websocket import KalshiWebSocketClient
from kalshi.models import Order
from market_making.acadia import AcadiaStrategy
from market_making.acadia_listener import MarketListener
from market_making.market_types import MarketConfig, MarketState, PositionState, OrderIntent

logger = logging.getLogger(__name__)


class MarketMakingBot:
    """Main market making bot class."""
    
    def __init__(self, config: Config, market_configs: List[MarketConfig], dry_run: bool = False):
        self.config = config
        self.market_configs = market_configs
        self.dry_run = dry_run
        self.api_client = KalshiAPIClient(config)
        self.ws_client = KalshiWebSocketClient(config)
        self.current_positions: Dict[str, Any] = {}
        self.outstanding_orders: Dict[str, List[Order]] = {}
        self.strategy = AcadiaStrategy(self.market_configs, dry_run=dry_run)
        
        # Create listeners for each market
        self.listeners: Dict[str, MarketListener] = {}
        for market_config in market_configs:
            self.listeners[market_config.ticker] = MarketListener(
                market_config=market_config,
                ws_client=self.ws_client,
                api_client=self.api_client,
                strategy=self.strategy,
            )

        logger.info(f"Initialized bot for {len(market_configs)} markets (dry_run={dry_run})")

        # Get all tickers we're trading
        self.tickers = [market.ticker for market in self.market_configs]

    def _store_current_positions_initial(self):
        # Get current positions
        positions = self.api_client.get_active_positions_only()
        if positions and 'positions' in positions:
            for position in positions['positions']:
                ticker = position.get('ticker')
                if ticker in self.tickers:
                    self.current_positions[ticker] = position
                    logger.info(f"Current position for {ticker}: {position.get('position', 0)}")

    def _store_outstanding_orders_initial(self):
        # Get outstanding orders
        self.outstanding_orders = self.api_client.get_outstanding_orders_by_tickers(self.tickers)
        for ticker in self.tickers:
            for order in self.outstanding_orders[ticker]:
                logger.info(f"Found outstanding order for {order.ticker}: {order.action} {order.side} {order.remaining_count} @ {order.yes_price_dollars or order.no_price_dollars}")
        
    def initialize_state(self):
        """Initialize current positions and outstanding orders for all configured markets."""
        logger.info("Initializing bot state...")
        logger.info(f"Fetching state for tickers: {self.tickers}")
        
        self._store_current_positions_initial()
        self._store_outstanding_orders_initial()
        
        # Initialize the strategy with current state
        self.strategy.initialize(self.current_positions, self.outstanding_orders)
            
        logger.info("Bot state initialization complete")
    
    async def _event_loop(self, client_task: asyncio.Task) -> None:
        """Main message pump: pull outbound messages while underlying client stays alive."""
        logger.info("Starting message dispatch loop")
        while True:
            if client_task.done():
                if exc := client_task.exception():
                    logger.error("[WS] Client task ended with error: %s", exc, exc_info=True)
                else:
                    logger.warning("[WS] Client task ended gracefully; reconnecting")
                break
            
            # Get messages from the websocket client
            message = self.ws_client.get_messages(timeout=1.0)
            if message:
                await self._dispatch(message)
            await asyncio.sleep(0.01)
    
    async def _dispatch(self, envelope: Dict[str, Any]) -> None:
        """Dispatch a message to the appropriate listener."""
        channel = envelope.get("channel")
        payload = envelope.get("data", {})
        
        # Extract market ticker from payload
        msg_body = payload.get("msg") or payload
        market_ticker = msg_body.get("market_ticker")
        
        # For orderbook messages, ticker might be nested
        if not market_ticker and channel == "orderbook_delta":
            market_ticker = (msg_body.get("orderbook") or {}).get("market_ticker")
        
        # Dispatch to appropriate listener
        if market_ticker and market_ticker in self.listeners:
            await self.listeners[market_ticker].process_message(envelope)
        elif channel in ["fill", "market_positions"]:
            # These channels don't always have market_ticker, dispatch to all listeners
            for listener in self.listeners.values():
                await listener.process_message(envelope)
        else:
            logger.debug(f"[DISPATCH] No listener for ticker {market_ticker}, channel {channel}")
    
    async def run(self) -> None:
        """Run the bot with WebSocket connections and auto-reconnect."""
        backoff_seconds = 2
        
        while True:
            client_task: Optional[asyncio.Task] = None
            try:
                logger.info("[WS] Launching WebSocket client")
                
                async def client_runner() -> None:
                    await self.ws_client.start()
                
                client_task = asyncio.create_task(client_runner())
                
                # Give the client a moment to establish the connection
                while not self.ws_client.running and not client_task.done():
                    await asyncio.sleep(0.1)
                
                if not self.ws_client.running:
                    if client_task.done() and client_task.exception():
                        raise client_task.exception()
                    logger.warning("[WS] Client failed to start; retrying in %ss", backoff_seconds)
                    await asyncio.sleep(backoff_seconds)
                    continue
                
                logger.info("[WS] Connection established")
                
                # Subscribe to channels for all markets
                await self._subscribe_to_markets()
                
                # Start the event loop
                await self._event_loop(client_task)
                
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[WS] Run-loop error: %s", exc, exc_info=True)
                await asyncio.sleep(backoff_seconds)
            finally:
                self.ws_client.stop()
                if client_task:
                    with contextlib.suppress(Exception):
                        await client_task
                logger.info("[WS] Client stopped")
    
    async def _subscribe_to_markets(self) -> None:
        """Subscribe to all necessary channels for our markets."""
        logger.info(f"Subscribing to market data channels for {len(self.tickers)} markets: {self.tickers}")
        
        # Subscribe to orderbook and trades for all markets
        # (First ticker subscribes, additional tickers update the subscription)
        await self.ws_client.subscribe_orderbook_updates(self.tickers)
        logger.info(f"Subscribed to orderbook for all markets")
        
        await self.ws_client.subscribe_public_trades(self.tickers)
        logger.info(f"Subscribed to trades for all markets")
        
        # Subscribe to ticker channel (supports multiple tickers in one subscription)
        await self.ws_client.subscribe_market_ticker(self.tickers)
        logger.info(f"Subscribed to ticker for all markets")
        
        # Subscribe to private channels (fills, positions)
        await self.ws_client.subscribe_fills()
        await self.ws_client.subscribe_market_positions()
        logger.info("Subscribed to private data channels (fills, positions)")
        
        logger.info("All subscriptions complete")

def load_config_file(config_path: str) -> tuple[List[MarketConfig], bool]:
    """
    Load market configurations and settings from YAML file.
    
    Returns:
        Tuple of (market_configs, dry_run)
    """
    try:
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)
            
        # Parse market configurations
        market_configs = []
        for market_data in data.get('markets', []):
            config = MarketConfig(
                ticker=market_data['ticker'],
                side=market_data['side'],
                position_limit=market_data['position_limit'],
                min_spread_cents=market_data.get('min_spread_cents', 2),
                min_price_delta_cents=market_data.get('min_price_delta_cents', 1),
                min_quote_time_seconds=market_data.get('min_quote_time_seconds', 1.0),
                exit_edge_threshold_cents=market_data.get('exit_edge_threshold_cents', 2)
            )
            market_configs.append(config)
        
        # Get dry_run setting
        dry_run = data.get('dry_run', False)
            
        logger.info(f"Loaded {len(market_configs)} market configurations (dry_run={dry_run})")
        return market_configs, dry_run
        
    except Exception as e:
        logger.error(f"Error loading config from {config_path}: {e}")
        return [], False

async def async_main():
    """Async main function."""
    parser = argparse.ArgumentParser(description="Market making bot")
    parser.add_argument(
        "--config", 
        type=str, 
        default=str(Path(__file__).parent / "config.yaml"), 
        help="Path to config file"
    )
    args = parser.parse_args()

    # Set up logging
    setup_logging(level=logging.INFO, include_filename=True)
    logger.info("Starting market making bot")
    
    # Load config file (YAML) - loads market configs and dry_run setting
    market_configs, dry_run = load_config_file(args.config)
    if not market_configs:
        logger.error("No market configurations loaded. Exiting.")
        return
    
    # Initialize Kalshi API config and bot
    config = Config()
    bot = MarketMakingBot(config, market_configs, dry_run=dry_run)
    
    # Initialize bot state (positions and orders)
    bot.initialize_state()
    
    logger.info("Market making bot initialization complete - starting event loop")
    
    try:
        # Run the bot (this will loop until interrupted)
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")
    except Exception as exc:
        logger.error(f"Bot error: {exc}", exc_info=True)
        raise
    finally:
        # Cleanup: disconnect websocket
        if bot.ws_client:
            await bot.ws_client.disconnect()
        logger.info("Market making bot shutdown complete")

def main():
    """Main entry point that runs the async main function."""
    asyncio.run(async_main())

if __name__ == "__main__":
    main()