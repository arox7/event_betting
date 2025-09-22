"""
Kalshi Market Making Bot - Single-sided market making with risk controls.

This bot implements a clever single-sided market making strategy that:
1. Only makes markets on one side (Yes or No) to avoid directional risk
2. Uses order groups to limit total exposure per market
3. Dynamically adjusts prices based on market conditions
4. Monitors positions and automatically adjusts orders
5. Uses websockets for real-time data and order management
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from kalshi import KalshiAPIClient
from kalshi.websocket import KalshiWebSocketClient
from kalshi.models import Market, MarketPosition
from config import Config, setup_logging
from bot_config import MarketMakingConfig, TradingMode, MarketSide
from bot_monitoring import BotMonitor

# Configure logging
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

class OrderAction(Enum):
    """Order action enumeration."""
    BUY = "buy"
    SELL = "sell"

@dataclass
class MarketMakingState:
    """Current state of market making bot."""
    active_markets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    order_groups: Dict[str, str] = field(default_factory=dict)  # market_ticker -> order_group_id
    active_orders: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)  # market_ticker -> orders
    positions: Dict[str, int] = field(default_factory=dict)  # market_ticker -> position
    daily_pnl: float = 0.0
    total_exposure: int = 0
    last_order_refresh: Dict[str, datetime] = field(default_factory=dict)
    emergency_stop: bool = False

class KalshiMarketMakingBot:
    """Kalshi Market Making Bot with single-sided strategy."""
    
    def __init__(self, config: Config, bot_config: MarketMakingConfig):
        """Initialize the market making bot."""
        self.config = config
        self.bot_config = bot_config
        self.kalshi_client = KalshiAPIClient(config)
        self.ws_client = KalshiWebSocketClient(config)
        self.state = MarketMakingState()
        
        # Bot control
        self.running = False
        self.start_time = None
        
        # Performance tracking
        self.total_orders_placed = 0
        self.total_orders_filled = 0
        self.total_fees_paid = 0.0
        
        # Monitoring
        self.monitor = BotMonitor()
        
        logger.info(f"Market Making Bot initialized with config: {bot_config}")
    
    async def start(self):
        """Start the market making bot."""
        if self.running:
            logger.warning("Bot is already running")
            return
        
        logger.info("Starting Kalshi Market Making Bot...")
        self.running = True
        self.start_time = datetime.now(timezone.utc)
        
        try:
            # Start monitoring
            self.monitor.start_monitoring()
            
            # Connect to websocket
            await self.ws_client.connect()
            
            # Subscribe to fills and positions for real-time updates
            self.ws_client.subscribe_fills(self._on_fill_update)
            self.ws_client.subscribe_market_positions(self._on_position_update)
            
            # Initial setup
            await self._initialize_bot()
            
            # Main trading loop
            await self._trading_loop()
            
        except Exception as e:
            logger.error(f"Error in bot main loop: {e}")
            self.monitor.record_api_error({"message": str(e), "type": "bot_error"})
            raise
        finally:
            await self._cleanup()
    
    async def stop(self):
        """Stop the market making bot."""
        logger.info("Stopping Market Making Bot...")
        self.running = False
        
        # Cancel all active orders
        await self._cancel_all_orders()
        
        # Disconnect websocket
        await self.ws_client.disconnect()
        
        # Stop monitoring
        self.monitor.stop_monitoring()
        
        logger.info("Market Making Bot stopped")
    
    async def _initialize_bot(self):
        """Initialize the bot with current positions and market data."""
        logger.info("Initializing bot...")
        
        # Check account balance
        balance = self.kalshi_client.get_balance_dollars()
        if balance is None or balance < 100:
            raise Exception(f"Insufficient balance: ${balance}")
        
        logger.info(f"Account balance: ${balance:.2f}")
        
        # Get current positions and resting orders (optimized for active positions only)
        positions_data = self.kalshi_client.get_active_positions_only()
        if positions_data:
            for pos in positions_data.get('market_positions', []):
                ticker = pos['ticker']
                position = pos['position']
                resting_orders = pos.get('resting_orders_count', 0)
                
                self.state.positions[ticker] = position
                
                # Log both position and resting orders
                if position != 0 or resting_orders > 0:
                    logger.info(f"Market {ticker}: position={position}, resting_orders={resting_orders}")
        
        # Find suitable markets
        suitable_markets = await self._find_suitable_markets()
        logger.info(f"Found {len(suitable_markets)} suitable markets")
        
        # Initialize order groups for each market
        for market in suitable_markets:
            await self._create_order_group(market['ticker'])
        
        logger.info("Bot initialization complete")
    
    async def _find_suitable_markets(self) -> List[Dict[str, Any]]:
        """Find markets suitable for market making."""
        logger.info("Finding suitable markets...")
        
        # Get active markets
        markets = self.kalshi_client.get_markets(limit=200, status='active')
        suitable_markets = []
        
        for market in markets:
            try:
                # Check if market meets criteria
                if self._is_market_suitable(market):
                    suitable_markets.append({
                        'ticker': market.ticker,
                        'market': market,
                        'side': self._determine_market_side(market)
                    })
                    
            except Exception as e:
                logger.warning(f"Error evaluating market {market.ticker}: {e}")
                continue
        
        # Sort by volume (highest first)
        suitable_markets.sort(key=lambda x: x['market'].volume or 0, reverse=True)
        
        # Limit to top markets to avoid overexposure
        max_markets = min(10, len(suitable_markets))
        return suitable_markets[:max_markets]
    
    def _is_market_suitable(self, market: Market) -> bool:
        """Check if a market is suitable for market making."""
        try:
            # Check volume (total volume)
            if market.volume and market.volume < self.bot_config.market_selection.min_volume:
                return False
            
            # Check 24h volume if available (more recent activity)
            if market.volume_24h and market.volume_24h < self.bot_config.market_selection.min_volume // 2:
                return False
            
            # Check spread
            if market.spread_cents and market.spread_cents > self.bot_config.market_selection.max_spread_cents:
                return False
            
            # Check time to close
            if market.days_to_close and market.days_to_close > self.bot_config.market_selection.max_time_to_close_days:
                return False
            
            # Check open interest (indicates market depth and interest)
            if market.open_interest and market.open_interest < self.bot_config.market_selection.min_volume:
                return False
            
            # Check liquidity using multiple metrics
            # 1. Direct liquidity field if available
            if hasattr(market, 'liquidity_dollars') and market.liquidity_dollars:
                if market.liquidity_dollars < self.bot_config.market_selection.min_liquidity_dollars:
                    return False
            # 2. Fallback to volume as liquidity proxy
            else:
                min_liquidity_cents = int(self.bot_config.market_selection.min_liquidity_dollars * 100)
                if market.volume and market.volume < min_liquidity_cents:
                    return False
            
            # Check if market has valid prices
            if market.yes_bid is None or market.yes_ask is None:
                return False
            
            # Check if market is active (not closed/settled)
            if market.status and market.status not in ['active', 'initialized']:
                return False
            
            # Check if market can close early (avoid markets that might close unexpectedly)
            if hasattr(market, 'can_close_early') and market.can_close_early:
                # For now, we'll allow early close markets but could add logic to avoid them
                pass
            
            return True
            
        except Exception as e:
            logger.warning(f"Error checking market suitability for {market.ticker}: {e}")
            return False
    
    def _determine_market_side(self, market: Market) -> MarketSide:
        """Determine which side to make markets on."""
        # For now, use the preferred side from config
        # In a more sophisticated bot, this could be based on:
        # - Market momentum
        # - Volatility
        # - Historical performance
        # - News sentiment
        return self.bot_config.preferred_side
    
    async def _create_order_group(self, ticker: str) -> Optional[str]:
        """Create an order group for a market."""
        try:
            # Create order group with position limit
            order_group_data = {
                "contracts_limit": self.bot_config.risk_limits.max_position_per_market
            }
            
            response = self.kalshi_client.http_client.post(
                "/portfolio/order_groups/create",
                data=order_group_data
            )
            
            if response and 'order_group_id' in response:
                order_group_id = response['order_group_id']
                self.state.order_groups[ticker] = order_group_id
                logger.info(f"Created order group {order_group_id} for {ticker}")
                return order_group_id
            else:
                logger.error(f"Failed to create order group for {ticker}: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating order group for {ticker}: {e}")
            return None
    
    async def _trading_loop(self):
        """Main trading loop."""
        logger.info("Starting trading loop...")
        
        while self.running:
            try:
                # Check emergency conditions
                if self._check_emergency_conditions():
                    logger.warning("Emergency conditions detected, stopping bot")
                    break
                
                # Update market data
                await self._update_market_data()
                
                # Manage orders for each active market
                for ticker in list(self.state.active_markets.keys()):
                    await self._manage_market_orders(ticker)
                
                # Wait before next iteration
                await asyncio.sleep(self.bot_config.order_management.order_refresh_interval)
                
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                await asyncio.sleep(5)  # Brief pause before retrying
    
    async def _update_market_data(self):
        """Update market data from websocket."""
        # Get recent messages from websocket
        messages = self.ws_client.get_all_messages()
        
        for message in messages:
            if message.get('channel') == 'ticker':
                data = message.get('data', {})
                ticker = data.get('market_ticker')
                if ticker and ticker in self.state.active_markets:
                    # Update market data
                    self.state.active_markets[ticker]['last_update'] = datetime.now(timezone.utc)
                    self.state.active_markets[ticker]['market_data'] = data
    
    async def _manage_market_orders(self, ticker: str):
        """Manage orders for a specific market."""
        try:
            market_info = self.state.active_markets.get(ticker)
            if not market_info:
                return
            
            # Check if we need to refresh orders
            last_refresh = self.state.last_order_refresh.get(ticker)
            if last_refresh and (datetime.now(timezone.utc) - last_refresh).seconds < self.bot_config.order_management.order_refresh_interval:
                return
            
            # Get current market data
            market = self.kalshi_client.get_market_by_ticker(ticker)
            if not market:
                logger.warning(f"Could not get market data for {ticker}")
                return
            
            # Add small delay to avoid rate limiting when fetching multiple markets
            await asyncio.sleep(0.1)
            
            # Get current position
            current_position = self.state.positions.get(ticker, 0)
            
            # Calculate target prices
            target_prices = self._calculate_target_prices(market, current_position)
            
            # Cancel existing orders
            await self._cancel_market_orders(ticker)
            
            # Place new orders
            await self._place_market_orders(ticker, market, target_prices)
            
            # Update last refresh time
            self.state.last_order_refresh[ticker] = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Error managing orders for {ticker}: {e}")
    
    def _calculate_target_prices(self, market: Market, current_position: int) -> Dict[str, int]:
        """Calculate target bid and ask prices."""
        # Get current mid price
        if market.yes_bid is None or market.yes_ask is None:
            return {}
        
        mid_price = (market.yes_bid + market.yes_ask) // 2
        
        # Adjust spread based on position
        # If we're long, widen the ask spread to encourage selling
        # If we're short, widen the bid spread to encourage buying
        base_spread_cents = self.bot_config.pricing_strategy.default_spread_cents
        # price_adjustment_factor is a multiplier (no units), result is in cents
        position_adjustment_cents = abs(current_position) * self.bot_config.pricing_strategy.price_adjustment_factor
        
        if current_position > 0:  # Long position
            bid_spread_cents = base_spread_cents + position_adjustment_cents
            ask_spread_cents = max(base_spread_cents - position_adjustment_cents, self.bot_config.pricing_strategy.min_spread_cents)
        elif current_position < 0:  # Short position
            bid_spread_cents = max(base_spread_cents - position_adjustment_cents, self.bot_config.pricing_strategy.min_spread_cents)
            ask_spread_cents = base_spread_cents + position_adjustment_cents
        else:  # Flat position
            bid_spread_cents = base_spread_cents
            ask_spread_cents = base_spread_cents
        
        # Calculate target prices (all in cents)
        target_bid_cents = max(1, mid_price - bid_spread_cents // 2)
        target_ask_cents = min(99, mid_price + ask_spread_cents // 2)
        
        return {
            'bid': target_bid_cents,
            'ask': target_ask_cents,
            'mid': mid_price
        }
    
    async def _cancel_market_orders(self, ticker: str):
        """Cancel all orders for a specific market."""
        try:
            active_orders = self.state.active_orders.get(ticker, [])
            
            for order in active_orders:
                order_id = order.get('order_id')
                if order_id:
                    await self._cancel_order(order_id)
            
            # Clear active orders
            self.state.active_orders[ticker] = []
            
        except Exception as e:
            logger.error(f"Error canceling orders for {ticker}: {e}")
    
    async def _place_market_orders(self, ticker: str, market: Market, target_prices: Dict[str, int]):
        """Place market making orders for a market."""
        try:
            order_group_id = self.state.order_groups.get(ticker)
            if not order_group_id:
                logger.warning(f"No order group for {ticker}")
                return
            
            current_position = self.state.positions.get(ticker, 0)
            orders_to_place = []
            
            # Determine which side to make markets on
            market_side = self.state.active_markets[ticker]['side']
            
            # Place bid order (buy)
            if current_position < self.bot_config.risk_limits.max_position_per_market:
                bid_order = {
                    "action": "buy",
                    "count": min(self.bot_config.risk_limits.max_order_size, self.bot_config.risk_limits.max_position_per_market - current_position),
                    "side": market_side.value,
                    "ticker": ticker,
                    "yes_price": target_prices['bid'],  # Price in cents
                    "order_group_id": order_group_id,
                    "client_order_id": f"bid_{ticker}_{int(time.time())}",
                    "time_in_force": "good_til_canceled"
                }
                orders_to_place.append(bid_order)
            
            # Place ask order (sell)
            if current_position > -self.bot_config.risk_limits.max_position_per_market:
                ask_order = {
                    "action": "sell",
                    "count": min(self.bot_config.risk_limits.max_order_size, self.bot_config.risk_limits.max_position_per_market + current_position),
                    "side": market_side.value,
                    "ticker": ticker,
                    "yes_price": target_prices['ask'],  # Price in cents
                    "order_group_id": order_group_id,
                    "client_order_id": f"ask_{ticker}_{int(time.time())}",
                    "time_in_force": "good_til_canceled"
                }
                orders_to_place.append(ask_order)
            
            # Place orders in batch
            if orders_to_place:
                await self._place_batch_orders(orders_to_place)
                
        except Exception as e:
            logger.error(f"Error placing orders for {ticker}: {e}")
    
    async def _place_batch_orders(self, orders: List[Dict[str, Any]]):
        """Place a batch of orders."""
        try:
            batch_data = {"orders": orders}
            
            response = self.kalshi_client.http_client.post(
                "/portfolio/orders/batched",
                data=batch_data
            )
            
            if response and 'orders' in response:
                for order_response in response['orders']:
                    if 'order' in order_response:
                        order = order_response['order']
                        ticker = order['ticker']
                        
                        # Track the order
                        if ticker not in self.state.active_orders:
                            self.state.active_orders[ticker] = []
                        self.state.active_orders[ticker].append(order)
                        
                        self.total_orders_placed += 1
                        self.monitor.record_order_placed(order)
                        logger.info(f"Placed order {order['order_id']} for {ticker}")
                    elif 'error' in order_response:
                        error = order_response['error']
                        self.monitor.record_order_rejected({"error": error, "order": order})
                        logger.error(f"Order error: {error}")
                        
        except Exception as e:
            logger.error(f"Error placing batch orders: {e}")
    
    async def _cancel_order(self, order_id: str):
        """Cancel a specific order."""
        try:
            response = self.kalshi_client.http_client.delete(f"/portfolio/orders/{order_id}")
            if response:
                self.monitor.record_order_canceled({"order_id": order_id})
                logger.info(f"Canceled order {order_id}")
            else:
                logger.warning(f"Failed to cancel order {order_id}")
                
        except Exception as e:
            logger.error(f"Error canceling order {order_id}: {e}")
            self.monitor.record_api_error({"message": str(e), "type": "cancel_order_error"})
    
    async def _cancel_all_orders(self):
        """Cancel all active orders."""
        logger.info("Canceling all active orders...")
        
        for ticker, orders in self.state.active_orders.items():
            for order in orders:
                order_id = order.get('order_id')
                if order_id:
                    await self._cancel_order(order_id)
        
        # Clear all active orders
        self.state.active_orders.clear()
    
    def _check_emergency_conditions(self) -> bool:
        """Check for emergency conditions that should stop the bot."""
        # Check daily P&L (in dollars)
        if self.state.daily_pnl < -self.bot_config.risk_limits.max_daily_loss:
            logger.error(f"Daily loss limit exceeded: ${self.state.daily_pnl:.2f}")
            return True
        
        # Check total exposure (in contracts)
        if self.state.total_exposure > self.bot_config.risk_limits.max_total_exposure:
            logger.error(f"Total exposure limit exceeded: {self.state.total_exposure}")
            return True
        
        # Check emergency stop flag
        if self.state.emergency_stop:
            logger.error("Emergency stop flag is set")
            return True
        
        return False
    
    def _on_fill_update(self, fill_data: Dict[str, Any]):
        """Handle fill updates from websocket."""
        try:
            ticker = fill_data.get('ticker')
            if not ticker:
                return
            
            # Update position
            position_change = fill_data.get('count', 0)
            if fill_data.get('side') == 'no':  # NO side fills are opposite
                position_change = -position_change
            
            current_position = self.state.positions.get(ticker, 0)
            self.state.positions[ticker] = current_position + position_change
            
            # Update total exposure
            self.state.total_exposure = sum(abs(pos) for pos in self.state.positions.values())
            
            # Track performance
            self.total_orders_filled += 1
            # Convert fees from cents to dollars for tracking
            fees_cents = fill_data.get('fees', 0)
            fees_dollars = fees_cents / 100.0
            self.total_fees_paid += fees_dollars
            
            # Record fill in monitoring system
            self.monitor.record_order_filled(fill_data)
            self.monitor.record_position_update({
                "ticker": ticker,
                "position": self.state.positions[ticker],
                "change": position_change
            })
            
            logger.info(f"Fill update for {ticker}: position={self.state.positions[ticker]}, fees=${fees_dollars:.2f}")
            
        except Exception as e:
            logger.error(f"Error processing fill update: {e}")
            self.monitor.record_api_error({"message": str(e), "type": "fill_update_error"})
    
    def _on_position_update(self, position_data: Dict[str, Any]):
        """Handle position updates from websocket."""
        try:
            ticker = position_data.get('ticker')
            position = position_data.get('position', 0)
            
            if ticker:
                self.state.positions[ticker] = position
                self.monitor.record_position_update(position_data)
                logger.info(f"Position update for {ticker}: {position}")
                
        except Exception as e:
            logger.error(f"Error processing position update: {e}")
            self.monitor.record_api_error({"message": str(e), "type": "position_update_error"})
    
    async def _cleanup(self):
        """Cleanup when bot stops."""
        logger.info("Cleaning up bot...")
        
        # Cancel all orders
        await self._cancel_all_orders()
        
        # Disconnect websocket
        await self.ws_client.disconnect()
        
        # Log final statistics
        self._log_final_statistics()
    
    def _log_final_statistics(self):
        """Log final bot statistics."""
        runtime = datetime.now(timezone.utc) - self.start_time if self.start_time else timedelta(0)
        
        logger.info("=== Bot Final Statistics ===")
        logger.info(f"Runtime: {runtime}")
        logger.info(f"Total orders placed: {self.total_orders_placed}")
        logger.info(f"Total orders filled: {self.total_orders_filled}")
        logger.info(f"Total fees paid: ${self.total_fees_paid:.2f}")
        logger.info(f"Daily P&L: ${self.state.daily_pnl:.2f}")
        logger.info(f"Total exposure: {self.state.total_exposure}")
        logger.info(f"Active markets: {len(self.state.active_markets)}")
        logger.info("===========================")

# Example usage and configuration
async def main():
    """Main function to run the market making bot."""
    # Load configuration
    config = Config()
    
    # Create bot configuration using the new system
    from bot_config import BotConfigManager, TradingMode
    
    config_manager = BotConfigManager()
    bot_config = config_manager.get_preset_config(TradingMode.MODERATE)
    
    # Create and start bot
    bot = KalshiMarketMakingBot(config, bot_config)
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, stopping bot...")
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
