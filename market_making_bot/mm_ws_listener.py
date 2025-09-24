#!/usr/bin/env python3
"""
Market Making WebSocket Listener for Kalshi.

Usage:
    python mm_ws_listener.py --ticker KXEPSTEINLIST-26-HKIS
    python mm_ws_listener.py --ticker KXEPSTEINLIST-26-HKIS --with-private --side yes
"""

import asyncio
import argparse
import logging
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure the project root is on the Python path when running this file directly
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

# Configure logging
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)


def timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass
class StrategyConfig:
    ticker: str
    side: str = "yes"  # "yes" or "no"
    lot_contracts: int = 5
    min_spread_cents: int = 3
    improve_if_last: bool = True
    queue_big_thresh_contracts: int = 400
    cancel_move_ticks: int = 2
    max_inventory_contracts: int = 100
    quote_ttl_seconds: int = 6
    reduce_only_step_contracts: int = 10


@dataclass
class MockOrder:
    price_cents: int
    remaining_contracts: int
    side: str
    expires_at: datetime


class MockMarketMaker:
    """One-sided market maker that prints intended actions."""

    def __init__(self, config: StrategyConfig) -> None:
        self.cfg = config
        self.position_contracts = 0  # YES long positive, NO long negative
        self.last_mid_cents: Optional[float] = None
        self.active_order: Optional[MockOrder] = None
        self._cached_orderbook = OrderBookTracker()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def update_orderbook_cache(self, orderbook: OrderBookTracker) -> None:
        self._cached_orderbook = orderbook

    def _inventory_ok(self) -> bool:
        if self.cfg.side == "yes":
            return self.position_contracts < self.cfg.max_inventory_contracts
        return -self.position_contracts < self.cfg.max_inventory_contracts

    def _mid_price_cents(self, orderbook: OrderBookTracker) -> Optional[float]:
        bid = orderbook.best_bid(self.cfg.side).price
        ask = orderbook.best_ask(self.cfg.side).price
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _spread_cents(self, orderbook: OrderBookTracker) -> Optional[int]:
        return orderbook.spread(self.cfg.side)

    def _best_prices(self, orderbook: OrderBookTracker) -> tuple[Optional[int], Optional[int], int, int]:
        best_bid = orderbook.best_bid(self.cfg.side)
        best_ask = orderbook.best_ask(self.cfg.side)
        return best_bid.price, best_ask.price, best_bid.size, best_ask.size

    def _select_target_price(self, orderbook: OrderBookTracker) -> Optional[int]:
        bid_price_cents, ask_price_cents, bid_size_contracts, ask_size_contracts = self._best_prices(orderbook)
        spread_cents = self._spread_cents(orderbook)

        if bid_price_cents is None or ask_price_cents is None or spread_cents is None:
            return None
        if spread_cents < self.cfg.min_spread_cents:
            return None

        target_price_cents = bid_price_cents if self.cfg.side == "yes" else ask_price_cents

        if self.cfg.side == "yes":
            if (
                self.cfg.improve_if_last
                and bid_size_contracts < 50
                and spread_cents >= (self.cfg.min_spread_cents + 1)
            ):
                target_price_cents = min(100, target_price_cents + 1)
            if (
                bid_size_contracts >= self.cfg.queue_big_thresh_contracts
                and (ask_price_cents - max(0, target_price_cents - 1)) >= self.cfg.min_spread_cents
            ):
                target_price_cents = max(0, target_price_cents - 1)
        else:
            if (
                self.cfg.improve_if_last
                and ask_size_contracts < 50
                and spread_cents >= (self.cfg.min_spread_cents + 1)
            ):
                target_price_cents = max(0, target_price_cents - 1)
            if (
                ask_size_contracts >= self.cfg.queue_big_thresh_contracts
                and (min(100, target_price_cents + 1) - bid_price_cents) >= self.cfg.min_spread_cents
            ):
                target_price_cents = min(100, target_price_cents + 1)

        return target_price_cents

    # ------------------------------------------------------------------
    # Strategy entry points
    # ------------------------------------------------------------------

    def on_orderbook(self, orderbook: OrderBookTracker) -> None:
        self.update_orderbook_cache(orderbook)
        mid_cents = self._mid_price_cents(orderbook)
        spread_cents = self._spread_cents(orderbook)

        if mid_cents is not None:
            if (
                self.last_mid_cents is not None
                and abs(mid_cents - self.last_mid_cents) >= self.cfg.cancel_move_ticks
                and self.active_order
            ):
                print(
                    f"{timestamp()}  [MOVE] mid moved ≥ {self.cfg.cancel_move_ticks} ticks → cancel/reprice"
                )
                self.active_order = None
            self.last_mid_cents = mid_cents

        if not self._inventory_ok():
            if self.active_order:
                print(
                    f"{timestamp()}  [CANCEL] pos cap hit (pos={self.position_contracts}); "
                    f"pulling {self.cfg.side.upper()} {self.active_order.remaining_contracts} @ {self.active_order.price_cents}¢"
                )
                self.active_order = None
            print(
                f"{timestamp()}  [REDUCE-ONLY] would send IOC reduce-only for {self.cfg.side.upper()} "
                f"{min(self.cfg.reduce_only_step_contracts, abs(self.position_contracts))} contracts"
            )
            return

        target_price_cents = self._select_target_price(orderbook)
        if target_price_cents is None:
            if self.active_order:
                print(
                    f"{timestamp()}  [CANCEL] spread {spread_cents}¢ < min {self.cfg.min_spread_cents}¢; "
                    f"pulling {self.cfg.side.upper()} {self.active_order.remaining_contracts} @ {self.active_order.price_cents}¢"
                )
                self.active_order = None
            return

        now = datetime.utcnow()
        if not self.active_order:
            self.active_order = MockOrder(
                price_cents=target_price_cents,
                remaining_contracts=self.cfg.lot_contracts,
                side=self.cfg.side,
                expires_at=now + timedelta(seconds=self.cfg.quote_ttl_seconds),
            )
            print(
                f"{timestamp()}  [POST] {self.cfg.ticker} {self.cfg.side.upper()} "
                f"{self.cfg.lot_contracts} @ {target_price_cents}¢ (post_only, ttl≈{self.cfg.quote_ttl_seconds}s)"
            )
            return

        if now >= self.active_order.expires_at:
            print(f"{timestamp()}  [EXPIRE] local quote expired; refreshing")
            self.active_order = MockOrder(
                price_cents=target_price_cents,
                remaining_contracts=self.cfg.lot_contracts,
                side=self.cfg.side,
                expires_at=now + timedelta(seconds=self.cfg.quote_ttl_seconds),
            )
            print(
                f"{timestamp()}  [POST] {self.cfg.ticker} {self.cfg.side.upper()} "
                f"{self.cfg.lot_contracts} @ {target_price_cents}¢ (post_only)"
            )
            return

        if self.active_order.price_cents != target_price_cents:
            print(
                f"{timestamp()}  [REPRICE] quote {self.active_order.price_cents}¢ → {target_price_cents}¢"
            )
            self.active_order = MockOrder(
                price_cents=target_price_cents,
                remaining_contracts=self.cfg.lot_contracts,
                side=self.cfg.side,
                expires_at=now + timedelta(seconds=self.cfg.quote_ttl_seconds),
            )
            print(
                f"{timestamp()}  [POST] {self.cfg.ticker} {self.cfg.side.upper()} "
                f"{self.cfg.lot_contracts} @ {target_price_cents}¢ (post_only)"
            )

    def on_trade(self, trade_payload: Dict[str, Any]) -> None:
        market_ticker = trade_payload.get("market_ticker")
        if market_ticker != self.cfg.ticker:
            return

        yes_price_cents = trade_payload.get("yes_price")
        no_price_cents = trade_payload.get("no_price")
        contracts = int(trade_payload.get("count", 0))
        taker_side = trade_payload.get("taker_side")

        print(
            f"{timestamp()}  [TRADE] {market_ticker} taker={taker_side} "
            f"yes={yes_price_cents}¢ no={no_price_cents}¢ size={contracts}"
        )

        if not self.active_order or contracts <= 0:
            self.on_orderbook(self._cached_orderbook)
            return

        filled_contracts = 0
        if self.cfg.side == "yes" and yes_price_cents == self.active_order.price_cents:
            filled_contracts = min(contracts, self.active_order.remaining_contracts)
            self.position_contracts += filled_contracts
        elif self.cfg.side == "no" and no_price_cents == self.active_order.price_cents:
            filled_contracts = min(contracts, self.active_order.remaining_contracts)
            self.position_contracts -= filled_contracts

        if filled_contracts > 0:
            self.active_order.remaining_contracts -= filled_contracts
            print(
                f"{timestamp()}  [FILL] {self.cfg.side.upper()} {filled_contracts} @ "
                f"{self.active_order.price_cents}¢ -> pos={self.position_contracts}"
            )
            if self.active_order.remaining_contracts <= 0:
                self.active_order = None
            if not self._inventory_ok():
                print(
                    f"{timestamp()}  [CAP] inventory limit reached (pos={self.position_contracts}); pulling quotes"
                )
                self.active_order = None
                print(
                    f"{timestamp()}  [REDUCE-ONLY] would send IOC reduce-only for {self.cfg.side.upper()} "
                    f"{min(self.cfg.reduce_only_step_contracts, abs(self.position_contracts))} contracts"
                )

        self.on_orderbook(self._cached_orderbook)


class MarketMakingListener:
    """Market making bot that listens to WebSocket streams for a specific ticker."""

    def __init__(self, ticker: str, with_private: bool = False, strategy_cfg: Optional[StrategyConfig] = None):
        """Initialize the market making listener."""
        self.ticker = ticker
        self.with_private = with_private
        self.config = Config()
        self.ws_client = KalshiWebSocketClient(self.config)
        self.api_client = KalshiAPIClient(self.config)
        self.running = False

        # Market data storage
        self.orderbook_tracker = OrderBookTracker()
        self.current_orderbook: Dict[str, Dict[int, int]] = {}
        self.recent_trades: list[dict[str, Any]] = []
        self.current_positions: dict[str, dict[str, Any]] = {}
        self.recent_fills: list[dict[str, Any]] = []

        self.strategy = MockMarketMaker(strategy_cfg or StrategyConfig(ticker=ticker))

        logger.info(f"Initialized MarketMakingListener for ticker: {ticker}")
        logger.info(f"Private mode: {with_private}")

    async def start(self):
        """Start the market making listener."""
        try:
            self.running = True
            logger.info(f"Starting market making listener for {self.ticker}")

            # Connect to WebSocket
            await self.ws_client.connect()

            # Subscribe to public market data
            await self._subscribe_public_data()

            # Subscribe to private data if requested
            if self.with_private:
                await self._subscribe_private_data()

            # Start listening for messages
            await self._listen_for_messages()

        except Exception as e:
            logger.error(f"Error starting market making listener: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        """Stop the market making listener."""
        self.running = False
        await self.ws_client.disconnect()
        logger.info("Market making listener stopped")

    async def _subscribe_public_data(self):
        """Subscribe to public market data streams."""
        logger.info(f"Subscribing to public data for {self.ticker}")

        # Subscribe to orderbook updates
        self.ws_client.subscribe_orderbook_updates([self.ticker])

        # Subscribe to public trades
        self.ws_client.subscribe_public_trades([self.ticker])

        # Subscribe to ticker updates
        self.ws_client.subscribe_market_ticker([self.ticker])

        logger.info(f"Subscribed to public data streams for {self.ticker}")

    async def _subscribe_private_data(self):
        """Subscribe to private/authenticated data streams."""
        logger.info("Subscribing to private data streams")

        # Subscribe to fills (trade confirmations)
        self.ws_client.subscribe_fills(self._on_fill)

        # Subscribe to market positions
        self.ws_client.subscribe_market_positions(self._on_market_position)

        logger.info("Subscribed to private data streams")

    async def _listen_for_messages(self):
        """Listen for WebSocket messages and process them."""
        logger.info("Starting message listener loop")

        while self.running:
            try:
                # Get messages from the WebSocket client
                message = self.ws_client.get_messages(timeout=1.0)

                if message:
                    await self._process_message(message)

                # Small delay to prevent busy waiting
                await asyncio.sleep(0.01)

            except Exception as e:
                logger.error(f"Error in message listener: {e}")
                await asyncio.sleep(1)

    async def _process_message(self, message: Dict[str, Any]):
        """Process incoming WebSocket messages."""
        try:
            channel = message.get('channel')
            message_type = message.get('message_type')
            data = message.get('data', {})

            logger.debug(f"Processing {channel} message: {message_type}")

            # Route to appropriate handler based on channel
            if channel == 'orderbook_delta':
                await self._on_orderbook_update(data)
            elif channel == 'trade':
                await self._on_public_trade(data)
            elif channel == 'ticker':
                await self._on_ticker_update(data)
            elif channel == 'fill':
                await self._on_fill(data)
            elif channel == 'market_positions':
                await self._on_market_position(data)
            else:
                logger.debug(f"Unhandled message type: {channel}")

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    # Event Handlers

    async def _on_orderbook_update(self, data: Dict[str, Any]):
        """Handle orderbook update messages (snapshot and delta)."""
        try:
            try:
                message_type = data['type']
                msg_data = data['msg']
                market_ticker = msg_data['market_ticker']
            except KeyError as exc:
                logger.error(f"Malformed orderbook message encountered: missing {exc!s}")
                raise

            if market_ticker != self.ticker:
                return

            if message_type == 'orderbook_snapshot':
                try:
                    self.orderbook_tracker.apply_snapshot(msg_data)
                except OrderBookError as exc:
                    logger.error(str(exc))
                    raise

                self.current_orderbook = self.orderbook_tracker.as_dict()
                self.strategy.on_orderbook(self.orderbook_tracker)
                self._log_orderbook_state(market_ticker, context="SNAPSHOT")

            elif message_type == 'orderbook_delta':
                try:
                    self.orderbook_tracker.apply_delta(msg_data)
                except OrderBookError as exc:
                    logger.error(str(exc))
                    raise

                self.current_orderbook = self.orderbook_tracker.as_dict()
                self.strategy.on_orderbook(self.orderbook_tracker)
                self._log_orderbook_state(
                    market_ticker,
                    context=f"DELTA {msg_data['side'].upper()} {msg_data['price']}¢ {msg_data['delta']:+d}"
                )

        except Exception as e:
            logger.error(f"Error handling orderbook update: {e}")

    def _log_orderbook_state(self, market_ticker: str, context: str, levels: int = 5):
        """Log the current orderbook state including top levels for both sides."""
        try:
            logger.info(f"[ORDERBOOK {context}] {market_ticker}")
            self._log_top_levels('yes', levels)
            self._log_top_levels('no', levels)
        except Exception as e:
            logger.error(f"Error logging orderbook state: {e}")

    def _log_top_levels(self, side: str, max_levels: int):
        """Log the top price levels for a given side of the orderbook."""
        try:
            levels = self.orderbook_tracker.top_levels(side, max_levels)
        except ValueError as exc:
            logger.error(str(exc))
            return

        if not levels:
            logger.info(f"  {side.upper()} Orders: none")
            return

        logger.info(f"  {side.upper()} Orders: {self.orderbook_tracker.level_count(side)} active levels")
        for idx, (price_cents, size_contracts) in enumerate(levels, start=1):
            logger.info(f"    Level {idx}: {price_cents}¢ x {size_contracts}")

    async def _on_public_trade(self, data: Dict[str, Any]):
        """Handle public trade messages."""
        try:
            try:
                msg_data = data['msg']
                market_ticker = msg_data['market_ticker']
            except KeyError as exc:
                logger.error(f"Malformed trade message encountered: missing {exc!s}")
                raise

            if market_ticker != self.ticker:
                return

            yes_price_cents = msg_data.get('yes_price')
            no_price_cents = msg_data.get('no_price')
            count_contracts = msg_data.get('count', 0)
            taker_side = msg_data.get('taker_side', 'unknown')
            ts_ms = msg_data.get('ts')

            trade_info = {
                'timestamp': datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc),
                'market_ticker': market_ticker,
                'yes_price_cents': yes_price_cents,
                'no_price_cents': no_price_cents,
                'count_contracts': count_contracts,
                'taker_side': taker_side
            }

            self.recent_trades.append(trade_info)
            if len(self.recent_trades) > 100:
                self.recent_trades.pop(0)

            logger.info(
                f"[TRADE] {market_ticker} | {taker_side.upper()} | "
                f"YES: {self._format_price(yes_price_cents)} | "
                f"NO: {self._format_price(no_price_cents)} | "
                f"Size: {count_contracts}"
            )

            self.strategy.on_trade(msg_data)

        except Exception as e:
            logger.error(f"Error handling public trade: {e}")

    async def _on_ticker_update(self, data: Dict[str, Any]):
        """Handle ticker update messages."""
        try:
            try:
                msg_data = data['msg']
                market_ticker = msg_data['market_ticker']
            except KeyError as exc:
                logger.error(f"Malformed ticker message encountered: missing {exc!s}")
                raise

            if market_ticker != self.ticker:
                return

            bid_cents = msg_data.get('bid') or msg_data.get('yes_bid')
            ask_cents = msg_data.get('ask') or msg_data.get('yes_ask')
            last_price_cents = msg_data.get('price') or msg_data.get('last_price')
            volume_contracts = msg_data.get('volume', 0)

            logger.info(
                f"[TICKER] {market_ticker} | "
                f"Bid: {self._format_price(bid_cents)} | "
                f"Ask: {self._format_price(ask_cents)} | "
                f"Last: {self._format_price(last_price_cents)} | "
                f"Volume: {volume_contracts}"
            )

        except Exception as e:
            logger.error(f"Error handling ticker update: {e}")

    async def _on_fill(self, data: Dict[str, Any]):
        """Handle fill (trade confirmation) messages."""
        try:
            market_ticker = data.get('market_ticker', '')
            side = data.get('side', 'unknown')
            count_contracts = data.get('count', 0)

            price_dollars = None
            if 'price_dollars' in data:
                price_dollars = float(data['price_dollars'])
            elif 'price_cc' in data:
                price_dollars = float(data['price_cc']) / 10000.0
            elif 'price' in data:
                price_dollars = float(data['price']) / 100.0

            fee_dollars = data.get('fee_dollars', 0)
            rebate_dollars = data.get('rebate_dollars', 0)

            fill_info = {
                'timestamp': datetime.now(timezone.utcnow().tzinfo or timezone.utc),
                'market_ticker': market_ticker,
                'side': side,
                'count_contracts': count_contracts,
                'price_dollars': price_dollars,
                'fee_dollars': fee_dollars,
                'rebate_dollars': rebate_dollars
            }

            self.recent_fills.append(fill_info)
            if len(self.recent_fills) > 50:
                self.recent_fills.pop(0)

            if price_dollars is not None:
                logger.info(
                    f"[FILL] {market_ticker} | {side.upper()} {count_contracts} @ ${price_dollars:.4f} | "
                    f"Fee: ${fee_dollars:.4f} | Rebate: ${rebate_dollars:.4f}"
                )
            else:
                logger.info(
                    f"[FILL] {market_ticker} | {side.upper()} {count_contracts} | "
                    f"Fee: ${fee_dollars:.4f} | Rebate: ${rebate_dollars:.4f}"
                )

        except Exception as e:
            logger.error(f"Error handling fill: {e}")

    async def _on_market_position(self, data: Dict[str, Any]):
        """Handle market position messages."""
        try:
            market_ticker = data.get('market_ticker', '')
            position_contracts = data.get('position', 0)

            exposure_centi_cents = data.get('market_exposure_cc', 0)
            exposure_dollars = (
                exposure_centi_cents / 10000.0 if isinstance(exposure_centi_cents, (int, float)) else None
            )

            self.current_positions[market_ticker] = {
                'position_contracts': position_contracts,
                'exposure_dollars': exposure_dollars,
                'timestamp': datetime.now(timezone.utcnow().tzinfo or timezone.utc)
            }

            if exposure_dollars is not None:
                logger.info(
                    f"[POSITION] {market_ticker} | Position: {position_contracts} | Exposure: ${exposure_dollars:.4f}"
                )
            else:
                logger.info(f"[POSITION] {market_ticker} | Position: {position_contracts}")

            self.strategy.position_contracts = position_contracts
        except Exception as e:
            logger.error(f"Error handling market position: {e}")

    def _format_price(self, price_cents: Optional[int]) -> str:
        if price_cents is None:
            return "—"
        return f"{price_cents}¢"

    def get_market_summary(self) -> Dict[str, Any]:
        return {
            'ticker': self.ticker,
            'current_orderbook': self.current_orderbook,
            'recent_trades_count': len(self.recent_trades),
            'recent_fills_count': len(self.recent_fills),
            'current_positions': self.current_positions,
            'timestamp': datetime.now(timezone.utcnow().tzinfo or timezone.utc)
        }


async def run_market_making_listener(ticker: str, with_private: bool, strategy_cfg: StrategyConfig):
    listener = MarketMakingListener(ticker, with_private, strategy_cfg)

    try:
        await listener.start()
    except KeyboardInterrupt:  # type: ignore[name-defined]
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        await listener.stop()


def main():
    parser = argparse.ArgumentParser(description="Kalshi Market Making WebSocket Listener")
    parser.add_argument("--ticker", required=True, help="Market ticker to subscribe to (e.g., KXEPSTEINLIST-26-HKIS)")
    parser.add_argument("--with-private", action="store_true", help="Subscribe to private streams (requires auth)")
    parser.add_argument("--side", choices=["yes", "no"], default="yes", help="Which leg to quote")
    parser.add_argument("--lot", type=int, default=5, help="Contracts per quote")
    parser.add_argument("--min-spread", type=int, default=3, help="Minimum spread (cents) required to quote")
    parser.add_argument("--queue-big-thresh", type=int, default=400, help="Queue size (contracts) considered large")
    parser.add_argument("--cancel-move-ticks", type=int, default=2, help="Reprice when mid moves by this many ticks")
    parser.add_argument("--max-inventory", type=int, default=100, help="Inventory cap in contracts")
    parser.add_argument("--quote-ttl-sec", type=int, default=6, help="Local quote refresh interval (seconds)")
    parser.add_argument("--reduce-only-step", type=int, default=10, help="Reduce-only size when over inventory cap")
    parser.add_argument("--improve-if-last", action="store_true", default=True, help="Improve by 1 tick when tailing queue")
    parser.add_argument("--no-improve-if-last", dest="improve_if_last", action="store_false", help="Disable improvement")

    args = parser.parse_args()

    logger.info(f"Starting market making listener for ticker: {args.ticker}")
    logger.info(f"Private mode: {args.with_private}")

    if args.with_private:
        config = Config()
        if not config.KALSHI_API_KEY_ID or not config.KALSHI_PRIVATE_KEY_PATH:
            logger.error("Private mode requested but KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH not set")
            logger.error("Set these environment variables or remove --with-private flag")
            return 1

    strategy_cfg = StrategyConfig(
        ticker=args.ticker,
        side=args.side,
        lot_contracts=args.lot,
        min_spread_cents=args.min_spread,
        improve_if_last=args.improve_if_last,
        queue_big_thresh_contracts=args.queue_big_thresh,
        cancel_move_ticks=args.cancel_move_ticks,
        max_inventory_contracts=args.max_inventory,
        quote_ttl_seconds=args.quote_ttl_sec,
        reduce_only_step_contracts=args.reduce_only_step,
    )

    try:
        asyncio.run(run_market_making_listener(args.ticker, args.with_private, strategy_cfg))
    except Exception as e:
        logger.error(f"Failed to start market making listener: {e}")
        return 1

    return 0


if __name__ == "__main__":
    # noqa: PLR0912 (argparse configuration is intentionally verbose)
    sys.exit(main())
