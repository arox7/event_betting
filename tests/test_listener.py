import asyncio
import contextlib
import logging

import pytest

from config import Config
from market_making_bot.mm_ws_listener import MarketMakingListener
from market_making_bot.strategy import StrategyConfig


class DummyKalshiClient:
    def __init__(self):
        self.running = True
        self.queue = asyncio.Queue()

    async def start(self):
        self.running = True
        while self.running:
            await asyncio.sleep(0.1)

    def stop(self):
        self.running = False

    def get_messages(self, timeout=0.1):
        try:
            return self.queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def disconnect(self):
        self.running = False

    def subscribe_orderbook_updates(self, *_):
        pass

    def subscribe_public_trades(self, *_):
        pass

    def subscribe_market_ticker(self, *_):
        pass

    def subscribe_fills(self, *_):
        pass

    def subscribe_market_positions(self, *_):
        pass


async def _run_listener_once(
    cfg: StrategyConfig,
    snapshot: dict,
    caplog,
    run_duration: float = 0.5,
    with_private: bool = False,
) -> list[logging.LogRecord]:
    client_cfg = Config()
    client_cfg.KALSHI_DEMO_MODE = True

    dummy_client = DummyKalshiClient()
    listener = MarketMakingListener(
        cfg.ticker,
        with_private,
        cfg,
        client_cfg,
        ws_client=dummy_client,
    )

    dummy_client.queue.put_nowait(snapshot)

    caplog.set_level(logging.INFO)
    task = asyncio.create_task(listener.run())
    await asyncio.sleep(run_duration)
    listener.ws_client.stop()
    await asyncio.sleep(0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    return list(caplog.records)


def _make_snapshot(yes_levels, no_levels, ticker="FAKE-DRY") -> dict:
    return {
        "channel": "orderbook_delta",
        "message_type": "orderbook_snapshot",
        "data": {
            "type": "orderbook_snapshot",
            "msg": {
                "market_ticker": ticker,
                "yes": yes_levels,
                "no": no_levels,
            },
        },
    }


@pytest.mark.asyncio
async def test_touch_strategy_emits_orders(caplog):
    cfg = StrategyConfig(
        ticker="FAKE-DRY",
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
    )

    yes_levels = [[70, 25], [72, 20], [74, 15], [76, 10]]
    no_levels = [[20, 15], [18, 10], [16, 5], [14, 3]]
    snapshot = _make_snapshot(yes_levels, no_levels)

    records = await _run_listener_once(cfg, snapshot, caplog)

    assert any("[ORDER TOUCH]" in record.message for record in records)


@pytest.mark.asyncio
async def test_depth_strategy_emits_orders(caplog):
    cfg = StrategyConfig(
        ticker="FAKE-DRY",
        touch_enabled=False,
        depth_enabled=True,
        band_enabled=False,
        depth_levels=2,
        depth_step_ticks=2,
    )

    yes_levels = [[74, 30], [72, 20], [70, 15], [68, 10]]
    no_levels = [[20, 30], [18, 20], [16, 15], [14, 10]]
    snapshot = _make_snapshot(yes_levels, no_levels)

    records = await _run_listener_once(cfg, snapshot, caplog)

    assert any("[ORDER DEPTH]" in record.message for record in records)


@pytest.mark.asyncio
async def test_band_strategy_emits_orders(caplog):
    cfg = StrategyConfig(
        ticker="FAKE-DRY",
        touch_enabled=False,
        depth_enabled=False,
        band_enabled=True,
        band_half_width_ticks=4,
        band_rungs=2,
    )

    yes_levels = [[74, 30], [72, 20], [70, 15], [68, 10]]
    no_levels = [[20, 30], [18, 20], [16, 15], [14, 10]]
    snapshot = _make_snapshot(yes_levels, no_levels)

    records = await _run_listener_once(cfg, snapshot, caplog)

    assert any("[ORDER BAND]" in record.message for record in records)
