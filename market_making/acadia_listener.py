"""
Market listener for Kalshi WebSocket events.

This module handles processing WebSocket messages for individual markets,
maintaining order book state, and updating the strategy with market data.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

from kalshi.client import KalshiAPIClient
from kalshi.websocket import KalshiWebSocketClient
from market_making.acadia import AcadiaStrategy
from market_making.market_types import MarketConfig, PositionState
from market_making.orderbook_tracker import OrderBookTracker, OrderBookError

logger = logging.getLogger(__name__)


class MarketListener:
    """Listens to Kalshi websockets for a single market and updates strategy."""
    
    def __init__(
        self,
        market_config: MarketConfig,
        ws_client: KalshiWebSocketClient,
        api_client: KalshiAPIClient,
        strategy: AcadiaStrategy,
    ) -> None:
        self.market_config = market_config
        self.ticker = market_config.ticker
        self.ws_client = ws_client
        self.api_client = api_client
        self.strategy = strategy
        self.orderbook_tracker = OrderBookTracker()
        
        # Recent events for debugging
        self.recent_trades: List[Dict[str, Any]] = []
        self.recent_positions: Dict[str, Dict[str, Any]] = {}
        self.recent_fills: List[Dict[str, Any]] = []
        
        logger.info(f"[{self.ticker}] Initialized market listener")
    
    async def process_message(self, envelope: Dict[str, Any]) -> None:
        """Process a single message from the websocket."""
        channel = envelope.get("channel")
        message_type = envelope.get("message_type")
        payload = envelope.get("data", {})
        
        if channel == "orderbook_delta":
            await self._on_orderbook(payload)
        elif channel == "trade":
            await self._on_public_trade(payload)
        elif channel == "ticker":
            await self._on_ticker(payload)
        elif channel == "fill":
            await self._on_fill(payload)
        elif channel == "market_positions":
            await self._on_market_position(payload)
        else:
            logger.debug(f"[{self.ticker}] Unhandled channel {channel} ({message_type})")
    
    async def _on_orderbook(self, payload: Dict[str, Any]) -> None:
        """Handle orderbook updates."""
        message_type = payload.get("type")
        msg_body = payload.get("msg") or payload
        market_ticker = msg_body.get("market_ticker")
        
        if not market_ticker:
            market_ticker = (msg_body.get("orderbook") or {}).get("market_ticker")
        
        if market_ticker != self.ticker:
            return
        
        # Normalize ladder fields
        ladder_body = msg_body.get("orderbook") or msg_body
        ladder_body = dict(ladder_body)
        if "market_ticker" not in ladder_body and market_ticker:
            ladder_body["market_ticker"] = market_ticker
        
        levels_block = ladder_body.get("levels")
        if isinstance(levels_block, dict):
            ladder_body.setdefault("yes", levels_block.get("yes", []))
            ladder_body.setdefault("no", levels_block.get("no", []))
        
        ladder_body.setdefault("yes", [])
        ladder_body.setdefault("no", [])
        
        try:
            if message_type == "orderbook_snapshot":
                self.orderbook_tracker.apply_snapshot(ladder_body)
                logger.info(
                    f"[{self.ticker}] Snapshot applied: yes_levels={len(ladder_body['yes'])}, "
                    f"no_levels={len(ladder_body['no'])}"
                )
            elif message_type == "orderbook_delta":
                self.orderbook_tracker.apply_delta(ladder_body)
            else:
                return
        except OrderBookError as exc:
            logger.error(f"[{self.ticker}] Orderbook error: {exc}")
            return
        
        # Update strategy with new orderbook state
        if self.ticker in self.strategy.market_states:
            best_yes_bid = self.orderbook_tracker.best_bid("yes")
            best_yes_ask = self.orderbook_tracker.best_ask("yes")
            best_no_bid = self.orderbook_tracker.best_bid("no")
            best_no_ask = self.orderbook_tracker.best_ask("no")
            
            market_state = self.strategy.market_states[self.ticker]
            market_state.yes_bid = best_yes_bid.price
            market_state.yes_ask = best_yes_ask.price
            market_state.no_bid = best_no_bid.price
            market_state.no_ask = best_no_ask.price
            
            # Debug: Show top levels of each side
            yes_levels = self.orderbook_tracker.top_levels("yes", max_levels=3)
            no_levels = self.orderbook_tracker.top_levels("no", max_levels=3)
            
            logger.info(
                f"[{self.ticker}] Orderbook: "
                f"YES {best_yes_bid.price}/{best_yes_ask.price}, "
                f"NO {best_no_bid.price}/{best_no_ask.price} | "
                f"YES levels: {yes_levels}, NO levels: {no_levels}"
            )
            
            # Get strategy actions (add/cancel/keep orders)
            actions = self.strategy.generate_orders(self.ticker)
            
            # Log order sync summary
            if actions['keep'] and not actions['add'] and not actions['cancel']:
                logger.debug(
                    f"[{self.ticker}] Keeping {len(actions['keep'])} orders unchanged "
                    f"(likely MQT or hysteresis throttling)"
                )
            
            # Execute the actions
            actions_taken = await self._execute_order_actions(actions)
            
            # Only log position summary if we took actions or if position changed
            if actions_taken:
                self._log_position_summary()
    
    async def _execute_order_actions(self, actions: Dict[str, List]) -> bool:
        """
        Execute order actions returned by the strategy.
        
        Args:
            actions: Dict with 'add', 'cancel', 'keep' lists
            
        Returns:
            True if any actions were taken (add/cancel), False if only keep
        """
        actions_taken = False
        
        # Cancel orders that are no longer wanted
        for order in actions.get('cancel', []):
            if self.strategy.dry_run:
                logger.info(f"[{self.ticker}] 🧪 DRY RUN: Would cancel order: {order.action} {order.side} @ {order.price_cents}¢ (ID: {order.order_id[:12]}...)")
                # In dry run, still remove from tracking to simulate the cancellation
                self.strategy.order_manager.remove_order(order.order_id, self.ticker)
                actions_taken = True
            else:
                logger.info(f"[{self.ticker}] Cancelling order: {order.action} {order.side} @ {order.price_cents}¢ (ID: {order.order_id[:12]}...)")
                # Cancel the order on Kalshi
                success = await self.api_client.cancel_order(order.order_id)
                if success:
                    # Remove from local tracking
                    self.strategy.order_manager.remove_order(order.order_id, self.ticker)
                    actions_taken = True
                else:
                    logger.error(f"[{self.ticker}] Failed to cancel order {order.order_id[:12]}...")
        
        # Add new orders
        for intent in actions.get('add', []):
            if self.strategy.dry_run:
                logger.info(f"[{self.ticker}] 🧪 DRY RUN: Would place order: {intent.action} {intent.side} {intent.size} @ {intent.price_cents}¢ ({intent.intent_type})")
                # In dry run, add a fake order to tracking to simulate the placement
                fake_order_id = f"dry_run_{uuid.uuid4().hex[:16]}"
                self.strategy.order_manager.add_order(fake_order_id, intent)
                actions_taken = True
            else:
                # Determine if this is an aggressive exit that should cross the spread
                post_only = intent.intent_type != "aggressive_exit"
                
                logger.info(
                    f"[{self.ticker}] Placing order: {intent.action} {intent.side} {intent.size} @ {intent.price_cents}¢ "
                    f"(type={intent.intent_type}, post_only={post_only})"
                )
                
                # Place the order on Kalshi
                order_response = await self.api_client.create_order(
                    ticker=self.ticker,
                    action=intent.action,
                    side=intent.side,
                    count=intent.size,
                    price_cents=intent.price_cents,
                    order_type="limit",
                    post_only=post_only
                )
                
                if order_response and 'order_id' in order_response:
                    order_id = order_response['order_id']
                    # Add to local tracking with the real Kalshi order ID
                    self.strategy.order_manager.add_order(order_id, intent)
                    actions_taken = True
                else:
                    logger.error(f"[{self.ticker}] Failed to place order: {intent.action} {intent.side} {intent.size} @ {intent.price_cents}¢")
        
        # Keep orders (no action needed, they're already tracked)
        keep_count = len(actions.get('keep', []))
        if keep_count > 0:
            logger.debug(f"[{self.ticker}] Keeping {keep_count} existing orders unchanged")
        
        return actions_taken
    
    def _log_position_summary(self) -> None:
        """Log position summary when position changes or actions are taken."""
        if self.ticker in self.strategy.position_states:
            position_state = self.strategy.position_states[self.ticker]
            
            # Format values safely (handle None)
            pnl_str = f"{position_state.unrealized_pnl_cents}c" if position_state.unrealized_pnl_cents is not None else "N/A"
            return_str = f"{position_state.position_return_pct:.1f}%" if position_state.position_return_pct is not None else "N/A"
            
            logger.info(
                f"[{self.ticker}] Position Summary: "
                f"pos={position_state.position}, "
                f"entry_yes={position_state.avg_entry_price_yes}, "
                f"entry_no={position_state.avg_entry_price_no}, "
                f"pnl={pnl_str}, "
                f"return={return_str}"
            )
    
    async def _on_public_trade(self, payload: Dict[str, Any]) -> None:
        """Handle public trade updates."""
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        
        if market_ticker != self.ticker:
            return
        
        self.recent_trades.append({
            "timestamp": datetime.now(timezone.utc),
            "payload": body,
        })
        if len(self.recent_trades) > 100:
            self.recent_trades.pop(0)
        
        logger.info(
            f"[{self.ticker}] Trade: taker={body.get('taker_side')}, "
            f"yes={body.get('yes_price')}¢, no={body.get('no_price')}¢, "
            f"size={body.get('count', 0)}"
        )
    
    async def _on_ticker(self, payload: Dict[str, Any]) -> None:
        """Handle ticker updates."""
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        
        if market_ticker != self.ticker:
            return
        
        # Update market state with ticker data if available
        if self.ticker in self.strategy.market_states:
            market_state = self.strategy.market_states[self.ticker]
            if "last_price" in body:
                market_state.last_price = body["last_price"]
            if "volume" in body:
                market_state.yes_volume = body.get("volume")
    
    async def _on_fill(self, payload: Dict[str, Any]) -> None:
        """Handle fill updates."""
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        
        if market_ticker != self.ticker:
            return
        
        self.recent_fills.append({
            "timestamp": datetime.now(timezone.utc),
            "payload": body,
        })
        if len(self.recent_fills) > 50:
            self.recent_fills.pop(0)
        
        # Update position based on fill
        side = body.get("side", "").lower()
        action = body.get("action", "").lower()
        count = int(body.get("count", 0) or 0)
        
        # Get fill price based on side
        if side == "yes":
            price_cents = body.get("yes_price", 0)
        elif side == "no":
            price_cents = body.get("no_price", 0)
        else:
            price_cents = 0
        
        # Debug: log fill message if price is 0
        if price_cents == 0:
            logger.warning(f"[{self.ticker}] Fill price is 0! Full fill payload: {body}")
            
        order_id = body.get("order_id")
        
        # Update order tracking if we have the order_id
        if order_id:
            filled_count = int(body.get("count", 0) or 0)
            self.strategy.order_manager.update_order_fill(order_id, self.ticker, filled_count)
        
        if count > 0 and self.ticker in self.strategy.position_states:
            position_state = self.strategy.position_states[self.ticker]
            
            # Update entry prices and position
            old_position = position_state.position
            
            if action == "buy":
                if side == "yes":
                    position_state.position += count
                    # Update average entry price for YES
                    self._update_entry_price(position_state, "yes", count, price_cents, old_position)
                elif side == "no":
                    position_state.position -= count
                    # Update average entry price for NO
                    self._update_entry_price(position_state, "no", count, price_cents, -old_position)
            elif action == "sell":
                if side == "yes":
                    position_state.position -= count
                    # Selling YES reduces average entry price
                    self._update_entry_price(position_state, "yes", -count, price_cents, old_position)
                elif side == "no":
                    position_state.position += count
                    # Selling NO reduces average entry price
                    self._update_entry_price(position_state, "no", -count, price_cents, -old_position)
            
            logger.info(
                f"[{self.ticker}] Fill: {action} {side} {count} contracts @ {price_cents}, "
                f"new position={position_state.position}, "
                f"avg_entry_yes={position_state.avg_entry_price_yes}, "
                f"avg_entry_no={position_state.avg_entry_price_no}"
            )
            
            # Regenerate orders after fill to immediately place exit orders if needed
            # and update market making quotes based on new position
            actions = self.strategy.generate_orders(self.ticker)
            actions_taken = await self._execute_order_actions(actions)
            
            if actions_taken:
                logger.info(
                    f"[{self.ticker}] Post-fill order update: "
                    f"added={len(actions['add'])}, cancelled={len(actions['cancel'])}, kept={len(actions['keep'])}"
                )
            
            # Log position summary after fill (position changed)
            self._log_position_summary()
    
    def _update_entry_price(self, position_state: PositionState, side: str, count: int, price_cents: int, current_position: int) -> None:
        """
        Update average entry price based on fill.
        
        Args:
            position_state: Position state to update
            side: "yes" or "no"
            count: Number of contracts (positive for buy, negative for sell)
            price_cents: Fill price in cents
            current_position: Current position before this fill
        """
        if side == "yes":
            current_avg = position_state.avg_entry_price_yes or 0.0
            current_qty = max(0, current_position)  # Only count positive YES position
            
            if count > 0:  # Buying YES
                if current_qty > 0:
                    # Update average: (old_qty * old_avg + new_qty * new_price) / total_qty
                    new_qty = current_qty + count
                    position_state.avg_entry_price_yes = (current_qty * current_avg + count * price_cents) / new_qty
                else:
                    # First YES purchase
                    position_state.avg_entry_price_yes = price_cents
            else:  # Selling YES
                if current_qty > 0:
                    new_qty = max(0, current_qty + count)  # count is negative
                    if new_qty > 0:
                        # Keep same average price (FIFO assumption)
                        position_state.avg_entry_price_yes = current_avg
                    else:
                        # Sold all YES position
                        position_state.avg_entry_price_yes = None
        
        elif side == "no":
            current_avg = position_state.avg_entry_price_no or 0.0
            current_qty = max(0, -current_position)  # Only count positive NO position (negative YES position)
            
            if count > 0:  # Buying NO (reduces YES position)
                if current_qty > 0:
                    # Update average: (old_qty * old_avg + new_qty * new_price) / total_qty
                    new_qty = current_qty + count
                    position_state.avg_entry_price_no = (current_qty * current_avg + count * price_cents) / new_qty
                else:
                    # First NO purchase
                    position_state.avg_entry_price_no = price_cents
            else:  # Selling NO (increases YES position)
                if current_qty > 0:
                    new_qty = max(0, current_qty + count)  # count is negative
                    if new_qty > 0:
                        # Keep same average price (FIFO assumption)
                        position_state.avg_entry_price_no = current_avg
                    else:
                        # Sold all NO position
                        position_state.avg_entry_price_no = None
    
    async def _on_market_position(self, payload: Dict[str, Any]) -> None:
        """Handle market position updates."""
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        
        if not market_ticker:
            return
        
        position_contracts = int(body.get("position", 0) or 0)
        exposure_centi_cents = body.get("position_cost") or body.get("market_exposure_cc")
        exposure_dollars = (
            exposure_centi_cents / 10000.0 
            if isinstance(exposure_centi_cents, (int, float)) 
            else None
        )
        
        self.recent_positions[market_ticker] = {
            "position_contracts": position_contracts,
            "exposure_dollars": exposure_dollars,
            "timestamp": datetime.now(timezone.utc),
        }
        
        if market_ticker == self.ticker and self.ticker in self.strategy.position_states:
            old_position = self.strategy.position_states[self.ticker].position
            self.strategy.position_states[self.ticker].position = position_contracts
            logger.info(
                f"[{self.ticker}] Position update: {position_contracts} contracts (was {old_position})"
            )
            
            # If position changed, regenerate orders to ensure exit orders are placed
            if old_position != position_contracts:
                actions = self.strategy.generate_orders(self.ticker)
                actions_taken = await self._execute_order_actions(actions)
                
                if actions_taken:
                    logger.info(
                        f"[{self.ticker}] Post-position-sync order update: "
                        f"added={len(actions['add'])}, cancelled={len(actions['cancel'])}, kept={len(actions['keep'])}"
                    )
                
                # Log position summary after position sync
                self._log_position_summary()

