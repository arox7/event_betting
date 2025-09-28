"""
Unified TouchMaker Strategy Implementation

Unified TouchMaker handles both entry and exit logic based on current inventory position.
It follows a clear state machine:

- NEUTRAL (no inventory): Bid both YES and NO for spread harvesting
- LONG_YES (positive YES inventory): Exit YES position, no new entries
- LONG_NO (positive NO inventory): Exit NO position, no new entries

This eliminates the conflict between entry and exit strategies by ensuring we never
simultaneously bid for inventory we're trying to sell.

Key Features:
- Inventory-aware state machine (entry vs exit mode)
- Queue-aware pricing for both entry and exit
- Flow-aware exit adjustments
- Ladder support for large positions
- Auto-refresh quotes based on TTL
- Respects inventory caps and spread floors

Behavior:
1. Check current inventory state (YES, NO, or neutral)
2. If neutral: Run entry logic (bid both sides when spread allows)
3. If long YES: Run YES exit logic (sell YES at optimal price)
4. If long NO: Run NO exit logic (sell NO at optimal price)
5. Never simultaneously bid for inventory we're trying to sell

Configuration:
- min_spread_cents: Per-leg spread floor to justify quotes
- bid_size_contracts: Contracts per entry per leg
- exit_size_contracts: Max contracts per exit order
- exit_ladder_threshold: Inventory level that triggers ladder mode
- quote_ttl_seconds: Auto-refresh frequency for orders
- exit_ttl_seconds: Auto-refresh frequency for exit orders
- improve_if_last: Enable 1-tick improvement on thin queues
- queue_small_threshold: Queue size threshold considered "thin"
- queue_big_threshold: Queue size threshold considered "thick"
- max_inventory_contracts: Net YES/NO cap across strategies
- touch_contract_limit: Per-strategy cap for TouchMaker group
- cancel_move_ticks: Mid-move threshold that triggers cancel-and-restage
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple, Optional

from orderbook_tracker import OrderBookTracker
from shared_config import StrategyConfig, OrderIntent, clamp

logger = logging.getLogger(__name__)


class UnifiedTouchMaker:
    """
    Unified TouchMaker strategy implementation.
    
    Handles both entry and exit logic based on current inventory position.
    Uses a clear state machine to avoid conflicts between entry and exit strategies.
    """

    def __init__(self, config: StrategyConfig) -> None:
        """
        Initialize UnifiedTouchMaker with strategy configuration.
        
        Args:
            config: StrategyConfig containing all strategy parameters
        """
        self.cfg = config

    def run(
        self, 
        orderbook: OrderBookTracker,
        inventory_yes: int,
        inventory_no: int,
        recent_taker_yes: Optional[str],
        recent_taker_no: Optional[str],
        inventory_room_func,
        reconcile_func,
        clear_strategy_func,
        strategy_name: str = "unified_touch"
    ) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        """
        Execute unified TouchMaker strategy logic.
        
        Args:
            orderbook: Current orderbook state with YES/NO ladders
            inventory_yes: Current YES inventory (positive = need to exit)
            inventory_no: Current NO inventory (positive = need to exit)
            recent_taker_yes: Recent taker flow direction for YES ("up"/"down"/None)
            recent_taker_no: Recent taker flow direction for NO ("up"/"down"/None)
            inventory_room_func: Function returning (yes_room, no_room) bools
            reconcile_func: Function to reconcile targets against existing orders
            clear_strategy_func: Function to clear strategy state
            strategy_name: Name of strategy for logging/grouping
            
        Returns:
            Tuple of (emitted_order_intents, strategy_summary)
        """
        emitted: List[OrderIntent] = []
        summary: Dict[str, Any] = {
            "group_remaining": 0,  # Will be set by reconcile_func
            "mode": "unknown",
            "targets": [],
            "skipped": [],
        }
        targets: Dict[str, Tuple[int, int]] = {}

        # Step 1: Determine current state based on inventory
        state = self._determine_state(inventory_yes, inventory_no)
        summary["mode"] = state
        logger.info(f"[UNIFIED] Current state: {state} (YES: {inventory_yes}, NO: {inventory_no})")

        # Step 2: Execute strategy based on state
        if state == "NEUTRAL":
            targets, summary = self._run_entry_logic(orderbook, inventory_room_func, summary)
        elif state == "LONG_YES":
            targets, summary = self._run_exit_logic("yes", inventory_yes, recent_taker_yes, orderbook, summary)
        elif state == "LONG_NO":
            targets, summary = self._run_exit_logic("no", inventory_no, recent_taker_no, orderbook, summary)
        else:
            logger.warning(f"[UNIFIED] Unknown state: {state}")
            clear_strategy_func(strategy_name)
            return emitted, summary

        # Step 3: Reconcile live orders against target map
        entries, entry_stats = reconcile_func(strategy_name, targets)
        emitted.extend(entries)
        summary.update(entry_stats)
        
        return emitted, summary

    def _determine_state(self, inventory_yes: int, inventory_no: int) -> str:
        """
        Determine current state based on inventory.
        
        Args:
            inventory_yes: Current YES inventory
            inventory_no: Current NO inventory
            
        Returns:
            State: "NEUTRAL", "LONG_YES", or "LONG_NO"
        """
        if inventory_yes > 0 and inventory_no == 0:
            return "LONG_YES"
        elif inventory_no > 0 and inventory_yes == 0:
            return "LONG_NO"
        elif inventory_yes == 0 and inventory_no == 0:
            return "NEUTRAL"
        else:
            # Edge case: both inventories > 0 (shouldn't happen in normal operation)
            logger.warning(f"[UNIFIED] Both inventories > 0: YES={inventory_yes}, NO={inventory_no}")
            return "LONG_YES" if inventory_yes >= inventory_no else "LONG_NO"

    def _run_entry_logic(
        self, 
        orderbook: OrderBookTracker, 
        inventory_room_func,
        summary: Dict[str, Any]
    ) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, Any]]:
        """
        Run entry logic when in NEUTRAL state.
        
        Args:
            orderbook: Current orderbook state
            inventory_room_func: Function returning (yes_room, no_room) bools
            summary: Summary dict to update
            
        Returns:
            Tuple of (targets, updated_summary)
        """
        targets: Dict[str, Tuple[int, int]] = {}
        
        # Step 1: Inspect top-of-book depth for YES/NO
        best_yes_bid = orderbook.best_bid("yes")
        best_yes_ask = orderbook.best_ask("yes")
        best_no_bid = orderbook.best_bid("no")
        best_no_ask = orderbook.best_ask("no")

        # Validate we have complete market data
        if (
            best_yes_bid.price is None
            or best_yes_ask.price is None
            or best_no_bid.price is None
            or best_no_ask.price is None
        ):
            return targets, summary

        # Check inventory room for both legs
        yes_room, no_room = inventory_room_func()

        # Step 2: Evaluate YES leg for quoting opportunity
        yes_spread = best_yes_ask.price - best_yes_bid.price
        if yes_spread >= self.cfg.min_spread_cents and yes_room:
            price = best_yes_bid.price
            if self._should_improve_yes(best_yes_bid, best_yes_ask, price):
                price = clamp(price + 1, 0, 99)
                logger.info("[UNIFIED] improving YES entry by 1 tick (queue=%s)", best_yes_bid.size)
            
            targets["yes:entry"] = (price, self.cfg.bid_size_contracts)
            summary["targets"].append({
                "leg": "yes", 
                "type": "entry",
                "price": price, 
                "size": self.cfg.bid_size_contracts
            })
        else:
            reason = self._get_yes_skip_reason(yes_spread, yes_room)
            logger.info("[UNIFIED] skip YES entry: %s", reason)
            summary["skipped"].append({"leg": "yes", "type": "entry", "reason": reason})

        # Step 3: Evaluate NO leg for quoting opportunity  
        no_spread = best_no_ask.price - best_no_bid.price
        if no_spread >= self.cfg.min_spread_cents and no_room:
            price = best_no_bid.price
            if self._should_improve_no(best_no_bid, best_no_ask, price):
                price = clamp(price + 1, 0, 99)
                logger.info("[UNIFIED] improving NO entry by 1 tick (queue=%s)", best_no_bid.size)
            
            targets["no:entry"] = (price, self.cfg.bid_size_contracts)
            summary["targets"].append({
                "leg": "no", 
                "type": "entry",
                "price": price, 
                "size": self.cfg.bid_size_contracts
            })
        else:
            reason = self._get_no_skip_reason(no_spread, no_room)
            logger.info("[UNIFIED] skip NO entry: %s", reason)
            summary["skipped"].append({"leg": "no", "type": "entry", "reason": reason})

        return targets, summary

    def _run_exit_logic(
        self, 
        leg: str, 
        inventory: int, 
        recent_taker: Optional[str],
        orderbook: OrderBookTracker,
        summary: Dict[str, Any]
    ) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, Any]]:
        """
        Run exit logic when in LONG_YES or LONG_NO state.
        
        Args:
            leg: "yes" or "no"
            inventory: Current inventory for this leg
            recent_taker: Recent taker flow direction
            orderbook: Current orderbook state
            summary: Summary dict to update
            
        Returns:
            Tuple of (targets, updated_summary)
        """
        targets: Dict[str, Tuple[int, int]] = {}
        
        if inventory <= 0:
            logger.info(f"[UNIFIED] No {leg.upper()} inventory to exit")
            return targets, summary

        # Build exit ladder based on inventory size
        if inventory >= self.cfg.exit_ladder_threshold:
            # Large inventory - build ladder
            ladder = self._build_exit_ladder(leg, inventory, orderbook)
            for kind, price, size in ladder:
                if price is not None and size > 0:
                    targets[kind] = (price, size)
                    summary["targets"].append({
                        "leg": leg,
                        "type": "exit",
                        "kind": kind,
                        "price": price,
                        "size": size
                    })
        else:
            # Small inventory - single exit order
            price = self._compute_exit_price(leg, recent_taker, orderbook)
            if price is not None:
                targets[f"exit-{leg}"] = (price, min(inventory, self.cfg.exit_size_contracts))
                summary["targets"].append({
                    "leg": leg,
                    "type": "exit",
                    "price": price,
                    "size": min(inventory, self.cfg.exit_size_contracts)
                })

        return targets, summary

    def _should_improve_yes(
        self, 
        best_yes_bid: Any, 
        best_yes_ask: Any, 
        base_price: int
    ) -> bool:
        """Determine if YES leg should improve by 1 tick for entry."""
        return (
            self.cfg.improve_if_last
            and best_yes_bid.size < self.cfg.queue_small_threshold
            and best_yes_ask.price - (base_price + 1) >= self.cfg.min_spread_cents
        )

    def _should_improve_no(
        self, 
        best_no_bid: Any, 
        best_no_ask: Any, 
        base_price: int
    ) -> bool:
        """Determine if NO leg should improve by 1 tick for entry."""
        return (
            self.cfg.improve_if_last
            and best_no_bid.size < self.cfg.queue_small_threshold
            and best_no_ask.price - (base_price + 1) >= self.cfg.min_spread_cents
        )

    def _compute_exit_price(self, leg: str, recent_taker: Optional[str], orderbook: OrderBookTracker) -> Optional[int]:
        """Compute optimal exit price for a leg."""
        best_bid = orderbook.best_bid(leg)
        best_ask = orderbook.best_ask(leg)
        if best_bid.price is None or best_ask.price is None:
            return None

        # Step 1: baseline exit price is the higher of best ask or min-spread over best bid
        min_spread = self.cfg.min_spread_cents
        price = max(best_ask.price, best_bid.price + min_spread)

        if best_ask.size is not None:
            # Step 2: adjust based on queue size—thin queues invite improvement, heavy queues allow widening
            if best_ask.size < self.cfg.queue_small_threshold and best_ask.price - best_bid.price >= min_spread + 1:
                price = max(best_ask.price - 1, best_bid.price + min_spread)
            elif best_ask.size >= self.cfg.queue_big_threshold:
                price = min(100, best_ask.price + 1)

        if recent_taker == "down" and price < 100:
            # Step 3: if recent flow sold into us, nudge price toward the top to increase fill odds
            price = min(100, price + 1)

        price = max(price, best_bid.price + min_spread)
        price = min(price, 100)
        return price

    def _build_exit_ladder(self, leg: str, inventory: int, orderbook: OrderBookTracker) -> List[Tuple[str, int, int]]:
        """Build exit ladder for large inventory positions."""
        best_bid = orderbook.best_bid(leg)
        best_ask = orderbook.best_ask(leg)
        if best_bid.price is None or best_ask.price is None:
            return []

        # Step 1: split inventory across a few rungs with diminishing sizes
        min_spread = self.cfg.min_spread_cents
        inv = inventory
        rung_sizes = [
            int(max(1, inv * 0.3)), 
            int(max(1, inv * 0.4)), 
            max(0, inv - int(inv * 0.3) - int(inv * 0.4))
        ]
        rung_sizes = [min(size, self.cfg.exit_size_contracts) for size in rung_sizes if size > 0]
        if not rung_sizes:
            return []

        # Step 2: precompute candidate prices around the offer; later rungs use wider quotes
        prices = [
            max(best_ask.price - 1, best_bid.price + min_spread),
            max(best_ask.price, best_bid.price + min_spread),
            max(min(best_ask.price + 1, 100), best_bid.price + min_spread),
        ]

        ladder: List[Tuple[str, int, int]] = []
        for idx, size in enumerate(rung_sizes):
            price = prices[min(idx, len(prices) - 1)]
            ladder.append((f"exit-{leg}-{idx+1}", price, size))
        return ladder

    def _get_yes_skip_reason(self, yes_spread: int, yes_room: bool) -> str:
        """Get human-readable reason for skipping YES leg entry."""
        if not yes_room:
            return "inventory room exhausted"
        return f"spread {yes_spread} < min {self.cfg.min_spread_cents}"

    def _get_no_skip_reason(self, no_spread: int, no_room: bool) -> str:
        """Get human-readable reason for skipping NO leg entry."""
        if not no_room:
            return "inventory room exhausted"
        return f"spread {no_spread} < min {self.cfg.min_spread_cents}"
