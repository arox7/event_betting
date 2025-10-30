"""
Order Management System for Market Making Bot

This module handles the lifecycle of orders including:
- Order placement and tracking
- Order cancellation and cleanup
- Stale order detection
- Price validation
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from market_making.market_types import (
    ActiveOrder, OrderIntent, OrderManagerState, 
    MarketState, PositionState
)

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages the lifecycle of orders for the market making bot.
    
    Key responsibilities:
    1. Track active orders and their states
    2. Detect and cancel stale orders
    3. Validate order prices against current market
    4. Manage order placement and cancellation
    """
    
    def __init__(self):
        self.state = OrderManagerState(active_orders={})
        self.max_order_age_seconds = 60  # Orders expire after 60 seconds
        self.max_price_deviation = 5     # Cancel if price moves >5 cents away
    
    def add_order(self, order_id: str, intent: OrderIntent) -> ActiveOrder:
        """Add a new active order to tracking."""
        now = datetime.now(timezone.utc)
        active_order = ActiveOrder(
            order_id=order_id,
            ticker=intent.ticker,
            side=intent.side,
            price_cents=intent.price_cents,
            size=intent.size,
            action=intent.action,
            intent_type=intent.intent_type,
            placed_at=now,
            remaining_size=intent.size,
            last_modified_at=now
        )
        
        if intent.ticker not in self.state.active_orders:
            self.state.active_orders[intent.ticker] = []
        
        self.state.active_orders[intent.ticker].append(active_order)
        
        logger.info(
            f"[ORDER] Placed {intent.action} {intent.side} {intent.size} @ {intent.price_cents} "
            f"(ID: {order_id[:8]}..., Type: {intent.intent_type})"
        )
        
        return active_order
    
    def remove_order(self, order_id: str, ticker: str) -> Optional[ActiveOrder]:
        """Remove an order from tracking."""
        if ticker not in self.state.active_orders:
            return None
        
        orders = self.state.active_orders[ticker]
        for i, order in enumerate(orders):
            if order.order_id == order_id:
                removed_order = orders.pop(i)
                logger.info(f"[ORDER] Removed {order.action} {order.side} (ID: {order_id[:8]}...)")
                
                # Clean up empty ticker lists
                if not orders:
                    del self.state.active_orders[ticker]
                
                return removed_order
        
        return None
    
    def update_order_fill(self, order_id: str, ticker: str, filled_size: int) -> Optional[ActiveOrder]:
        """Update order size after a partial fill."""
        if ticker not in self.state.active_orders:
            return None
        
        for order in self.state.active_orders[ticker]:
            if order.order_id == order_id:
                order.remaining_size -= filled_size
                logger.info(
                    f"[ORDER] Fill: {order.action} {order.side} {filled_size} "
                    f"(Remaining: {order.remaining_size}, ID: {order_id[:8]}...)"
                )
                return order
        
        return None
    
    def get_orders_to_cancel(self, ticker: str, market_state: MarketState) -> List[ActiveOrder]:
        """Get orders that should be cancelled due to staleness or price movement."""
        if ticker not in self.state.active_orders:
            return []
        
        orders_to_cancel = []
        
        for order in self.state.active_orders[ticker]:
            should_cancel = False
            reason = ""
            
            # Check if order is too old
            if order.is_stale(self.max_order_age_seconds):
                should_cancel = True
                reason = "stale_age"
            
            # Check if price has moved too far
            elif order.is_price_stale(
                market_state.yes_bid if order.side == "yes" else market_state.no_bid,
                market_state.yes_ask if order.side == "yes" else market_state.no_ask,
                self.max_price_deviation
            ):
                should_cancel = True
                reason = "stale_price"
            
            if should_cancel:
                orders_to_cancel.append(order)
                logger.info(
                    f"[ORDER] Marked for cancellation: {order.action} {order.side} "
                    f"@ {order.price_cents} (Reason: {reason}, ID: {order.order_id[:8]}...)"
                )
        
        return orders_to_cancel
    
    def get_active_orders(self, ticker: str) -> List[ActiveOrder]:
        """Get all active orders for a ticker."""
        return self.state.active_orders.get(ticker, [])
    
    def has_active_orders(self, ticker: str) -> bool:
        """Check if there are any active orders for a ticker."""
        return len(self.get_active_orders(ticker)) > 0
    
    def get_order_summary(self, ticker: str) -> str:
        """Get a summary of active orders for a ticker."""
        orders = self.get_active_orders(ticker)
        if not orders:
            return "No active orders"
        
        summary_parts = []
        for order in orders:
            age = (datetime.now(timezone.utc) - order.placed_at).total_seconds()
            summary_parts.append(
                f"{order.action} {order.side} {order.remaining_size} @ {order.price_cents} "
                f"(age: {age:.0f}s, type: {order.intent_type})"
            )
        
        return "; ".join(summary_parts)
    
    
    
    def sync_target_orders(
        self, 
        ticker: str, 
        target_orders: List[OrderIntent],
        min_price_delta_cents: int = 1,
        market_state: Optional['MarketState'] = None
    ) -> Dict[str, List]:
        """
        Compare target orders with current orders and determine actions needed.
        
        Decision Priority (highest to lowest):
        1. STALE orders → Cancel immediately (safety)
        2. EXACT MATCH → Keep (no point replacing identical order)
        3. Price delta too small → Keep (hysteresis)
        4. Significant change → Cancel & Replace
        
        Args:
            ticker: Market ticker
            target_orders: List of OrderIntent representing desired order state
            min_price_delta_cents: Minimum price movement to trigger requote (hysteresis)
            market_state: Optional market state for staleness checks
            
        Returns:
            Dict with 'add', 'cancel', 'keep' lists of orders
        """
        current_orders = self.get_active_orders(ticker)
        stale_orders = self.get_orders_to_cancel(ticker, market_state) if market_state else []
        stale_order_ids = {o.order_id for o in stale_orders}
        
        if stale_orders:
            logger.info(f"[ORDER] {ticker}: Found {len(stale_orders)} stale orders")
        
        # Helper functions for order matching
        def exact_sig(order) -> tuple:
            """Signature for exact match: (side, action, price, size)"""
            return (order.side, order.action, order.price_cents, order.size)
        
        def type_sig(order) -> tuple:
            """Signature for order type: (side, action)"""
            return (order.side, order.action)
        
        # Build lookup tables
        current_by_exact = {exact_sig(o): o for o in current_orders}
        current_by_type = {}
        for order in current_orders:
            sig = type_sig(order)
            current_by_type.setdefault(sig, []).append(order)
        
        target_types = {type_sig(intent) for intent in target_orders}
        processed_order_ids = set()  # Track which current orders we've handled (by ID)
        
        actions = {'add': [], 'cancel': [], 'keep': []}
        
        # ==================== PHASE 1: Match targets to current orders ====================
        for intent in target_orders:
            # Priority 1: Check for exact match
            if exact_sig(intent) in current_by_exact:
                existing = current_by_exact[exact_sig(intent)]
                if existing.order_id in stale_order_ids:
                    # Stale: replace even if exact match
                    actions['cancel'].append(existing)
                    actions['add'].append(intent)
                    logger.info(f"[ORDER] {ticker}: Replacing stale order (exact match)")
                else:
                    # Exact match, not stale: keep it
                    actions['keep'].append(existing)
                processed_order_ids.add(existing.order_id)
                continue
            
            # Priority 2: Check for similar order (same type, different price/size)
            matching = current_by_type.get(type_sig(intent), [])
            similar = [o for o in matching if o.order_id not in processed_order_ids]
            
            if not similar:
                # No matching order exists: add new
                actions['add'].append(intent)
                continue
            
            existing = similar[0]  # Take first unprocessed match
            processed_order_ids.add(existing.order_id)
            
            # Priority 3: Is it stale?
            if existing.order_id in stale_order_ids:
                actions['cancel'].append(existing)
                actions['add'].append(intent)
                logger.info(f"[ORDER] {ticker}: Replacing stale order")
                continue
            
            # Priority 4: Is price delta significant (hysteresis)?
            price_delta = abs(intent.price_cents - existing.price_cents)
            size_delta = abs(intent.size - existing.remaining_size)
            
            if price_delta < min_price_delta_cents and size_delta == 0:
                actions['keep'].append(existing)
                logger.debug(f"[ORDER] {ticker}: Keeping (price delta {price_delta}¢ < threshold)")
                continue
            
            # Priority 5: Significant change → replace
            actions['cancel'].append(existing)
            actions['add'].append(intent)
            if price_delta >= min_price_delta_cents:
                logger.warning(
                    f"[ORDER] {ticker}: Cancel/replace due to PRICE: "
                    f"{existing.action} {existing.side} {existing.price_cents}¢→{intent.price_cents}¢ "
                    f"(Δ={price_delta}¢, threshold={min_price_delta_cents}¢)"
                )
            else:
                logger.warning(
                    f"[ORDER] {ticker}: Cancel/replace due to SIZE: "
                    f"{existing.action} {existing.side} @ {existing.price_cents}¢: "
                    f"{existing.remaining_size}→{intent.size} (Δ={size_delta})"
                )
        
        # ==================== PHASE 2: Cancel unmatched current orders ====================
        for order in current_orders:
            if order.order_id in processed_order_ids:
                continue  # Already handled
            
            # Stale orders: cancel immediately
            if order.order_id in stale_order_ids:
                actions['cancel'].append(order)
                logger.info(f"[ORDER] {ticker}: Cancelling stale order (orphaned)")
                continue
            
            # Order type not in targets: cancel it
            if type_sig(order) not in target_types:
                actions['cancel'].append(order)
        
        logger.debug(
            f"[ORDER] Sync for {ticker}: add={len(actions['add'])}, "
            f"cancel={len(actions['cancel'])}, keep={len(actions['keep'])}"
        )
        
        return actions
