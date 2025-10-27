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
        active_order = ActiveOrder(
            order_id=order_id,
            ticker=intent.ticker,
            side=intent.side,
            price_cents=intent.price_cents,
            size=intent.size,
            action=intent.action,
            intent_type=intent.intent_type,
            placed_at=datetime.now(timezone.utc),
            remaining_size=intent.size
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
    
    
    
    def sync_target_orders(self, ticker: str, target_orders: List[OrderIntent]) -> Dict[str, List]:
        """
        Compare target orders with current orders and determine actions needed.
        
        Args:
            ticker: Market ticker
            target_orders: List of OrderIntent representing desired order state
            
        Returns:
            Dict with 'add', 'cancel', 'keep' lists of orders
        """
        current_orders = self.get_active_orders(ticker)
        
        # Create a signature for each order to compare
        def order_signature(order):
            return (order.side, order.action, order.price_cents, order.size)
        
        # Create target signatures
        target_signatures = {order_signature(intent): intent for intent in target_orders}
        current_signatures = {order_signature(order): order for order in current_orders}
        
        # Determine actions
        actions = {
            'add': [],      # Orders to place
            'cancel': [],   # Orders to cancel  
            'keep': []      # Orders to keep (unchanged)
        }
        
        # Find orders to add (in target but not current)
        for sig, intent in target_signatures.items():
            if sig not in current_signatures:
                actions['add'].append(intent)
        
        # Find orders to cancel (in current but not target)
        for sig, order in current_signatures.items():
            if sig not in target_signatures:
                actions['cancel'].append(order)
        
        # Find orders to keep (in both)
        for sig in target_signatures:
            if sig in current_signatures:
                actions['keep'].append(current_signatures[sig])
        
        logger.debug(
            f"[ORDER] Sync for {ticker}: add={len(actions['add'])}, "
            f"cancel={len(actions['cancel'])}, keep={len(actions['keep'])}"
        )
        
        return actions
