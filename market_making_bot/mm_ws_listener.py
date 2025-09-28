#!/usr/bin/env python3
"""
Market Making WebSocket Listener for Kalshi.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import contextlib
import json

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

MODULE_PATH = Path(__file__).resolve().parent
if str(MODULE_PATH) not in sys.path:
    sys.path.insert(0, str(MODULE_PATH))

from config import Config, setup_logging
from kalshi.websocket import KalshiWebSocketClient
from kalshi.client import KalshiAPIClient
from orderbook_tracker import OrderBookTracker, OrderBookError
from shared_config import StrategyConfig
from strategy import StrategyEngine

setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class MarketMakingListener:
    """Listens to Kalshi websockets and drives a strategy engine (with auto-reconnect)."""

    def __init__(
        self,
        ticker: str,
        with_private: bool,
        strategy_cfg: StrategyConfig,
        client_cfg: Config,
        *,
        ws_client: Optional[KalshiWebSocketClient] = None,
        api_client: Optional[KalshiAPIClient] = None,
    ) -> None:
        # Basic instance attributes and service clients.
        self.ticker = ticker
        self.with_private = with_private
        self.config = client_cfg
        self.ws_client = ws_client or KalshiWebSocketClient(self.config)
        self.api_client = api_client or KalshiAPIClient(self.config)
        self.orderbook_tracker = OrderBookTracker()
        self.engine = StrategyEngine(strategy_cfg, order_executor=self.api_client)

        self.recent_trades: list[dict[str, Any]] = []
        self.recent_positions: dict[str, dict[str, Any]] = {}
        self.recent_fills: list[dict[str, Any]] = []

        # Register subscriptions so reconnect logic inside the client resends automatically.
        self.ws_client.subscribe_orderbook_updates([self.ticker])
        self.ws_client.subscribe_public_trades([self.ticker])
        self.ws_client.subscribe_market_ticker([self.ticker])
        if self.with_private:
            self.ws_client.subscribe_fills(self._on_fill)
            self.ws_client.subscribe_market_positions(self._on_market_position)

    async def run(self) -> None:
        """Run the WS client and continuously process messages with reconnect tolerance."""
        backoff_seconds = 2
        while True:
            client_task: Optional[asyncio.Task] = None
            try:
                logger.info("[WS] launching client task for %s", self.ticker)

                async def client_runner() -> None:
                    await self.ws_client.start()

                client_task = asyncio.create_task(client_runner())

                # Give the client a moment to fully establish the websocket connection.
                while not self.ws_client.running and not client_task.done():
                    await asyncio.sleep(0.1)

                if not self.ws_client.running:
                    # Surface startup exceptions so callers can handle the root cause.
                    if client_task.done() and client_task.exception():
                        raise client_task.exception()
                    # Otherwise, the client never reported as running; log and retry.
                    logger.warning("[WS] client failed to start; retrying in %ss", backoff_seconds)
                    await asyncio.sleep(backoff_seconds)
                    continue

                # At this point the client is healthy; start consuming events until it stops.
                logger.info("[WS] connection established (ticker=%s)", self.ticker)
                await self._event_loop(client_task)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[WS] run-loop error: %s", exc, exc_info=True)
                await asyncio.sleep(backoff_seconds)
            finally:
                self.ws_client.stop()
                if client_task:
                    with contextlib.suppress(Exception):
                        await client_task
                logger.info("[WS] client stopped for %s", self.ticker)

    async def _event_loop(self, client_task: asyncio.Task) -> None:
        # Main message pump: pull outbound messages while underlying client stays alive.
        logger.info("Starting message loop")
        while True:
            if client_task.done():
                if exc := client_task.exception():
                    logger.error("[WS] client task ended with error: %s", exc, exc_info=True)
                else:
                    logger.warning("[WS] client task ended gracefully; reconnecting")
                break

            message = self.ws_client.get_messages(timeout=1.0)
            if message:
                await self._dispatch(message)
            await asyncio.sleep(0.01)

    async def _dispatch(self, envelope: Dict[str, Any]) -> None:
        channel = envelope.get("channel")
        message_type = envelope.get("message_type")
        payload = envelope.get("data", {})

        if channel == "orderbook_delta":
            # Ladder updates (snapshots or deltas) drive pricing decisions.
            await self._on_orderbook(payload)
        elif channel == "trade":
            # Public trade prints update recent activity and inform signals.
            await self._on_public_trade(payload)
        elif channel == "ticker":
            # Lightweight ticker pings are reserved for future analytics.
            await self._on_ticker(payload)
        elif channel == "fill":
            # Private fills adjust our inventory view and strategy state.
            await self._on_fill(payload)
        elif channel == "market_positions":
            # Position snapshots keep local exposure tracking in sync with Kalshi.
            await self._on_market_position(payload)
        else:
            logger.debug("Unhandled channel %s (%s)", channel, message_type)

    async def _on_orderbook(self, payload: Dict[str, Any]) -> None:
        # Decode the incoming ladder payload and feed it into the orderbook tracker.
        # Step 1: unwrap the Kalshi envelope (`msg` vs top-level) and ensure we know which
        # market it belongs to. Some events omit `market_ticker` at the top, so we fall back
        # to the nested `orderbook` section if needed.
        message_type = payload.get("type")
        msg_body = payload.get("msg") or payload
        market_ticker = msg_body.get("market_ticker")
        if not market_ticker:
            market_ticker = (msg_body.get("orderbook") or {}).get("market_ticker")
        if market_ticker != self.ticker:
            return

        # Step 2: normalize ladder fields so downstream tracker accepts consistent shape.
        # Snapshots nest their ladder under `orderbook`, whereas deltas often flatten it.
        # We coerce everything into a plain dict with `market_ticker`, `yes`, and `no` keys
        # because the tracker expects those fields regardless of the upstream variant.
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
                # Step 3a: snapshots reset every level, so we rebuild the tracker state
                # wholesale using the provided ladder.
                self.orderbook_tracker.apply_snapshot(ladder_body)
                logger.info("[OB] snapshot applied: yes_levels=%d no_levels=%d", len(ladder_body["yes"]), len(ladder_body["no"]))
            elif message_type == "orderbook_delta":
                # Step 3b: deltas only contain changed levels; the tracker merges them
                # into its existing state while maintaining order integrity.
                self.orderbook_tracker.apply_delta(ladder_body)
            else:
                return
        except OrderBookError as exc:
            logger.error("Orderbook error: %s", exc)
            return

        # Step 4: once the tracker is current, push the data into the strategy engine.
        # `refresh()` re-evaluates quoting logic and emits any order intents we should send.
        self.engine.update_orderbook(self.orderbook_tracker)
        orders_emitted = self.engine.refresh()
        summary = self.engine.last_decision_summary()
        if not orders_emitted:
            logger.info("[OB] no quotes emitted; summary=%s", json.dumps(summary, default=str))
        else:
            logger.info("[OB] emitted %d intents", len(orders_emitted))

    async def _on_public_trade(self, payload: Dict[str, Any]) -> None:
        # Record public tape updates and notify the strategy for flow-aware adjustments.
        # Step 1: unwrap the payload to a consistent structure the engine understands.
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        if market_ticker != self.ticker:
            return

        # Step 2: capture the trade in a rolling buffer (for logs, debugging dashboards,
        # or health checks). We cap the list to avoid unbounded growth.
        self.recent_trades.append(
            {
                "timestamp": datetime.now(timezone.utc),
                "payload": body,
            }
        )
        if len(self.recent_trades) > 100:
            self.recent_trades.pop(0)

        # Step 3: inform the strategy so it can react to recent flow (e.g., momentum
        # signals, inventory throttling, price anchoring).
        self.engine.on_public_trade(body)

    async def _on_ticker(self, payload: Dict[str, Any]) -> None:
        # Placeholder for ticker stream—currently ignored but reserved for analytics hooks.
        # We still normalize the shape so future logic can plug in with minimal changes.
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        if market_ticker != self.ticker:
            return
        # Reserved for future analytics
        return

    async def _on_fill(self, payload: Dict[str, Any]) -> None:
        # Track private fills and push them to the strategy so inventory stays accurate.
        # Step 1: normalize the message because fills can arrive as bare payloads or nested.
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        if market_ticker != self.ticker:
            return

        # Step 2: maintain a short history of fills for observability/debugging. Like
        # trades, this is bounded to keep memory usage predictable.
        self.recent_fills.append(
            {
                "timestamp": datetime.now(timezone.utc),
                "payload": body,
            }
        )
        if len(self.recent_fills) > 50:
            self.recent_fills.pop(0)

        # Step 3: hand the fill to the strategy so it can reconcile position, PnL, and
        # cancel/replace logic for outstanding quotes.
        self.engine.on_private_fill(body)

    async def _on_market_position(self, payload: Dict[str, Any]) -> None:
        # Surface account-level exposure updates emitted by Kalshi.
        # Step 1: normalize and verify the snapshot references a market.
        body = payload.get("msg") or payload
        market_ticker = body.get("market_ticker")
        if not market_ticker:
            return

        # Step 2: convert exchange units (contracts and centi-cents) into friendlier
        # representations for logs and dashboards.
        position_contracts = int(body.get("position", 0) or 0)
        exposure_centi_cents = body.get("position_cost") or body.get("market_exposure_cc")
        exposure_dollars = (
            exposure_centi_cents / 10000.0 if isinstance(exposure_centi_cents, (int, float)) else None
        )

        self.recent_positions[market_ticker] = {
            "position_contracts": position_contracts,
            "exposure_dollars": exposure_dollars,
            "timestamp": datetime.now(timezone.utc),
        }

        if market_ticker == self.ticker:
            # Step 3: if this is the actively traded market, sync the engine's view so
            # it enforces inventory limits and possibly refreshes outstanding quotes.
            self.engine.on_position_update(position_contracts)
            self.engine.refresh()


async def run_listener(ticker: str, with_private: bool, strategy_cfg: StrategyConfig, client_cfg: Config) -> None:
    listener = MarketMakingListener(ticker, with_private, strategy_cfg, client_cfg)
    try:
        await listener.run()
    except KeyboardInterrupt:
        logger.info("Interrupted, gathering cleanup cancels")
        cancels = listener.engine.cancel_all_orders()
        for intent in cancels:
            logger.info("[CLEANUP] would POST /portfolio/cancel for %s", intent.client_order_id)
    except Exception as exc:
        logger.error("Listener error: %s", exc, exc_info=True)
        raise
    finally:
        await listener.ws_client.disconnect()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kalshi TouchMaker strategy")
    parser.add_argument("--ticker", required=True, help="Market ticker (e.g., KXGDP-25Q4)")
    parser.add_argument("--with-private", action="store_true", help="Subscribe to fills/positions (requires auth)")
    parser.add_argument("--min-spread", type=int, default=3, help="Minimum spread (cents) per leg")
    parser.add_argument("--bid-size", type=int, default=5, help="Contracts per entry bid (per leg)")
    parser.add_argument("--sum-cushion", type=int, default=3, help="Guard so bid_yes+bid_no ≤ 100 - cushion")
    parser.add_argument("--quote-ttl", type=int, default=6, help="Seconds before entry bids refresh")
    parser.add_argument("--max-inventory", type=int, default=100, help="Net YES inventory cap")
    parser.add_argument("--touch-cap", type=int, default=40, help="Contracts cap for TouchMaker strategy")
    parser.add_argument("--enable-touch", action="store_true", help="Enable TouchMaker strategy")
    parser.add_argument("--live-mode", action="store_true", help="Use live mode")
    parser.add_argument("--demo-mode", action="store_true", help="Use demo environment mode")
    return parser.parse_args(argv)


def build_strategy_config(args: argparse.Namespace) -> StrategyConfig:
    return StrategyConfig(
        ticker=args.ticker,
        min_spread_cents=args.min_spread,
        bid_size_contracts=args.bid_size,
        sum_cushion_ticks=args.sum_cushion,
        quote_ttl_seconds=args.quote_ttl,
        max_inventory_contracts=args.max_inventory,
        touch_enabled=args.enable_touch,
        touch_contract_limit=args.touch_cap,
        live_mode=args.live_mode,
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    logger.info("Starting strategy-grouped mock maker for ticker=%s", args.ticker)
    if args.with_private:
        cfg = Config()
        cfg.KALSHI_DEMO_MODE = args.demo_mode
        if not cfg.KALSHI_API_KEY_ID or not cfg.KALSHI_PRIVATE_KEY_PATH:
            logger.error("Private mode requested but Kalshi credentials are missing")
            return 1

    strategy_cfg = build_strategy_config(args)

    try:
        asyncio.run(run_listener(args.ticker, args.with_private, strategy_cfg, cfg))
    except Exception as exc:  # pragma: no cover
        logger.error("Fatal error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
