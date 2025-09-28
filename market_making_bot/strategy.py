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
                group.register_fill(count)
                print(f"{_now_str()}  [GROUP] {group.name} fill +{count} (total {group.filled_contracts})")
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

        # TODO: Update depth and band strategies to use stateless approach
        if self.cfg.depth_enabled:
            depth_summary = {"status": "disabled", "reason": "not_implemented", "group_remaining": self.groups["depth"].remaining()}
        else:
            depth_summary = {"status": "disabled", "group_remaining": self.groups["depth"].remaining()}
        strategies_summary["depth"] = depth_summary

        if self.cfg.band_enabled:
            band_summary = {"status": "disabled", "reason": "not_implemented", "group_remaining": self.groups["band"].remaining()}
        else:
            band_summary = {"status": "disabled", "group_remaining": self.groups["band"].remaining()}
        strategies_summary["band"] = band_summary


        self._last_summary["strategies"] = strategies_summary

        return emitted

    def last_decision_summary(self) -> Dict[str, Any]:
        return self._last_summary

    def cancel_all_orders(self) -> List[OrderIntent]:
        # Sweep all live orders and synthesize cancel intents for graceful shutdowns.
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
                order_group_id=f"order-group-{order.strategy}",
                client_order_id=client_id,
            )
            # Actually execute the cancel request
            self._execute_request("POST", "/portfolio/cancel_order", 
                                json_data={"ticker": self.cfg.ticker, "client_order_id": client_id})
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
                self._execute_request(
                    "POST",
                    "/portfolio/cancel_order",
                    json_data={"ticker": self.cfg.ticker, "client_order_id": client_id},
                )
                self.live_orders.pop(client_id, None)
                group.remove_intent(client_id)

    # ------------------------------------------------------------------
    # Exit helpers
    # ------------------------------------------------------------------





    # ------------------------------------------------------------------
    # Printing helpers
    # ------------------------------------------------------------------

    def _emit_order(self, intent: OrderIntent, group: OrderGroupState) -> None:
        if not group.created:
            # First order for this group—we would normally create the order-group on Kalshi.
            payload = {"contracts_limit": group.contracts_limit}
            logger.info(f"{_now_str()}  [ORDER GROUP] create")
            resp = self._execute_request("POST", "/portfolio/order_groups/create", json_data=payload)
            # Parse order_group_id if provided (live API typically returns it). For test fakes
            # that return no JSON, skip assignment and proceed.
            if resp:
                try:
                    body = resp.json() if hasattr(resp, "json") else json.loads(getattr(resp, "text", "{}"))
                    group_id = (body or {}).get("order_group_id")
                    if isinstance(group_id, str):
                        group_id = group_id.strip()
                    if group_id and isinstance(group_id, str) and len(group_id) == UUID4_HEX_LEN and group_id.count("-") == 4:
                        group.group_id = group_id
                except Exception:
                    # Best-effort: absence or parse failure isn't fatal here because non-UUID
                    # IDs are acceptable in dry-run/tests; live failures already raised earlier.
                    pass
            group.created = True

        group.register_intent(intent)
        action = "BUY" if intent.action == "buy" else "SELL"
        logger.info(
            f"{_now_str()}  [ORDER {intent.strategy.upper()}] {action} {intent.side.upper()} "
            f"{intent.count} @ {intent.price_cents}¢ (group={group.name})"
        )
        payload = intent.to_api_payload()
        # Always override order_group_id with the live group id if present
        if group.group_id:
            payload["order_group_id"] = group.group_id
        resp = self._execute_request("POST", "/portfolio/orders", json_data=payload)

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
        
        # Get current live orders for this strategy
        current_orders = {client_id: order for client_id, order in self.live_orders.items() 
                        if order.strategy == strategy}
        
        # Cancel orders that are no longer desired
        for client_id, order in current_orders.items():
            if not any(d.client_order_id == client_id for d in desired_orders):
                # Cancel this order
                cancel_intent = OrderIntent(
                    ticker=order.ticker,
                    strategy=order.strategy,
                    purpose="cancel",
                    action="cancel",
                    side=order.side,
                    price_cents=order.price_cents,
                    count=order.count,
                    post_only=True,
                    client_order_id=client_id,
                    expiration_ts=None,
                    order_group_id=f"order-group-{order.strategy}"
                )
                # Actually execute the cancel
                self._execute_request("POST", "/portfolio/cancel_order", 
                                    json_data={"ticker": self.cfg.ticker, "client_order_id": client_id})
                emitted.append(cancel_intent)
                self.live_orders.pop(client_id, None)
                group.remove_intent(client_id)
        
        # Place missing orders or update existing orders
        for desired_order in desired_orders:
            client_id = desired_order.client_order_id
            if client_id not in self.live_orders:
                # Check if we have capacity
                if group.remaining() >= desired_order.count:
                    # Actually execute the order
                    self._emit_order(desired_order, group)
                    emitted.append(desired_order)
                    self.live_orders[client_id] = desired_order
                    group.add_intent(client_id, desired_order)
                else:
                    logger.info(f"[SYNC] {strategy} cap reached, skipping {client_id}")
            else:
                # Check if the order properties have changed
                existing_order = self.live_orders[client_id]
                if (existing_order.price_cents != desired_order.price_cents or 
                    existing_order.count != desired_order.count or
                    existing_order.side != desired_order.side):
                    # Cancel old order and place new one
                    cancel_intent = OrderIntent(
                        ticker=existing_order.ticker,
                        strategy=existing_order.strategy,
                        purpose="cancel",
                        action="cancel",
                        side=existing_order.side,
                        price_cents=existing_order.price_cents,
                        count=existing_order.count,
                        post_only=True,
                        client_order_id=client_id,
                        expiration_ts=None,
                        order_group_id=f"order-group-{existing_order.strategy}"
                    )
                    # Execute cancel
                    self._execute_request("POST", "/portfolio/cancel_order", 
                                        json_data={"ticker": self.cfg.ticker, "client_order_id": client_id})
                    emitted.append(cancel_intent)
                    self.live_orders.pop(client_id, None)
                    group.remove_intent(client_id)
                    
                    # Place new order
                    if group.remaining() >= desired_order.count:
                        # Actually execute the new order
                        self._emit_order(desired_order, group)
                        emitted.append(desired_order)
                        self.live_orders[client_id] = desired_order
                        group.add_intent(client_id, desired_order)
                    else:
                        logger.info(f"[SYNC] {strategy} cap reached, skipping {client_id}")
        
        return emitted
