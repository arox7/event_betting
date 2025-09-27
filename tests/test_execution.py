import logging
import os
import pytest

from market_making_bot.strategy import StrategyConfig, StrategyEngine
from market_making_bot.strategy import StrategyExecutionError, OrderIntent
from market_making_bot.orderbook_tracker import OrderBookTracker


class DummyResponse:
    def __init__(self, status_code=200, text="ok") -> None:
        self.status_code = status_code
        self.text = text


class RecordingHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def make_authenticated_request(self, method: str, path: str, json_data: dict):
        self.calls.append((method, path, json_data))
        return DummyResponse()


class RecordingExecutor:
    def __init__(self) -> None:
        self.http_client = RecordingHttpClient()


class ErroringHttpClient(RecordingHttpClient):
    def __init__(self, error_paths: set[str]) -> None:
        super().__init__()
        self.error_paths = error_paths

    def make_authenticated_request(self, method: str, path: str, json_data: dict):
        self.calls.append((method, path, json_data))
        if path in self.error_paths:
            return DummyResponse(status_code=500, text="error")
        return DummyResponse(status_code=200, text="ok")


class ErroringExecutor:
    def __init__(self, error_paths: set[str]) -> None:
        self.http_client = ErroringHttpClient(error_paths)


def load_snapshot(engine: StrategyEngine, yes_levels, no_levels) -> None:
    tracker = OrderBookTracker()
    tracker.apply_snapshot({"market_ticker": engine.cfg.ticker, "yes": yes_levels, "no": no_levels})
    engine.update_orderbook(tracker)


@pytest.fixture(autouse=True)
def force_demo_mode(monkeypatch):
    """Ensure demo mode is always on during execution tests to avoid real trading."""
    monkeypatch.setenv("KALSHI_DEMO_MODE", "true")


@pytest.fixture
def yes_no_book():
    # A stable, tradeable book: YES bid=70 (thin), NO bid=26 -> implied YES ask=74 (spread 4)
    return [[70, 10], [69, 80]], [[26, 40], [25, 30]]


def test_dry_run_logs_but_does_not_execute(yes_no_book, caplog: pytest.LogCaptureFixture):
    cfg = StrategyConfig(
        ticker="FAKE-EXEC",
        live_mode=False,  # DRY RUN
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
        bid_size_contracts=1,
        exit_size_contracts=1,
    )
    executor = RecordingExecutor()
    engine = StrategyEngine(cfg, order_executor=executor)
    load_snapshot(engine, *yes_no_book)

    caplog.set_level(logging.INFO)
    orders = engine.refresh()

    # Should have emitted entries, but no real HTTP calls recorded
    assert orders
    assert executor.http_client.calls == []
    # And the engine should log the intention instead of executing
    assert any("Would POST /portfolio/orders" in rec.message for rec in caplog.records)


def test_live_mode_executes_requests(yes_no_book):
    cfg = StrategyConfig(
        ticker="FAKE-EXEC",
        live_mode=True,  # LIVE
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
        bid_size_contracts=1,
        exit_size_contracts=1,
    )
    executor = RecordingExecutor()
    engine = StrategyEngine(cfg, order_executor=executor)
    load_snapshot(engine, *yes_no_book)

    orders = engine.refresh()
    # Expect at least one group create and order create
    paths = [p for (_m, p, _j) in executor.http_client.calls]
    assert "/portfolio/order_groups/create" in paths
    assert "/portfolio/orders" in paths

    # Now test cancel path
    cancels = engine.cancel_all_orders()
    assert cancels
    paths_after = [p for (_m, p, _j) in executor.http_client.calls]
    assert any(p == "/portfolio/cancel_order" for p in paths_after)


def test_live_mode_raises_on_error(monkeypatch, yes_no_book):
    cfg = StrategyConfig(
        ticker="FAKE-EXEC",
        live_mode=True,
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
        bid_size_contracts=1,
        exit_size_contracts=1,
    )
    executor = ErroringExecutor(error_paths={"/portfolio/orders"})
    engine = StrategyEngine(cfg, order_executor=executor)
    load_snapshot(engine, *yes_no_book)

    with pytest.raises(StrategyExecutionError):
        engine.refresh()


def test_reconcile_cancels_previous_entries_on_clear_live(yes_no_book):
    cfg = StrategyConfig(
        ticker="FAKE-CANCEL",
        live_mode=True,
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
        bid_size_contracts=1,
        exit_size_contracts=1,
    )
    executor = RecordingExecutor()
    engine = StrategyEngine(cfg, order_executor=executor)

    # Place initial touch entries on both legs
    load_snapshot(engine, *yes_no_book)
    engine.refresh()
    assert engine.current_entries["touch"]

    # Force a clear by providing an empty ladder (no quotes)
    load_snapshot(engine, yes_levels=[], no_levels=[])
    engine.refresh()

    cancel_calls = [j for (_m, p, j) in executor.http_client.calls if p == "/portfolio/cancel_order"]
    cancel_ids = {j.get("client_order_id") for j in cancel_calls}
    # Expect cancels for both original touch client ids
    assert {"touch-yes:touch", "touch-no:touch"}.issubset(cancel_ids)


def test_exit_cancels_when_inventory_goes_flat_live(yes_no_book):
    cfg = StrategyConfig(
        ticker="FAKE-EXIT-CANCEL",
        live_mode=True,
        touch_enabled=False,
        depth_enabled=False,
        band_enabled=False,
        bid_size_contracts=1,
        exit_size_contracts=5,
    )
    executor = RecordingExecutor()
    engine = StrategyEngine(cfg, order_executor=executor)

    # With positive YES inventory, exit maker should place an exit order
    engine.on_position_update(2)
    load_snapshot(engine, *yes_no_book)
    engine.refresh()

    # Now inventory goes flat -> expect exit cancels to be sent
    engine.on_position_update(0)
    engine.refresh()

    cancel_calls = [j for (_m, p, j) in executor.http_client.calls if p == "/portfolio/cancel_order"]
    cancel_ids = {j.get("client_order_id") for j in cancel_calls}
    assert "exit-yes" in cancel_ids

