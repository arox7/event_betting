"""Strategy engine building mock Kalshi orders for multiple maker profiles."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from orderbook_tracker import OrderBookTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Datamodels
# ---------------------------------------------------------------------------


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


@dataclass
class OrderIntent:
    """Representation of a Create Order request we *would* send."""

    ticker: str
    strategy: str
    purpose: str  # e.g. "entry"/"exit"
    action: str  # "buy"/"sell"
    side: str  # "yes"/"no"
    price_cents: int
    count: int
    post_only: bool
    expiration_ts: Optional[int]
    order_group_id: str
    client_order_id: str
    hedge_with: Optional[str] = None

    def to_api_payload(self) -> Dict[str, Any]:
        field = "yes_price" if self.side == "yes" else "no_price"
        payload: Dict[str, Any] = {
            "action": self.action,
            "side": self.side,
            "ticker": self.ticker,
            field: self.price_cents,
            "count": self.count,
            "post_only": self.post_only,
            "client_order_id": self.client_order_id,
            "order_group_id": self.order_group_id,
        }
        if self.expiration_ts is not None:
            payload["expiration_ts"] = self.expiration_ts
        if self.hedge_with:
            payload["hedge_with_client_order_id"] = self.hedge_with
        return payload


@dataclass
class OrderGroupState:
    """Tracks cap + pending orders for a strategy bucket."""

    name: str
    contracts_limit: int
    filled_contracts: int = 0
    created: bool = False
    pending: Dict[str, OrderIntent] = field(default_factory=dict)

    def remaining(self) -> int:
        return max(0, self.contracts_limit - self.filled_contracts)

    def register_intent(self, intent: OrderIntent) -> None:
        self.pending[intent.client_order_id] = intent

    def remove_intent(self, client_order_id: str) -> None:
        self.pending.pop(client_order_id, None)

    def register_fill(self, count: int) -> None:
        self.filled_contracts += count


@dataclass
class StrategyConfig:
    """High level knobs for all strategies."""

    ticker: str
    min_spread_cents: int = 3
    bid_size_contracts: int = 5
    exit_size_contracts: int = 5
    sum_cushion_ticks: int = 3
    take_profit_ticks: int = 2
    quote_ttl_seconds: int = 6
    exit_ttl_seconds: int = 20
    cancel_move_ticks: int = 2
    max_inventory_contracts: int = 100
    reduce_only_step_contracts: int = 10

    touch_enabled: bool = True
    touch_contract_limit: int = 40

    depth_enabled: bool = True
    depth_contract_limit: int = 120
    depth_levels: int = 3
    depth_step_ticks: int = 2

    band_enabled: bool = True
    band_contract_limit: int = 80
    band_half_width_ticks: int = 4
    band_rungs: int = 2

    queue_small_threshold: int = 50
    queue_big_threshold: int = 400
    exit_ladder_threshold: int = 30
    improve_if_last: bool = True


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

        # Group state keeps per-strategy inventory caps and outstanding intents.
        self.groups: Dict[str, OrderGroupState] = {
            "touch": OrderGroupState("touch", cfg.touch_contract_limit),
            "depth": OrderGroupState("depth", cfg.depth_contract_limit),
            "band": OrderGroupState("band", cfg.band_contract_limit),
            "exit": OrderGroupState("exit", cfg.max_inventory_contracts),
        }

        # Track currently posted entries keyed by strategy/leg so we can reconcile deltas.
        self.current_entries: Dict[str, Dict[str, Tuple[int, int]]] = {
            "touch": {},
            "depth": {},
            "band": {},
        }
        self.exit_orders: Dict[str, Dict[str, Tuple[int, int]]] = {"yes": {}, "no": {}}

        # State derived from fills/positions that drive quoting decisions.
        self.net_yes_position: int = 0
        self.last_mid_cents: Optional[float] = None
        self.recent_taker_yes: Optional[str] = None
        self.recent_taker_no: Optional[str] = None
        self._last_summary: Dict[str, Any] = {}

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
        for group in self.groups.values():
            if client_id in group.pending:
                group.remove_intent(client_id)
                group.register_fill(count)
                print(f"{_now_str()}  [GROUP] {group.name} fill +{count} (total {group.filled_contracts})")
                if group.remaining() <= 0:
                    print(f"{_now_str()}  [GROUP] {group.name} cap hit -> would cancel remaining orders")
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
            touch_orders, touch_summary = self._run_touchmaker()
            emitted.extend(touch_orders)
        else:
            self._clear_strategy("touch", reason="disabled")
            touch_summary = {"status": "disabled", "group_remaining": self.groups["touch"].remaining()}
        strategies_summary["touch"] = touch_summary

        if self.cfg.depth_enabled:
            depth_orders, depth_summary = self._run_depth_ladder()
            emitted.extend(depth_orders)
        else:
            self._clear_strategy("depth", reason="disabled")
            depth_summary = {"status": "disabled", "group_remaining": self.groups["depth"].remaining()}
        strategies_summary["depth"] = depth_summary

        if self.cfg.band_enabled:
            band_orders, band_summary = self._run_band_replenish()
            emitted.extend(band_orders)
        else:
            self._clear_strategy("band", reason="disabled")
            band_summary = {"status": "disabled", "group_remaining": self.groups["band"].remaining()}
        strategies_summary["band"] = band_summary

        exit_orders, exit_summary = self._run_exit_orders()
        emitted.extend(exit_orders)
        strategies_summary["exit"] = exit_summary

        self._last_summary["strategies"] = strategies_summary

        return emitted

    def last_decision_summary(self) -> Dict[str, Any]:
        return self._last_summary

    def cancel_all_orders(self) -> List[OrderIntent]:
        # Sweep all strategies and synthesize cancel intents for graceful shutdowns.
        cleanup_intents: List[OrderIntent] = []
        for strategy, records in self.current_entries.items():
            for key, (price, size) in list(records.items()):
                leg = key.split(":")[0]
                client_id = f"{strategy}-{key}"
                logger.info("[CLEANUP] cancel %s %s @ %s¢ (%s)", leg.upper(), size, price, strategy)
                intent = OrderIntent(
                    ticker=self.cfg.ticker,
                    strategy=strategy,
                    purpose="cancel",
                    action="cancel",
                    side=leg,
                    price_cents=price,
                    count=size,
                    post_only=True,
                    expiration_ts=None,
                    order_group_id=f"order-group-{strategy}",
                    client_order_id=client_id,
                )
                cleanup_intents.append(intent)
                if self.order_executor:
                    try:
                        resp = self.order_executor.http_client.make_authenticated_request(
                            "POST",
                            "/portfolio/cancel_order",
                            json_data={"ticker": intent.ticker, "client_order_id": intent.client_order_id},
                        )
                        logger.info("[LIVE CANCEL] %s status=%s", client_id, resp.status_code)
                    except Exception as exc:
                        logger.error("[LIVE CANCEL] %s failed: %s", client_id, exc, exc_info=True)
                self.groups[strategy].remove_intent(client_id)
            records.clear()

        for leg, exits in self.exit_orders.items():
            for client_id, (price, size) in list(exits.items()):
                logger.info("[CLEANUP] cancel EXIT %s %s @ %s¢", leg.upper(), size, price)
                intent = OrderIntent(
                    ticker=self.cfg.ticker,
                    strategy="exit",
                    purpose="cancel",
                    action="cancel",
                    side=leg,
                    price_cents=price,
                    count=size,
                    post_only=True,
                    expiration_ts=None,
                    order_group_id="order-group-exit",
                    client_order_id=client_id,
                )
                cleanup_intents.append(intent)
                if self.order_executor:
                    try:
                        resp = self.order_executor.http_client.make_authenticated_request(
                            "POST",
                            "/portfolio/cancel_order",
                            json_data={"ticker": intent.ticker, "client_order_id": intent.client_order_id},
                        )
                        logger.info("[LIVE CANCEL] %s status=%s", client_id, resp.status_code)
                    except Exception as exc:
                        logger.error("[LIVE CANCEL] %s failed: %s", client_id, exc, exc_info=True)
                self.groups["exit"].remove_intent(client_id)
            exits.clear()

        return cleanup_intents

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _run_touchmaker(self) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        emitted: List[OrderIntent] = []
        summary: Dict[str, Any] = {
            "group_remaining": self.groups["touch"].remaining(),
            "targets": [],
            "skipped": [],
        }
        assert self.orderbook
        targets: Dict[str, Tuple[int, int]] = {}

        # Step 1: inspect top-of-book depth for YES/NO using tracker helpers.
        best_yes_bid = self.orderbook.best_bid("yes")
        best_yes_ask = self.orderbook.best_ask("yes")
        best_no_bid = self.orderbook.best_bid("no")
        best_no_ask = self.orderbook.best_ask("no")

        if (
            best_yes_bid.price is None
            or best_yes_ask.price is None
            or best_no_bid.price is None
            or best_no_ask.price is None
        ):
            self._clear_strategy("touch")
            return emitted, summary

        yes_room, no_room = self._inventory_room()

        # Step 2: decide if spread + queue heuristics merit quoting on each leg.
        yes_spread = best_yes_ask.price - best_yes_bid.price
        if yes_spread >= self.cfg.min_spread_cents and yes_room:
            price = best_yes_bid.price
            if (
                self.cfg.improve_if_last
                and best_yes_bid.size < self.cfg.queue_small_threshold
                and best_yes_ask.price - (price + 1) >= self.cfg.min_spread_cents
            ):
                price = clamp(price + 1, 0, 99)
                logger.info("[TOUCH] improving YES by 1 tick (queue=%s)", best_yes_bid.size)
            targets["yes:touch"] = (price, self.cfg.bid_size_contracts)
            summary["targets"].append({"leg": "yes", "price": price, "size": self.cfg.bid_size_contracts})
        elif not yes_room:
            msg = "inventory room exhausted"
            logger.info("[TOUCH] skip YES: %s", msg)
            summary["skipped"].append({"leg": "yes", "reason": msg})
        else:
            msg = f"spread {yes_spread} < min {self.cfg.min_spread_cents}"
            logger.info("[TOUCH] skip YES: %s", msg)
            summary["skipped"].append({"leg": "yes", "reason": msg})

        no_spread = best_no_ask.price - best_no_bid.price
        if no_spread >= self.cfg.min_spread_cents and no_room:
            price = best_no_bid.price
            if (
                self.cfg.improve_if_last
                and best_no_bid.size < self.cfg.queue_small_threshold
                and best_no_ask.price - (price + 1) >= self.cfg.min_spread_cents
            ):
                price = clamp(price + 1, 0, 99)
                logger.info("[TOUCH] improving NO by 1 tick (queue=%s)", best_no_bid.size)
            targets["no:touch"] = (price, self.cfg.bid_size_contracts)
            summary["targets"].append({"leg": "no", "price": price, "size": self.cfg.bid_size_contracts})
        elif not no_room:
            msg = "inventory room exhausted"
            logger.info("[TOUCH] skip NO: %s", msg)
            summary["skipped"].append({"leg": "no", "reason": msg})
        else:
            msg = f"spread {no_spread} < min {self.cfg.min_spread_cents}"
            logger.info("[TOUCH] skip NO: %s", msg)
            summary["skipped"].append({"leg": "no", "reason": msg})

        # Step 3: reconcile live orders against target map, emitting required intents.
        entries, entry_stats = self._reconcile_entries("touch", targets)
        emitted.extend(entries)
        summary.update(entry_stats)
        self._last_summary.setdefault("strategies", {})["touch"] = summary
        return emitted, summary

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

    def _run_exit_orders(self) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        emitted: List[OrderIntent] = []
        summary: Dict[str, Any] = {"yes": {}, "no": {}}
        assert self.orderbook
        # Step 1: treat YES and NO legs independently using current inventory snapshots.
        yes_orders, yes_stats = self._maintain_exit_leg("yes", self.inv_yes, self.recent_taker_yes)
        emitted.extend(yes_orders)
        summary["yes"] = yes_stats
        no_orders, no_stats = self._maintain_exit_leg("no", self.inv_no, self.recent_taker_no)
        emitted.extend(no_orders)
        summary["no"] = no_stats
        self._last_summary.setdefault("strategies", {})["exit"] = summary
        return emitted, summary

    # ------------------------------------------------------------------
    # Entry helpers
    # ------------------------------------------------------------------

    def _reconcile_entries(self, strategy: str, targets: Dict[str, Tuple[int, int]]) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        emitted: List[OrderIntent] = []
        group = self.groups[strategy]
        previous = self.current_entries[strategy]
        stats: Dict[str, Any] = {
            "cancels": [],
            "posted": [],
            "skipped_caps": [],
            "existing": len(previous),
            "remaining": group.remaining(),
        }

        # Step 1: cancel any live intents that no longer appear in the desired target map.
        for key, (price, size) in list(previous.items()):
            if key not in targets:
                leg = key.split(":")[0]
                print(f"{_now_str()}  [CANCEL {strategy.upper()}] {leg.upper()} {size} @ {price}¢")
                previous.pop(key, None)
                group.remove_intent(f"{strategy}-{key}")
                stats["cancels"].append({"leg": leg, "price": price, "size": size})

        # Step 2: post or amend required intents, respecting per-group caps and TTLs.
        for key, (price, size) in targets.items():
            leg = key.split(":")[0]
            client_id = f"{strategy}-{key}"
            expires_at = int(time.time() + self.cfg.quote_ttl_seconds)

            if key in previous and previous[key] == (price, size):
                continue

            if group.remaining() < size:
                logger.info(
                    "[CAP] %s remaining %s < size %s -> skip %s",
                    strategy,
                    group.remaining(),
                    size,
                    key,
                )
                stats["skipped_caps"].append({"leg": leg, "size": size, "remaining": group.remaining()})
                continue

            intent = OrderIntent(
                ticker=self.cfg.ticker,
                strategy=strategy,
                purpose="entry",
                action="buy",
                side=leg,
                price_cents=price,
                count=size,
                post_only=True,
                expiration_ts=expires_at,
                order_group_id=f"order-group-{strategy}",
                client_order_id=client_id,
            )
            emitted.append(intent)
            self._emit_order(intent, group)
            previous[key] = (price, size)
            stats["posted"].append({"leg": leg, "price": price, "size": size})

        stats["remaining_after"] = group.remaining()
        return emitted, stats

    def _cancel_all_entries(self, reason: str) -> None:
        for strategy in ("touch", "depth", "band"):
            self._clear_strategy(strategy, reason=reason)

    def _clear_strategy(self, strategy: str, reason: Optional[str] = None) -> None:
        group = self.groups[strategy]
        previous = self.current_entries[strategy]
        if not previous:
            return
        reason_suffix = f" ({reason})" if reason else ""
        for key, (price, size) in previous.items():
            leg = key.split(":")[0]
            print(f"{_now_str()}  [CANCEL {strategy.upper()}] {leg.upper()} {size} @ {price}¢{reason_suffix}")
            group.remove_intent(f"{strategy}-{key}")
        previous.clear()

    # ------------------------------------------------------------------
    # Exit helpers
    # ------------------------------------------------------------------

    def _maintain_exit_leg(self, leg: str, inventory: int, recent_taker: Optional[str]) -> Tuple[List[OrderIntent], Dict[str, Any]]:
        emitted: List[OrderIntent] = []
        stats: Dict[str, Any] = {
            "inventory": inventory,
            "active": list(self.exit_orders[leg].keys()),
            "placed": [],
            "cancelled": [],
        }
        exits = self.exit_orders[leg]
        if inventory <= 0:
            # Step 1: if we have no exposure on this leg, cancel anything still working.
            for kind, (price, size) in list(exits.items()):
                print(f"{_now_str()}  [CANCEL EXIT] {leg.upper()} {size} @ {price}¢")
                self.groups["exit"].remove_intent(kind)
                exits.pop(kind, None)
                stats["cancelled"].append({"kind": kind, "price": price, "size": size})
            return emitted, stats

        ladder = []
        if inventory >= self.cfg.exit_ladder_threshold:
            # Step 2a: large inventory—build a ladder of staged exits.
            ladder = self._build_exit_ladder(leg, inventory)
        if not ladder:
            price = self._compute_exit_price(leg, recent_taker)
            if price is None:
                ladder = []
            else:
                # Step 2b: otherwise place a single tactical quote sized by exit cap.
                ladder = [(f"exit-{leg}", price, min(inventory, self.cfg.exit_size_contracts))]

        # Step 3: remove any obsolete exit orders.
        keep_keys = {kind for kind, _, _ in ladder}
        for kind in list(exits.keys()):
            if kind not in keep_keys:
                price, size = exits[kind]
                print(f"{_now_str()}  [CANCEL EXIT] {leg.upper()} {size} @ {price}¢")
                self.groups["exit"].remove_intent(kind)
                exits.pop(kind, None)
                stats["cancelled"].append({"kind": kind, "price": price, "size": size})

        # Step 4: install or refresh any ladder quotes that are new/changed.
        for kind, price, size in ladder:
            if price is None or size <= 0:
                continue
            current = exits.get(kind)
            if current == (price, size):
                continue
            expires_at = int(time.time() + self.cfg.exit_ttl_seconds)
            intent = OrderIntent(
                ticker=self.cfg.ticker,
                strategy="exit",
                purpose="exit",
                action="sell",
                side=leg,
                price_cents=price,
                count=size,
                post_only=True,
                expiration_ts=expires_at,
                order_group_id="order-group-exit",
                client_order_id=kind,
            )
            emitted.append(intent)
            self._emit_order(intent, self.groups["exit"])
            exits[kind] = (price, size)
            stats["placed"].append({"kind": kind, "price": price, "size": size})

        return emitted, stats

    def _compute_exit_price(self, leg: str, recent_taker: Optional[str]) -> Optional[int]:
        assert self.orderbook
        best_bid = self.orderbook.best_bid(leg)
        best_ask = self.orderbook.best_ask(leg)
        if best_bid.price is None or best_ask.price is None:
            return None

        # Step 1: baseline exit price is the higher of best ask or min-spread over best bid.
        min_spread = self.cfg.min_spread_cents
        price = max(best_ask.price, best_bid.price + min_spread)

        if best_ask.size is not None:
            # Step 2: adjust based on queue size—thin queues invite improvement, heavy queues allow widening.
            if best_ask.size < self.cfg.queue_small_threshold and best_ask.price - best_bid.price >= min_spread + 1:
                price = max(best_ask.price - 1, best_bid.price + min_spread)
            elif best_ask.size >= self.cfg.queue_big_threshold:
                price = min(100, best_ask.price + 1)

        inventory = self.inv_yes if leg == "yes" else self.inv_no
        if inventory >= max(self.cfg.exit_ladder_threshold, 50) and price - best_bid.price >= min_spread + 1:
            # Step 3: heavy inventory pushes us to tighten a tick to exit faster.
            price = max(best_bid.price + min_spread, price - 1)

        if recent_taker == "down" and price < 100:
            # Step 4: if recent flow sold into us, nudge price toward the top to increase fill odds.
            price = min(100, price + 1)

        price = max(price, best_bid.price + min_spread)
        price = min(price, 100)
        return price

    def _build_exit_ladder(self, leg: str, inventory: int) -> List[Tuple[str, int, int]]:
        assert self.orderbook
        best_bid = self.orderbook.best_bid(leg)
        best_ask = self.orderbook.best_ask(leg)
        if best_bid.price is None or best_ask.price is None:
            return []

        # Step 1: split inventory across a few rungs with diminishing sizes.
        min_spread = self.cfg.min_spread_cents
        inv = inventory
        rung_sizes = [int(max(1, inv * 0.3)), int(max(1, inv * 0.4)), max(0, inv - int(inv * 0.3) - int(inv * 0.4))]
        rung_sizes = [min(size, self.cfg.exit_size_contracts) for size in rung_sizes if size > 0]
        if not rung_sizes:
            return []

        # Step 2: precompute candidate prices around the offer; later rungs use wider quotes.
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

    # ------------------------------------------------------------------
    # Printing helpers
    # ------------------------------------------------------------------

    def _emit_order(self, intent: OrderIntent, group: OrderGroupState) -> None:
        if not group.created:
            # First order for this group—we would normally create the order-group on Kalshi.
            payload = {"contracts_limit": group.contracts_limit}
            logger.info(f"{_now_str()}  [ORDER GROUP] Would POST /portfolio/order_groups/create")
            logger.info("%s", json.dumps(payload, indent=2))
            group.created = True

        group.register_intent(intent)
        action = "BUY" if intent.action == "buy" else "SELL"
        logger.info(
            f"{_now_str()}  [ORDER {intent.strategy.upper()}] {action} {intent.side.upper()} "
            f"{intent.count} @ {intent.price_cents}¢ (group={group.name})"
        )
        payload = intent.to_api_payload()
        logger.info("--> Would POST /portfolio/orders with payload:\n%s", json.dumps(payload, indent=2))
        if self.order_executor and intent.action in {"buy", "sell"}:
            try:
                # When running live, fire the authenticated order request through the executor.
                response = self.order_executor.http_client.make_authenticated_request(
                    "POST", "/portfolio/orders", json_data=payload
                )
                logger.info("[LIVE ORDER] status=%s body=%s", response.status_code, response.text)
            except Exception as exc:
                logger.error("[LIVE ORDER] failed: %s", exc, exc_info=True)

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

    def _inventory_room(self) -> Tuple[bool, bool]:
        # Determine whether we still have capacity to add YES or NO inventory relative to caps.
        cap = self.cfg.max_inventory_contracts
        yes_ok = self.inv_yes < cap
        no_ok = self.inv_no < cap
        if abs(self.net_yes_position) >= cap:
            logger.info("[INV] cap reached (%s)", self.net_yes_position)
            return False, False
        if not yes_ok:
            logger.info("[INV] YES cap reached (%s)", self.inv_yes)
        if not no_ok:
            logger.info("[INV] NO cap reached (%s)", self.inv_no)
        return yes_ok, no_ok
