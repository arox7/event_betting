"""Strategy engine building mock Kalshi orders for multiple maker profiles."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from orderbook_tracker import OrderBookTracker
from touchmaker import TouchMaker
from shared_config import StrategyConfig, OrderIntent, OrderGroupState, _now_str, clamp

logger = logging.getLogger(__name__)
UUID4_HEX_LEN = 36


class StrategyExecutionError(RuntimeError):
    """Raised when a live API request fails during strategy execution."""

    def __init__(self, method: str, path: str, status_code: int, body: Optional[str]) -> None:
        super().__init__(f"{method} {path} failed: status={status_code} body={body}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body or ""


# ---------------------------------------------------------------------------
# Datamodels
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# Strategy Engine
# ---------------------------------------------------------------------------


class StrategyEngine:
    """Aggregates strategies, generates mock order payloads, and prints them."""

    def __init__(self, cfg: StrategyConfig, order_executor=None) -> None:
        # Strategy configuration and optional live executor.
        self.cfg = cfg
        self.order_executor = order_executor
        self.orderbook: Optional[OrderBookTracker] = None
        
        # Initialize strategy implementations
        self.touchmaker = TouchMaker(cfg)

        # Single source of truth for live orders
        self.live_orders: Dict[str, OrderIntent] = {}
        
        # Group state for cap accounting
        self.groups: Dict[str, OrderGroupState] = {
            "touch": OrderGroupState("touch", cfg.touch_contract_limit),
            "depth": OrderGroupState("depth", cfg.depth_contract_limit),
            "band": OrderGroupState("band", cfg.band_contract_limit),
        }

        # State derived from fills/positions that drive quoting decisions.
        self.net_yes_position: int = 0
        self.last_mid_cents: Optional[float] = None
        self.recent_taker_yes: Optional[str] = None
        self.recent_taker_no: Optional[str] = None
        self._last_summary: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Execution helpers (single place to honor live_mode)
    # ------------------------------------------------------------------

    def _should_execute(self) -> bool:
        return bool(self.cfg.live_mode and self.order_executor)

    def _execute_request(self, method: str, path: str, json_data: Dict[str, Any]):
        if not self._should_execute():
            logger.info("--> Would %s %s with payload:\n%s", method, path, json.dumps(json_data, indent=2))
            return None
        
        # DEBUG: Track order patterns that might trigger 409s
        if path == "/portfolio/orders" and json_data.get('client_order_id'):
            client_order_id = json_data['client_order_id']
            logger.debug(f"[STRATEGY DEBUG] Placing order: {client_order_id}")
            logger.debug(f"[STRATEGY DEBUG] Order payload: {json_data}")
            
            # Track timing patterns
            import time
            current_time = time.time()
            if hasattr(self, '_last_order_time'):
                time_since_last = current_time - self._last_order_time
                logger.debug(f"[STRATEGY DEBUG] Time since last order: {time_since_last:.2f}s")
            self._last_order_time = current_time
            
            # Track order count in this session
            if not hasattr(self, '_order_count'):
                self._order_count = 0
            self._order_count += 1
            logger.debug(f"[STRATEGY DEBUG] Order count in session: {self._order_count}")
        
        try:
            response = self.order_executor.http_client.make_authenticated_request(method, path, json_data=json_data)
        except Exception as exc:
            logger.error("[LIVE %s] failed: %s", method, exc, exc_info=True)
            raise StrategyExecutionError(method, path, 0, str(exc))

        logger.info("[LIVE %s] status=%s body=%s", method, response.status_code, getattr(response, "text", ""))
        if getattr(response, "status_code", 0) >= 400:
            raise StrategyExecutionError(method, path, response.status_code, getattr(response, "text", ""))
        return response

    # ------------------------------------------------------------------
    # Public hooks
    # ------------------------------------------------------------------

    def update_orderbook(self, tracker: OrderBookTracker) -> None:
        # Listener feeds us a mutable tracker whenever the ladder changes; keep the
        # reference so subsequent refresh() calls inspect the latest depth/quotes.
        self.orderbook = tracker

    def on_public_trade(self, payload: Dict[str, Any]) -> None:
        # Step 1: unpack tape details; trades include implied YES/NO prices and taker info.
        yes_price = payload.get("yes_price")
        no_price = payload.get("no_price")
        size = payload.get("count", 0)
        taker = payload.get("taker_side")
        print(
            f"{_now_str()}  [TRADE] {self.cfg.ticker} taker={taker} yes={yes_price}¢ no={no_price}¢ size={size}"
        )

        # Step 2: remember which side the aggressor hit so exit makers can lean away.
        if taker == "yes":
            self.recent_taker_yes = "up"
            self.recent_taker_no = "down"
        elif taker == "no":
            self.recent_taker_yes = "down"
            self.recent_taker_no = "up"

    def on_private_fill(self, payload: Dict[str, Any]) -> None:
        # Step 1: extract client order reference and filled size (ignore malformed events).
        client_id = payload.get("client_order_id")
        count = int(payload.get("count", 0) or 0)
        if not client_id or count <= 0:
            return

        # Step 2: locate the pending intent in whichever strategy produced it and update that
        # group's accounting so cap checks remain accurate.
        for strategy, group in self.groups.items():
            if client_id in group.pending:
                group.remove_intent(client_id)
                # Update group position (absolute value)
                if "yes" in client_id:
                    group.filled_contracts = abs(self.net_yes_position + count)
                elif "no" in client_id:
                    group.filled_contracts = abs(self.net_yes_position - count)
                
                print(f"{_now_str()}  [GROUP] {group.name} fill +{count} (position: {group.filled_contracts})")
                if group.remaining() <= 0:
                    print(f"{_now_str()}  [GROUP] {group.name} cap hit -> would cancel remaining orders")
                
                # Step 3: Update net position based on the fill
                if "yes" in client_id:
                    self.net_yes_position += count
                elif "no" in client_id:
                    self.net_yes_position -= count
                
                # Step 4: Remove from live_orders so sync can create new orders
                self.live_orders.pop(client_id, None)
                break

    def on_position_update(self, net_yes_position: int) -> None:
        # Single scalar from the listener representing YES contracts minus NO contracts.
        # Downstream helpers (`inv_yes`/`inv_no`) convert this into per-leg inventory.
        self.net_yes_position = net_yes_position


    def refresh(self) -> List[OrderIntent]:
        """Recompute strategy actions and return emitted intents for logging."""
        emitted: List[OrderIntent] = []
        self._last_summary = {}
        if not self.orderbook:
            self._last_summary["reason"] = "no_orderbook"
            return emitted

        # Step 1: determine the current mid-price and, if it jumped beyond the configured
        # tolerance, cancel all working entries so we can restage at the new context.
        mid = self._mid_cents()
        if mid is not None:
            self._last_summary["mid"] = mid
            if (
                self.last_mid_cents is not None
                and self.cfg.cancel_move_ticks > 0
                and abs(mid - self.last_mid_cents) >= self.cfg.cancel_move_ticks
            ):
                logger.info(
                    "[STRAT] mid shift %.2f -> %.2f (cancel threshold=%s)",
                    self.last_mid_cents,
                    mid,
                    self.cfg.cancel_move_ticks,
                )
                self._cancel_all_entries(reason="mid_move")
            self.last_mid_cents = mid

        # Step 2: run each strategy module in turn (respecting enable flags) and collect intents.
        strategies_summary: Dict[str, Any] = {}

        if self.cfg.touch_enabled:
            # Get desired orders from stateless TouchMaker
            desired_orders = self.touchmaker.run(
                self.orderbook,
                self.net_yes_position,
                self.recent_taker_yes,
                self.recent_taker_no,
                self.cfg.max_inventory_contracts
            )
            
            # Sync orders (cancel unwanted, place missing)
            touch_orders = self._sync_orders("touch", desired_orders)
            emitted.extend(touch_orders)
            
            touch_summary = {
                "status": "active",
                "desired_orders": len(desired_orders),
                "emitted_orders": len(touch_orders),
                "group_remaining": self.groups["touch"].remaining()
            }
        else:
            self._clear_strategy("touch", reason="disabled")
            touch_summary = {"status": "disabled", "group_remaining": self.groups["touch"].remaining()}
        strategies_summary["touch"] = touch_summary

        self._last_summary["strategies"] = strategies_summary

        # Log request frequency stats every 10 refreshes
        if not hasattr(self, '_stats_log_counter'):
            self._stats_log_counter = 0
        self._stats_log_counter += 1
        
        if self._stats_log_counter % 10 == 0:
            try:
                stats = self.order_executor.http_client.get_request_stats()
                logger.info(f"[STATS] Request frequency: {stats['total_requests']} total requests, "
                           f"{stats['time_since_last_request']:.1f}s since last, "
                           f"{stats['avg_requests_per_second']:.2f} req/s avg")
            except Exception as e:
                logger.debug(f"[STATS] Could not get request stats: {e}")

        return emitted

    def last_decision_summary(self) -> Dict[str, Any]:
        return self._last_summary

    def cancel_all_orders(self) -> List[OrderIntent]:
        # Sweep all live orders and synthesize cancel intents for graceful shutdowns.
        logger.info(f"[CLEANUP] Found {len(self.live_orders)} orders to cancel: {list(self.live_orders.keys())}")
        cleanup_intents: List[OrderIntent] = []
        for client_id, order in list(self.live_orders.items()):
            logger.info("[CLEANUP] cancel %s %s @ %s¢ (%s)", 
                       order.side.upper(), order.count, order.price_cents, order.strategy)
            intent = OrderIntent(
                ticker=order.ticker,
                strategy=order.strategy,
                purpose="cancel",
                action="cancel",
                side=order.side,
                price_cents=order.price_cents,
                count=order.count,
                post_only=True,
                expiration_ts=None,
                order_group_id=None,  # Remove order groups to simplify
                client_order_id=client_id,
            )
            # Actually execute the cancel request (handle 404 gracefully - order may not exist)
            try:
                self._execute_request("POST", "/portfolio/cancel_order", 
                                    json_data={"ticker": self.cfg.ticker, "client_order_id": client_id})
            except StrategyExecutionError as e:
                if e.status_code == 404:
                    logger.info("[LIVE CANCEL] Order %s not found (404) - treating as success", client_id)
                else:
                    raise
            cleanup_intents.append(intent)
            # Remove from live orders
            self.live_orders.pop(client_id, None)

        return cleanup_intents

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------


    def _run_depth_ladder(self) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        emitted: List[OrderIntent] = []
        summary: Dict[str, Any] = {
            "group_remaining": self.groups["depth"].remaining(),
            "targets": [],
            "skipped": [],
        }
        assert self.orderbook
        targets: Dict[str, Tuple[int, int]] = {}

        # Step 1: ensure both sides of the ladder have quotes; otherwise bail and clear state.
        yes_bid = self.orderbook.best_bid("yes").price
        no_bid = self.orderbook.best_bid("no").price
        yes_ask = self.orderbook.best_ask("yes").price
        no_ask = self.orderbook.best_ask("no").price
        if not all([yes_bid, yes_ask, no_bid, no_ask]):
            self._clear_strategy("depth")
            return emitted, summary

        # Step 2: for each configured depth level, walk bids down the ladder while checking spread guardrails.
        for level in range(1, self.cfg.depth_levels + 1):
            price_yes = clamp(yes_bid - level * self.cfg.depth_step_ticks, 1, 99)
            price_no = clamp(no_bid - level * self.cfg.depth_step_ticks, 1, 99)
            if yes_ask - price_yes >= self.cfg.min_spread_cents:
                targets[f"yes:depth:{level}"] = (price_yes, self.cfg.bid_size_contracts)
                summary["targets"].append({"leg": "yes", "level": level, "price": price_yes})
            else:
                summary["skipped"].append({
                    "leg": "yes",
                    "level": level,
                    "reason": f"spread {yes_ask - price_yes} < min {self.cfg.min_spread_cents}",
                })
            if no_ask - price_no >= self.cfg.min_spread_cents:
                targets[f"no:depth:{level}"] = (price_no, self.cfg.bid_size_contracts)
                summary["targets"].append({"leg": "no", "level": level, "price": price_no})
            else:
                summary["skipped"].append({
                    "leg": "no",
                    "level": level,
                    "reason": f"spread {no_ask - price_no} < min {self.cfg.min_spread_cents}",
                })

        # Step 3: hand off to reconciliation to place/cancel orders against the updated target book.
        entries, entry_stats = self._reconcile_entries("depth", targets)
        emitted.extend(entries)
        summary.update(entry_stats)
        self._last_summary.setdefault("strategies", {})["depth"] = summary
        return emitted, summary

    def _run_band_replenish(self) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        emitted: List[OrderIntent] = []
        summary: Dict[str, Any] = {
            "group_remaining": self.groups["band"].remaining(),
            "targets": [],
            "skipped": [],
        }
        assert self.orderbook
        targets: Dict[str, Tuple[int, int]] = {}

        # Step 1: compute an anchor mid-price; if unavailable, we cannot stage band orders.
        mid = self._mid_cents()
        if mid is None:
            self._clear_strategy("band")
            return emitted, summary

        # Step 2: pull best quotes (falling back to mid) to keep spread checks honest.
        yes_bid = self.orderbook.best_bid("yes").price or int(mid)
        no_bid = self.orderbook.best_bid("no").price or int(100 - mid)
        yes_ask = self.orderbook.best_ask("yes").price or int(mid)
        no_ask = self.orderbook.best_ask("no").price or int(100 - mid)

        # Step 3: propose symmetric price rungs around mid within the configured band width.
        for rung in range(1, self.cfg.band_rungs + 1):
            offset = rung * self.cfg.band_half_width_ticks
            price_yes = clamp(int(mid) - offset, 1, 99)
            price_no = clamp(int(100 - mid) - offset, 1, 99)
            if yes_ask - price_yes >= self.cfg.min_spread_cents:
                targets[f"yes:band:{rung}"] = (price_yes, self.cfg.bid_size_contracts)
                summary["targets"].append({"leg": "yes", "rung": rung, "price": price_yes})
            else:
                summary["skipped"].append({
                    "leg": "yes",
                    "rung": rung,
                    "reason": f"spread {yes_ask - price_yes} < min {self.cfg.min_spread_cents}",
                })
            if no_ask - price_no >= self.cfg.min_spread_cents:
                targets[f"no:band:{rung}"] = (price_no, self.cfg.bid_size_contracts)
                summary["targets"].append({"leg": "no", "rung": rung, "price": price_no})
            else:
                summary["skipped"].append({
                    "leg": "no",
                    "rung": rung,
                    "reason": f"spread {no_ask - price_no} < min {self.cfg.min_spread_cents}",
                })

        # Step 4: reconcile order state for the band strategy and emit any new intents.
        entries, entry_stats = self._reconcile_entries("band", targets)
        emitted.extend(entries)
        summary.update(entry_stats)
        self._last_summary.setdefault("strategies", {})["band"] = summary
        return emitted, summary


    # ------------------------------------------------------------------
    # Entry helpers
    # ------------------------------------------------------------------


    def _cancel_all_entries(self, reason: str) -> None:
        for strategy in ("touch", "depth", "band"):
            self._clear_strategy(strategy, reason=reason)

    def _clear_strategy(self, strategy: str, reason: Optional[str] = None) -> None:
        """Clear all orders for a strategy."""
        group = self.groups[strategy]
        reason_suffix = f" ({reason})" if reason else ""
        
        # Cancel all live orders for this strategy
        for client_id, order in list(self.live_orders.items()):
            if order.strategy == strategy:
                print(f"{_now_str()}  [CANCEL {strategy.upper()}] {order.side.upper()} {order.count} @ {order.price_cents}¢{reason_suffix}")
                # Handle 404 gracefully - order may not exist
                try:
                    self._execute_request(
                        "POST",
                        "/portfolio/cancel_order",
                        json_data={"ticker": self.cfg.ticker, "client_order_id": client_id},
                    )
                except StrategyExecutionError as e:
                    if e.status_code == 404:
                        logger.info("[LIVE CANCEL] Order %s not found (404) - treating as success", client_id)
                    else:
                        raise
                self.live_orders.pop(client_id, None)
                group.remove_intent(client_id)

    # ------------------------------------------------------------------
    # Exit helpers
    # ------------------------------------------------------------------





    # ------------------------------------------------------------------
    # Printing helpers
    # ------------------------------------------------------------------

    def _place_and_track_order(self, intent: OrderIntent, group: OrderGroupState) -> bool:
        """
        Place an order on the exchange and track it if successful.
        
        Returns:
            True if order was successfully placed and tracked, False otherwise
        """
        try:
            # Simple delay between order placements
            time.sleep(0.2)  # 200ms delay
            self._emit_order(intent, group)
            # Reset auth failure counter on successful order
            if hasattr(self, '_auth_failures'):
                self._auth_failures = 0
            return True
        except StrategyExecutionError as e:
            # Only handle specific errors gracefully (like 409 auth issues)
            if e.status_code == 409 and "try_logging_in" in e.body:
                logger.error(f"[LIVE ORDER] Auth error (409) for order {intent.client_order_id}: {e.body}")
                # 409 errors are handled by the HTTP client with automatic session reset and retry
                logger.warning(f"[LIVE ORDER] 409 authentication error - session will be reset and retried")
                
                return False
            else:
                # Re-raise other errors so tests and callers can handle them
                raise
        except Exception as e:
            logger.error(f"[LIVE ORDER] Unexpected error placing order {intent.client_order_id}: {e}")
            return False

    def _emit_order(self, intent: OrderIntent, group: OrderGroupState) -> None:
        action = "BUY" if intent.action == "buy" else "SELL"
        logger.info(
            f"{_now_str()}  [ORDER {intent.strategy.upper()}] {action} {intent.side.upper()} "
            f"{intent.count} @ {intent.price_cents}¢"
        )
        payload = intent.to_api_payload()
        
        # Place order on exchange (no order group needed)
        resp = self._execute_request("POST", "/portfolio/orders", json_data=payload)
        
        # Only register intent AFTER successful order placement
        group.register_intent(intent)


    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def inv_yes(self) -> int:
        # Net YES position cannot go below zero; negative inventory is handled by `inv_no`.
        return max(self.net_yes_position, 0)

    @property
    def inv_no(self) -> int:
        # Net NO inventory is represented as negative YES; clamp and flip the sign.
        return max(-self.net_yes_position, 0)

    def _mid_cents(self) -> Optional[float]:
        if not self.orderbook:
            return None
        # YES quotes trade between 0 and 100; mid is the midpoint between best bid/ask.
        bid = self.orderbook.best_bid("yes").price
        ask = self.orderbook.best_ask("yes").price
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _sync_orders(self, strategy: str, desired_orders: List[OrderIntent]) -> List[OrderIntent]:
        """
        Sync live orders with desired orders for a strategy.
        
        Args:
            strategy: Strategy name (e.g., "touch")
            desired_orders: List of orders that should exist
            
        Returns:
            List of OrderIntent objects to emit (cancels + new orders)
        """
        emitted = []
        group = self.groups[strategy]
        
        # Smart cancel-and-replace: only cancel orders that need to be changed

        def _fingerprint(order: OrderIntent) -> tuple[str, str, int, int]:
            return (order.side, order.action, order.price_cents, order.count)

        desired_fps = [_fingerprint(order) for order in desired_orders]
        matched_indices: set[int] = set()
        orders_to_cancel: list[OrderIntent] = []

        logger.debug(
            "[SYNC] %s comparing %d existing orders with %d desired orders",
            strategy,
            sum(1 for order in self.live_orders.values() if order.strategy == strategy),
            len(desired_orders),
        )

        for existing_client_id, existing_order in self.live_orders.items():
            if existing_order.strategy != strategy:
                continue

            existing_fp = _fingerprint(existing_order)
            match_idx: int | None = None
            for idx, fp in enumerate(desired_fps):
                if idx in matched_indices:
                    continue
                if fp == existing_fp:
                    match_idx = idx
                    break

            if match_idx is None:
                orders_to_cancel.append(existing_order)
                logger.info(
                    "[SYNC] %s canceling %s order (no matching desired order)",
                    strategy,
                    existing_order.side,
                )
            else:
                matched_indices.add(match_idx)
                logger.debug(
                    "[SYNC] %s keeping existing %s order (price=%s, count=%s)",
                    strategy,
                    existing_order.side,
                    existing_order.price_cents,
                    existing_order.count,
                )

        # Remove matched desired orders from further consideration
        unmatched_desired = [order for idx, order in enumerate(desired_orders) if idx not in matched_indices]

        # Step 2: Cancel orders that need to be changed
        for existing_order in orders_to_cancel:
            client_id = existing_order.client_order_id
            logger.info(f"[SYNC] {strategy} canceling order {client_id}")
            
            try:
                self._execute_request("POST", "/portfolio/cancel_order", 
                                    json_data={"ticker": self.cfg.ticker, "client_order_id": client_id})
                logger.info(f"[SYNC] {strategy} successfully canceled {client_id}")
            except StrategyExecutionError as e:
                if e.status_code == 404:
                    logger.info(f"[SYNC] {strategy} order {client_id} not found (404) - treating as success")
                else:
                    logger.error(f"[SYNC] {strategy} failed to cancel order {client_id}: {e}")
                    # Exit program if cancel fails (as requested)
                    raise SystemExit(f"Failed to cancel order {client_id}: {e}")
            
            # Remove from tracking
            self.live_orders.pop(client_id, None)
            group.remove_intent(client_id)
        
        # Step 3: Place new orders for unmatched desired intents
        for desired_order in unmatched_desired:
            client_id = desired_order.client_order_id
            logger.info(
                "[SYNC] %s placing new order %s (content: %s %s¢ %s contracts)",
                strategy,
                client_id,
                desired_order.side,
                desired_order.price_cents,
                desired_order.count,
            )

            if self._place_and_track_order(desired_order, group):
                emitted.append(desired_order)
                self.live_orders[client_id] = desired_order
                group.add_intent(client_id, desired_order)
                logger.debug(
                    "[SYNC] %s added order to live_orders: %s (%s %s¢ %s)",
                    strategy,
                    client_id,
                    desired_order.side,
                    desired_order.price_cents,
                    desired_order.count,
                )
            else:
                logger.error(f"[SYNC] {strategy} failed to place order {client_id}")
                raise SystemExit(f"Failed to place order {client_id}")
        
        return emitted
