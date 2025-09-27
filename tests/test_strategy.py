import pytest

from market_making_bot.strategy import StrategyEngine, StrategyConfig
from market_making_bot.orderbook_tracker import OrderBookTracker


@pytest.fixture
def touch_config():
    return StrategyConfig(
        ticker="FAKE-1",
        min_spread_cents=3,
        bid_size_contracts=1,
        exit_size_contracts=1,
        sum_cushion_ticks=3,
        take_profit_ticks=2,
        quote_ttl_seconds=5,
        exit_ttl_seconds=10,
        max_inventory_contracts=5,
        reduce_only_step_contracts=1,
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
    )


def load_snapshot(engine: StrategyEngine, yes_levels, no_levels):
    tracker = OrderBookTracker()
    tracker.apply_snapshot(
        {
            "market_ticker": "FAKE-1",
            "yes": yes_levels,
            "no": no_levels,
        }
    )
    engine.update_orderbook(tracker)


def test_touchmaker_emits_when_spread_wide_enough(touch_config):
    """When the complementary YES/NO books imply a spread >= min, touchmaker should quote."""
    engine = StrategyEngine(touch_config)
    # YES best bid = 76, NO best bid = 21 -> implied YES ask = 79 (spread 3 >= min_spread_cents).
    load_snapshot(engine, yes_levels=[[76, 20], [75, 10]], no_levels=[[21, 20], [20, 10]])
    orders = engine.refresh()
    ids = {intent.client_order_id: intent for intent in orders}
    assert ids["touch-yes:touch"].price_cents == 76
    assert ids["touch-no:touch"].price_cents == 21


def test_touchmaker_skips_when_spread_too_tight(touch_config, caplog):
    """If the ladder collapses to a crossed/flat spread, strategy stands down and logs a skip."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[82, 20], [81, 10]], no_levels=[[18, 20], [17, 10]])
    caplog.clear()
    orders = engine.refresh()
    assert orders == []
    assert any("[TOUCH] skip YES" in record.message for record in caplog.records)


def test_inventory_cap_yields_exit_only(touch_config):
    """When inventory hits the cap, touchmaker suppresses entries and only exit orders remain."""
    engine = StrategyEngine(touch_config)
    engine.on_position_update(5)
    load_snapshot(engine, yes_levels=[[76, 20], [75, 10]], no_levels=[[21, 20], [20, 10]])
    orders = engine.refresh()
    assert orders
    assert all(intent.strategy == "exit" for intent in orders)


def test_cancel_all_orders_returns_intents(touch_config):
    """Cancellation path materialises OrderIntent stubs for every outstanding touch quote."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[76, 20], [75, 10]], no_levels=[[21, 20], [20, 10]])
    orders = engine.refresh()
    assert orders
    cancels = engine.cancel_all_orders()
    assert cancels
    assert not engine.current_entries["touch"]
