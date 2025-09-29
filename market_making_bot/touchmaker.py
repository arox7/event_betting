"""
Stateless TouchMaker Strategy Implementation

TouchMaker is now stateless - it takes all necessary market and position data as inputs
and returns the orders that should exist. The StrategyEngine handles all state management.

Key Features:
- Pure function: same inputs always produce same outputs
- No internal state: all data passed as parameters
- Simple logic: bid both sides until inventory caps reached
- Queue-aware pricing: improves prices on thin queues
- Flow-aware adjustments: adjusts based on recent taker flow

Behavior:
1. Check inventory caps (can we bid YES/NO?)
2. Calculate optimal prices for each leg
3. Return list of orders that should exist
4. StrategyEngine handles the rest (placing/cancelling orders)

Configuration:
- min_spread_cents: Per-leg spread floor to justify quotes
- bid_size_contracts: Contracts per entry per leg
- improve_if_last: Enable 1-tick improvement on thin queues
- queue_small_threshold: Queue size threshold considered "thin"
- queue_big_threshold: Queue size threshold considered "thick"
- max_inventory_contracts: Net YES/NO cap across strategies
- touch_contract_limit: Per-strategy cap for TouchMaker group
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Tuple, Optional

from orderbook_tracker import OrderBookTracker
from shared_config import StrategyConfig, OrderIntent, clamp

logger = logging.getLogger(__name__)


class TouchMaker:
    """
    Stateless TouchMaker strategy implementation.
    
    Pure function that takes market state and position as inputs and returns
    the orders that should exist. No internal state - all data passed as parameters.
    """

    def __init__(self, config: StrategyConfig) -> None:
        """
        Initialize TouchMaker with strategy configuration.
        
        Args:
            config: StrategyConfig containing all strategy parameters
        """
        self.cfg = config
        # Track cooldown after shade-down to prevent oscillation
        self._shade_cooldown: Dict[str, float] = {}

    def run(
        self, 
        orderbook: OrderBookTracker,
        net_position: int,
        recent_taker_yes: Optional[str],
        recent_taker_no: Optional[str],
        max_inventory: int
    ) -> List[OrderIntent]:
        """
        Execute TouchMaker strategy logic.
        
        TouchMaker always bids both sides (YES and NO) as long as inventory caps allow.
        Kalshi handles the conversion - bidding YES when we have NO inventory effectively
        exits our NO position, and vice versa.
        
        Args:
            orderbook: Current orderbook state with YES/NO ladders
            net_position: Net position (positive = long YES, negative = long NO)
            recent_taker_yes: Recent taker flow direction for YES ("up"/"down"/None)
            recent_taker_no: Recent taker flow direction for NO ("up"/"down"/None)
            max_inventory: Maximum inventory cap (e.g., 100 means range [-100, +100])
            
        Returns:
            List of OrderIntent objects that should exist
        """
        orders = []
        
        # Check if we can bid YES (net position < max_inventory)
        yes_order = None
        if net_position < max_inventory:
            yes_order = self._create_order(orderbook, "yes", recent_taker_yes, net_position)
            if yes_order:
                orders.append(yes_order)
        
        # Check if we can bid NO (net_position > -max_inventory)
        no_order = None
        if net_position > -max_inventory:
            no_order = self._create_order(orderbook, "no", recent_taker_no, net_position)
            if no_order:
                orders.append(no_order)
        
        # Apply sum-cushion guard: ensure yes_bid + no_bid <= 100 - sum_cushion_ticks
        # BUT allow exit orders even if sum-guard violated (inventory management priority)
        if yes_order and no_order:
            sum_bids = yes_order.price_cents + no_order.price_cents
            max_allowed = 100 - self.cfg.sum_cushion_ticks
            if sum_bids > max_allowed:
                excess = sum_bids - max_allowed

                # Check if we have existing inventory that needs exiting
                has_yes_inventory = net_position > 0
                has_no_inventory = net_position < 0
                
                if has_yes_inventory and no_order:
                    # We have YES inventory, allow NO order to exit (even if sum-guard violated)
                    # But limit NO order size to our YES inventory (reduce-only)
                    max_exit_size = min(no_order.count, net_position)
                    if max_exit_size > 0:
                        no_order.count = max_exit_size
                        logger.info(f"[TOUCH] Sum-guard violated ({sum_bids} > {max_allowed}) but allowing NO order to exit {max_exit_size} YES inventory")
                        # Keep both orders but limit NO to reduce-only
                        orders = [yes_order, no_order]
                    else:
                        # Can't exit any inventory, remove both orders
                        orders = []
                        logger.info(f"[TOUCH] Sum-guard violated and no inventory to exit, removing both orders")
                elif has_no_inventory and yes_order:
                    # We have NO inventory, allow YES order to exit (even if sum-guard violated)
                    # But limit YES order size to our NO inventory (reduce-only)
                    max_exit_size = min(yes_order.count, abs(net_position))
                    if max_exit_size > 0:
                        yes_order.count = max_exit_size
                        logger.info(f"[TOUCH] Sum-guard violated ({sum_bids} > {max_allowed}) but allowing YES order to exit {max_exit_size} NO inventory")
                        # Keep both orders but limit YES to reduce-only
                        orders = [yes_order, no_order]
                    else:
                        # Can't exit any inventory, remove both orders
                        orders = []
                        logger.info(f"[TOUCH] Sum-guard violated and no inventory to exit, removing both orders")
                else:
                    # No existing inventory, apply normal sum-guard logic
                    logger.info(f"[TOUCH] Sum-cushion guard: {sum_bids} > {max_allowed} (excess={excess}), adjusting orders")

                    # Try to adjust down the worse side by the exact excess
                    if yes_order.price_cents >= no_order.price_cents:
                        # YES is higher, try to adjust it down
                        adjusted_yes_price = yes_order.price_cents - excess
                        if adjusted_yes_price >= 1:  # Don't go below 1 cent
                            yes_order.price_cents = adjusted_yes_price
                            logger.info(f"[TOUCH] Adjusted YES from {yes_order.price_cents + excess} to {adjusted_yes_price}")
                        else:
                            # Can't adjust enough, remove YES order
                            orders = [no_order]
                            logger.info(f"[TOUCH] Removed YES order (can't adjust enough)")
                    else:
                        # NO is higher, try to adjust it down
                        adjusted_no_price = no_order.price_cents - excess
                        if adjusted_no_price >= 1:  # Don't go below 1 cent
                            no_order.price_cents = adjusted_no_price
                            logger.info(f"[TOUCH] Adjusted NO from {no_order.price_cents + excess} to {adjusted_no_price}")
                        else:
                            # Can't adjust enough, remove NO order
                            orders = [yes_order]
                            logger.info(f"[TOUCH] Removed NO order (can't adjust enough)")
        
        # Final position check: ensure target position doesn't exceed limits
        for order in orders:
            if order.side == "yes":
                target_position = net_position + order.count
                if target_position > max_inventory:
                    logger.info(f"[TOUCH] Removing YES order - would exceed cap: {target_position} > {max_inventory}")
                    orders.remove(order)
            elif order.side == "no":
                target_position = net_position - order.count
                if target_position < -max_inventory:
                    logger.info(f"[TOUCH] Removing NO order - would exceed cap: {target_position} < {-max_inventory}")
                    orders.remove(order)
        
        
        logger.info(f"[TOUCH] Net position: {net_position}, max: {max_inventory}, orders: {len(orders)}")
        return orders

    def _calculate_midpoint(self, orderbook: OrderBookTracker) -> Optional[float]:
        """Calculate current market midpoint for change detection."""
        yes_bid = orderbook.best_bid("yes")
        yes_ask = orderbook.best_ask("yes")
        no_bid = orderbook.best_bid("no")
        no_ask = orderbook.best_ask("no")
        
        if not all([yes_bid.price, yes_ask.price, no_bid.price, no_ask.price]):
            return None
            
        # Calculate implied YES ask from NO side
        implied_yes_ask = 100 - no_bid.price
        implied_yes_bid = 100 - no_ask.price
        
        # Use the tighter spread
        yes_spread = yes_ask.price - yes_bid.price
        implied_spread = implied_yes_ask - implied_yes_bid
        
        if yes_spread <= implied_spread:
            return (yes_bid.price + yes_ask.price) / 2.0
        else:
            return (implied_yes_bid + implied_yes_ask) / 2.0

    def _create_order(
        self,
        orderbook: OrderBookTracker,
        side: str,
        recent_taker: Optional[str],
        net_position: int
    ) -> Optional[OrderIntent]:
        """
        Create order if market conditions allow.
        
        Args:
            orderbook: Current orderbook state
            side: "yes" or "no"
            recent_taker: Recent taker flow direction
            net_position: Current net position
            
        Returns:
            OrderIntent for the order, or None if conditions not met
        """
        # Check if we have valid market data
        bid = orderbook.best_bid(side)
        ask = orderbook.best_ask(side)
        if not bid.price or not ask.price:
            logger.info(f"[TOUCH] Skipping {side.upper()} - missing market data")
            return None
        
        # Check spread - exit orders are always allowed, entry orders need valid spread
        spread = ask.price - bid.price
        is_exit_order = (side == "yes" and net_position < 0) or (side == "no" and net_position > 0)
        
        if not is_exit_order and spread < self.cfg.min_spread_cents:
            logger.info(f"[TOUCH] Skipping {side.upper()} entry - spread too tight: {spread}")
            return None
        
        if is_exit_order:
            inventory_type = "NO" if side == "yes" else "YES"
            inventory_size = abs(net_position)
            logger.info(f"[TOUCH] Allowing {side.upper()} exit order ({inventory_type} inventory: {inventory_size})")
        
        # Calculate price
        price = self._calculate_price(orderbook, side, recent_taker, is_exit_order)
        
        # Generate unique client_order_id for each order to avoid 409 conflicts
        # Use timestamp + price hash to ensure uniqueness while maintaining some predictability
        import time
        import hashlib
        current_timestamp = int(time.time() * 1000)  # milliseconds for uniqueness
        price_key = f"{price}-{side}-{self.cfg.ticker}"
        price_hash = hashlib.md5(price_key.encode()).hexdigest()[:6]
        client_order_id = f"touch-{side}-{current_timestamp}-{price_hash}"
        
        # For exit orders, limit size to inventory (reduce-only)
        if is_exit_order:
            max_exit_size = abs(net_position)
            count = min(self.cfg.bid_size_contracts, max_exit_size)
            logger.info(f"[TOUCH] Exit order size limited to inventory: {count} (inventory: {max_exit_size})")
        else:
            count = self.cfg.bid_size_contracts
        
        return OrderIntent(
            ticker=self.cfg.ticker,
            strategy="touch",
            purpose="entry",
            action="buy",
            side=side,
            price_cents=price,
            count=count,
            post_only=True,
            client_order_id=client_order_id,
            expiration_ts=int(time.time() + self.cfg.quote_ttl_seconds),
            order_group_id=None
        )
    
    
    def _calculate_price(
        self, 
        orderbook: OrderBookTracker, 
        side: str,
        recent_taker: Optional[str],
        is_exit_order: bool = False
    ) -> int:
        """
        Calculate optimal price with hysteresis and anti-oscillation.
        
        Args:
            orderbook: Current orderbook state
            side: "yes" or "no"
            recent_taker: Recent taker flow direction
            is_exit_order: Whether this is an exit order (bypasses restrictions)
            
        Returns:
            Price in cents for the order
        """
        bid = orderbook.best_bid(side)
        ask = orderbook.best_ask(side)
        base_price = bid.price
        queue_size = bid.size or 0
        current_time = time.time()
        
        # Exit orders bypass all tick movement restrictions - use base price
        if is_exit_order:
            logger.info(f"[TOUCH] Exit order using base price: {base_price} (bypassing queue/cooldown logic)")
            return base_price
        
        
        # Check cooldown after previous shade-down
        if side in self._shade_cooldown:
            cooldown_remaining = self._shade_cooldown[side] - current_time
            if cooldown_remaining > 0:
                logger.info(f"[TOUCH] {side.upper()} in cooldown: {cooldown_remaining:.1f}s left")
                return base_price
        
        # Hysteresis-based queue logic
        if queue_size >= self.cfg.queue_thick_threshold:
            # Shade down on thick queue (but keep spread safe)
            shaded_price = base_price - 1
            if ask.price - shaded_price >= self.cfg.min_spread_cents:
                price = shaded_price
                self._shade_cooldown[side] = current_time + (self.cfg.cooldown_after_shade_ms / 1000.0)
                logger.info(f"[TOUCH] shading {side.upper()} down to {shaded_price} (thick queue={queue_size})")
            else:
                price = base_price
                logger.info(f"[TOUCH] keeping {side.upper()} at {base_price} (shade would break spread)")
        elif queue_size <= self.cfg.queue_thin_threshold and self.cfg.improve_if_last:
            # Improve price on thin queue
            price = base_price + 1
            # Check don't-chase guard: ensure we don't collapse spread
            if ask.price - price >= self.cfg.min_spread_cents:
                logger.info(f"[TOUCH] improving {side.upper()} entry by 1 tick (thin queue={queue_size})")
            else:
                price = base_price
                logger.info(f"[TOUCH] keeping {side.upper()} at {base_price} (improve would break spread)")
        else:
            # In hysteresis band - do nothing
            price = base_price
            logger.info(f"[TOUCH] touching {side.upper()} bid at {base_price} (queue={queue_size}, in hysteresis band)")

        return price

    