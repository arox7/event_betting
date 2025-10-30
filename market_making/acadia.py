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
    
    def __init__(self, market_configs: List[MarketConfig], dry_run: bool = False):
        """
        Initialize the Acadia strategy.
        
        Args:
            market_configs: List of market configurations from the main bot
            dry_run: If True, log actions without actually placing/cancelling orders
        """
        self.market_configs = market_configs
        self.market_states: Dict[str, MarketState] = {}
        self.position_states: Dict[str, PositionState] = {}
        self.order_manager = OrderManager()
        self.dry_run = dry_run
        
        if dry_run:
            logger.warning("🧪 DRY RUN MODE ENABLED - No real orders will be placed or cancelled")
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
    
    def generate_orders(self, ticker: str) -> Dict[str, List]:
        """
        Generate order intents for a specific market.
        
        Strategy:
        1. If no position: Place orders at best bid/ask on specified side to open
        2. If in position: Place opposite orders to exit at best ask/bid
        3. If position is down >25%: Exit more aggressively
        
        Args:
            ticker: Market ticker to generate orders for
            
        Returns:
            Dict with 'add', 'cancel', 'keep' lists of orders/intents
        """
        # Empty actions dict for early returns
        empty_actions = {'add': [], 'cancel': [], 'keep': []}
        
        if ticker not in self.market_states or ticker not in self.position_states:
            logger.warning(f"Missing state for ticker {ticker}")
            return empty_actions
        
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
            return empty_actions
        
        # Check if we have valid market data
        if (market_state.yes_bid is None or market_state.yes_ask is None or 
            market_state.no_bid is None or market_state.no_ask is None):
            logger.debug(f"[{ticker}] Missing market data, skipping order generation")
            return empty_actions
        
        current_position = position_state.position
        
        # Calculate position return before making decisions
        self._calculate_position_return(position_state, market_state)
        
        # Generate target order state (what orders should exist)
        target_orders = []
        
        # Generate opening orders (will check capacity internally including outstanding orders)
        target_orders.extend(self._generate_opening_orders(market_state, market_config, position_state))
        
        # Generate closing orders if we have a position
        if abs(current_position) > 0:
            target_orders.extend(self._generate_closing_orders(market_state, market_config, position_state))
        
        # Let order manager determine what actions are needed
        # Pass hysteresis threshold and market state for staleness checks
        actions = self.order_manager.sync_target_orders(
            ticker=ticker,
            target_orders=target_orders,
            min_price_delta_cents=market_config.min_price_delta_cents,
            market_state=market_state
        )
        
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
        Generate orders to open positions when we have not yet reached the position limit.
        Places orders at best bid/ask on the specified side using position limits.
        Only generates orders if minimum spread requirement is met.
        """
        orders = []
        side = market_config.side.lower()
        ticker = market_config.ticker
        position_limit = position_state.position_limit
        current_position = position_state.position
        
        # Check minimum spread requirement for opening orders
        yes_spread = market_state.yes_ask - market_state.yes_bid
        no_spread = market_state.no_ask - market_state.no_bid
        
        if yes_spread < market_config.min_spread_cents and no_spread < market_config.min_spread_cents:
            logger.debug(
                f"[{ticker}] Spread too tight for opening orders: YES {yes_spread}¢, NO {no_spread}¢ "
                f"(min {market_config.min_spread_cents}¢) - skipping opening quotes"
            )
            return []
        
        # Calculate remaining capacity accounting for outstanding orders
        # This prevents race conditions where an order is cancelled but fills before cancel processes
        outstanding_orders = self.order_manager.state.active_orders.get(ticker, [])
        outstanding_buy_yes = sum(o.remaining_size for o in outstanding_orders 
                                  if o.action == "buy" and o.side == "yes")
        outstanding_buy_no = sum(o.remaining_size for o in outstanding_orders 
                                 if o.action == "buy" and o.side == "no")
        
        # Effective position = current + what could still fill from outstanding orders
        effective_long_position = current_position + outstanding_buy_yes - outstanding_buy_no
        
        remaining_long_capacity = max(0, position_limit - effective_long_position)
        remaining_short_capacity = max(0, position_limit + effective_long_position)
        
        logger.debug(
            f"[{ticker}] Position capacity: current={current_position}, "
            f"outstanding_yes={outstanding_buy_yes}, outstanding_no={outstanding_buy_no}, "
            f"effective={effective_long_position}, limit={position_limit}, "
            f"long_capacity={remaining_long_capacity}, short_capacity={remaining_short_capacity}"
        )
        
        if side == "yes":
            # Only buy YES contracts (go long YES only)
            if market_state.yes_bid is not None and remaining_long_capacity > 0:
                order_size = remaining_long_capacity
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
                order_size = remaining_short_capacity
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
                order_size = remaining_long_capacity
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
                order_size = remaining_short_capacity
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
        Generate orders to close positions with simple but profitable exit logic.
        
        Exit Strategy (based on edge = exit_price - entry_price):
        1. Disaster (down >25%): cross spread immediately (bid for YES, ask for NO)
        2. Good edge (>= exit_edge_threshold_cents): undercut by 1¢ for faster fill, still profitable
        3. Small edge (0 to threshold): take best price, don't give up edge
        4. Underwater (negative): take best price to minimize loss, don't wait
        
        This prioritizes "salvage edge when possible" over "wait for breakeven".
        """
        orders = []
        ticker = market_config.ticker
        current_position = position_state.position
        position_return_pct = position_state.position_return_pct or 0.0
        
        # Check if we should exit aggressively (down >25%)
        is_aggressive_exit = position_return_pct < -25.0
        
        if current_position > 0:
            # Long YES position - sell YES to close
            if market_state.yes_ask is None or market_state.yes_bid is None:
                return orders
                
            entry_price = position_state.avg_entry_price_yes
            
            if is_aggressive_exit:
                # Disaster: cross the spread immediately
                exit_price = market_state.yes_bid
                intent_type = "aggressive_exit"
                logger.warning(f"[{ticker}] AGGRESSIVE EXIT: Position down {position_return_pct:.1f}%, selling YES at bid {exit_price}")
            elif entry_price is None:
                # No entry data (bot restart or initialization): sell at ask conservatively
                exit_price = market_state.yes_ask
                intent_type = "exit"
                logger.warning(f"[{ticker}] Exit: No entry price data! Selling YES at ask {exit_price} (position={current_position})")
            else:
                # Calculate our edge: how much profit room do we have?
                edge_cents = market_state.yes_ask - entry_price
                
                if edge_cents >= market_config.exit_edge_threshold_cents:
                    # Good profit margin: undercut ask by 1¢ for faster fill, still profitable
                    exit_price = market_state.yes_ask - 1
                    intent_type = "exit"
                    logger.debug(f"[{ticker}] Exit: {edge_cents}¢ edge (>={market_config.exit_edge_threshold_cents}¢), undercutting ask by 1¢ → {exit_price}")
                elif edge_cents >= 0:
                    # Small profit or breakeven: take the ask, don't give up edge
                    exit_price = market_state.yes_ask
                    intent_type = "exit"
                    logger.debug(f"[{ticker}] Exit: {edge_cents}¢ edge, selling at ask {exit_price}")
                else:
                    # Underwater: sell at ask to minimize loss
                    exit_price = market_state.yes_ask
                    intent_type = "exit"
                    logger.info(f"[{ticker}] Exit: Underwater by {-edge_cents}¢, selling at ask {exit_price} to minimize loss")
            
            orders.append(OrderIntent(
                ticker=ticker,
                side="yes",
                price_cents=exit_price,
                size=current_position,
                action="sell",
                intent_type=intent_type
            ))
        
        elif current_position < 0:
            # Negative position means we have NO contracts - sell NO to close
            if market_state.no_ask is None or market_state.no_bid is None:
                return orders
                
            no_spread = market_state.no_ask - market_state.no_bid
            entry_price = position_state.avg_entry_price_no
            
            if is_aggressive_exit:
                # Disaster: cross the spread immediately
                exit_price = market_state.no_ask
                intent_type = "aggressive_exit"
                logger.warning(f"[{ticker}] AGGRESSIVE EXIT: Position down {position_return_pct:.1f}%, selling NO at ask {exit_price}")
            elif entry_price is None:
                # No entry data (bot restart or initialization): sell NO at bid conservatively
                exit_price = market_state.no_bid
                intent_type = "exit"
                logger.warning(f"[{ticker}] Exit: No entry price data! Selling NO at bid {exit_price} (position={current_position})")
            else:
                # Calculate our edge: how much profit room do we have?
                # For NO: we sell NO, so we want bid >= entry (higher is better)
                edge_cents = market_state.no_bid - entry_price
                
                if edge_cents >= market_config.exit_edge_threshold_cents:
                    # Good profit margin: can undercut for faster fill
                    # Undercut means we sell NO at a LOWER price (closer to ask)
                    exit_price = market_state.no_bid - 1
                    intent_type = "exit"
                    logger.debug(f"[{ticker}] Exit: NO {edge_cents}¢ edge (>={market_config.exit_edge_threshold_cents}¢), undercutting bid by 1¢ → {exit_price}")
                elif edge_cents >= 0:
                    # Small profit or breakeven: take the bid
                    exit_price = market_state.no_bid
                    intent_type = "exit"
                    logger.debug(f"[{ticker}] Exit: NO {edge_cents}¢ edge, selling at bid {exit_price}")
                else:
                    # Underwater: sell at bid to minimize loss
                    exit_price = market_state.no_bid
                    intent_type = "exit"
                    logger.info(f"[{ticker}] Exit: NO underwater by {-edge_cents}¢, selling at bid {exit_price} to minimize loss")
            
            orders.append(OrderIntent(
                ticker=ticker,
                side="no",
                price_cents=exit_price,
                size=abs(current_position),
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