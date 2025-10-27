"""
Acadia Market Making Strategy

A simple market making strategy that adapts from the TouchMaker approach.
This is a stateless strategy that takes market state and position data as inputs
and returns the orders that should exist.

Key Features:
- Simple bid/ask spread management
- Position-aware order sizing
- Basic risk management through position limits
- Queue-aware pricing improvements
"""

import logging
from typing import Dict, List
from kalshi.models import Order
from market_making.market_types import MarketConfig, MarketState, PositionState, OrderIntent
from market_making.order_manager import OrderManager

logger = logging.getLogger(__name__)



class AcadiaStrategy:
    """
    Simple market making strategy for Kalshi markets.
    
    This strategy:
    1. Maintains bid/ask spreads on both sides when inventory allows
    2. Adjusts prices based on current position
    3. Respects position limits from configuration
    4. Uses simple queue-aware pricing
    """
    
    def __init__(self, market_configs: List[MarketConfig]):
        """
        Initialize the Acadia strategy.
        
        Args:
            market_configs: List of market configurations from the main bot
        """
        self.market_configs = market_configs
        self.market_states: Dict[str, MarketState] = {}
        self.position_states: Dict[str, PositionState] = {}
        self.order_manager = OrderManager()
        
        logger.info(f"Initialized Acadia strategy for {len(market_configs)} markets")
    
    def initialize(self, current_positions: Dict[str, PositionState], outstanding_orders: Dict[str, List[Order]]):
        """
        Initialize the strategy with current state from the main bot.
        
        Args:
            current_positions: Current positions from the API
            outstanding_orders: Current outstanding orders from the API
        """
        logger.info("Initializing Acadia strategy state...")
        
        # Initialize position states
        for market_config in self.market_configs:
            ticker = market_config.ticker
            side = market_config.side
            position_limit = market_config.position_limit
            
            # Get current position
            position = 0
            if ticker in current_positions:
                position = current_positions[ticker]["position"]
            
            self.position_states[ticker] = PositionState(
                ticker=ticker,
                position=position,
                position_limit=position_limit,
                side=side
            )
            
            logger.info(f"Initialized {ticker}: position={position}, limit={position_limit}, side={side}")
        
        # Initialize market states (will be updated with real market data)
        for market_config in self.market_configs:
            ticker = market_config.ticker
            self.market_states[ticker] = MarketState(ticker=ticker)
            logger.info(f"Initialized market state for {ticker}")
        
        # Log outstanding orders
        for ticker, orders in outstanding_orders.items():
            if orders:
                logger.info(f"Found {len(orders)} outstanding orders for {ticker}")
                for order in orders:
                    logger.info(f"  - {order.action} {order.side} {order.remaining_count} @ {order.yes_price_dollars or order.no_price_dollars}")
            else:
                logger.info(f"No outstanding orders for {ticker}")
        
        logger.info("Acadia strategy initialization complete")
    
    def generate_orders(self, ticker: str) -> List[OrderIntent]:
        """
        Generate order intents for a specific market.
        
        Strategy:
        1. If no position: Place orders at best bid/ask on specified side to open
        2. If in position: Place opposite orders to exit at best ask/bid
        3. If position is down >25%: Exit more aggressively
        
        Args:
            ticker: Market ticker to generate orders for
            
        Returns:
            List of OrderIntent objects
        """
        if ticker not in self.market_states or ticker not in self.position_states:
            logger.warning(f"Missing state for ticker {ticker}")
            return []
        
        market_state = self.market_states[ticker]
        position_state = self.position_states[ticker]
        
        # Get market config for this ticker
        market_config = None
        for config in self.market_configs:
            if config.ticker == ticker:
                market_config = config
                break
        
        if not market_config:
            logger.warning(f"No config found for ticker {ticker}")
            return []
        
        orders = []
        
        # Check if we have valid market data
        if (market_state.yes_bid is None or market_state.yes_ask is None or 
            market_state.no_bid is None or market_state.no_ask is None):
            logger.debug(f"[{ticker}] Missing market data, skipping order generation")
            return []
        
        current_position = position_state.position
        
        # Calculate position return before making decisions
        self._calculate_position_return(position_state, market_state)
        
        # Generate target order state (what orders should exist)
        if current_position == 0:
            # No position - generate opening orders
            target_orders = self._generate_opening_orders(market_state, market_config, position_state)
        else:
            # In position - generate closing orders
            target_orders = self._generate_closing_orders(market_state, market_config, position_state)
        
        # Let order manager determine what actions are needed
        actions = self.order_manager.sync_target_orders(ticker, target_orders)
        
        # Log the sync results
        if actions['add']:
            logger.info(f"[{ticker}] Orders to add: {len(actions['add'])}")
            for order in actions['add']:
                logger.info(f"  + {order.action} {order.side} {order.size} @ {order.price_cents}")
        
        if actions['cancel']:
            logger.info(f"[{ticker}] Orders to cancel: {len(actions['cancel'])}")
            for order in actions['cancel']:
                logger.info(f"  - {order.action} {order.side} {order.size} @ {order.price_cents}")
        
        if actions['keep']:
            logger.debug(f"[{ticker}] Orders to keep: {len(actions['keep'])}")
        
        # Return the actions for the listener to execute
        return actions
    
    def _generate_opening_orders(self, market_state: MarketState, market_config: MarketConfig, position_state: PositionState) -> List[OrderIntent]:
        """
        Generate orders to open positions when we have no current position.
        Places orders at best bid/ask on the specified side using position limits.
        """
        orders = []
        side = market_config.side.lower()
        ticker = market_config.ticker
        position_limit = position_state.position_limit
        current_position = position_state.position
        
        # Calculate remaining capacity for each direction
        remaining_long_capacity = position_limit - current_position  # How many more we can buy
        remaining_short_capacity = position_limit + current_position  # How many more we can sell
        
        if side == "yes":
            # Only buy YES contracts (go long YES only)
            if market_state.yes_bid is not None and remaining_long_capacity > 0:
                order_size = min(remaining_long_capacity, position_limit)
                orders.append(OrderIntent(
                    ticker=ticker,
                    side="yes",
                    price_cents=market_state.yes_bid,
                    size=order_size,
                    action="buy",
                    intent_type="market_making"
                ))
        
        elif side == "no":
            # Only buy NO contracts (go long NO only)
            if market_state.no_bid is not None and remaining_short_capacity > 0:
                order_size = min(remaining_short_capacity, position_limit)
                orders.append(OrderIntent(
                    ticker=ticker,
                    side="no",
                    price_cents=market_state.no_bid,
                    size=order_size,
                    action="buy",
                    intent_type="market_making"
                ))
        
        elif side == "both":
            # Place buy orders on both YES and NO to capture spread
            # Buy YES at best YES bid
            if market_state.yes_bid is not None and remaining_long_capacity > 0:
                order_size = min(remaining_long_capacity, position_limit)
                orders.append(OrderIntent(
                    ticker=ticker,
                    side="yes",
                    price_cents=market_state.yes_bid,
                    size=order_size,
                    action="buy",
                    intent_type="market_making"
                ))
            
            # Buy NO at best NO bid (equivalent to selling YES)
            if market_state.no_bid is not None and remaining_short_capacity > 0:
                order_size = min(remaining_short_capacity, position_limit)
                orders.append(OrderIntent(
                    ticker=ticker,
                    side="no",
                    price_cents=market_state.no_bid,
                    size=order_size,
                    action="buy",
                    intent_type="market_making"
                ))
        
        logger.debug(f"[{ticker}] Generated {len(orders)} opening orders for side={side}")
        return orders
    
    def _generate_closing_orders(self, market_state: MarketState, market_config: MarketConfig, position_state: PositionState) -> List[OrderIntent]:
        """
        Generate orders to close positions.
        Places opposite orders to exit at best ask/bid to collect spread profit.
        If position is down >25%, exits more aggressively.
        """
        orders = []
        ticker = market_config.ticker
        current_position = position_state.position
        position_return_pct = position_state.position_return_pct or 0.0
        
        # Check if we should exit aggressively (down >25%)
        is_aggressive_exit = position_return_pct < -25.0
        
        if current_position > 0:
            # Long YES position - sell YES to close
            if market_state.yes_ask is not None:
                exit_price = market_state.yes_ask
                
                # If down >25%, be more aggressive and sell at bid instead of ask
                intent_type = "aggressive_exit" if is_aggressive_exit else "exit"
                if is_aggressive_exit and market_state.yes_bid is not None:
                    exit_price = market_state.yes_bid
                    logger.warning(f"[{ticker}] AGGRESSIVE EXIT: Position down {position_return_pct:.1f}%, selling YES at bid {exit_price} instead of ask {market_state.yes_ask}")
                
                orders.append(OrderIntent(
                    ticker=ticker,
                    side="yes",
                    price_cents=exit_price,
                    size=current_position,  # Sell entire YES position
                    action="sell",
                    intent_type=intent_type
                ))
        
        elif current_position < 0:
            # Negative position means we have NO contracts - sell NO to close
            if market_state.no_bid is not None:
                exit_price = market_state.no_bid
                
                # If down >25%, be more aggressive and sell NO at ask instead of bid
                intent_type = "aggressive_exit" if is_aggressive_exit else "exit"
                if is_aggressive_exit and market_state.no_ask is not None:
                    exit_price = market_state.no_ask
                    logger.warning(f"[{ticker}] AGGRESSIVE EXIT: Position down {position_return_pct:.1f}%, selling NO at ask {exit_price} instead of bid {market_state.no_bid}")
                
                orders.append(OrderIntent(
                    ticker=ticker,
                    side="no",
                    price_cents=exit_price,
                    size=abs(current_position),  # Sell NO to close negative position
                    action="sell",
                    intent_type=intent_type
                ))
        
        logger.debug(f"[{ticker}] Generated {len(orders)} closing orders for position={current_position}")
        return orders
    
    def _calculate_position_return(self, position_state: PositionState, market_state: MarketState) -> None:
        """
        Calculate current position return and update position state.
        """
        position = position_state.position
        if position == 0:
            position_state.unrealized_pnl_cents = 0
            position_state.position_return_pct = 0.0
            return
        
        unrealized_pnl = 0
        entry_price = None
        
        if position > 0:
            # Long YES position
            if position_state.avg_entry_price_yes is not None and market_state.yes_bid is not None:
                entry_price = position_state.avg_entry_price_yes
                # P&L = (current_bid - entry_price) * position
                unrealized_pnl = (market_state.yes_bid - entry_price) * position
        
        elif position < 0:
            # Short YES position (long NO)
            if position_state.avg_entry_price_no is not None and market_state.no_bid is not None:
                entry_price = position_state.avg_entry_price_no
                # P&L = (current_no_bid - entry_price) * abs(position)
                unrealized_pnl = (market_state.no_bid - entry_price) * abs(position)
        
        position_state.unrealized_pnl_cents = unrealized_pnl
        
        if entry_price and entry_price > 0:
            position_state.position_return_pct = (unrealized_pnl / (entry_price * abs(position))) * 100
        else:
            position_state.position_return_pct = 0.0