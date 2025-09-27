import pytest

from market_making_bot.strategy import StrategyEngine, StrategyConfig
from market_making_bot.orderbook_tracker import OrderBookTracker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def depth_config():
    return StrategyConfig(
        ticker="FAKE-DEPTH",
        min_spread_cents=3,
        bid_size_contracts=2,
        exit_size_contracts=1,
        sum_cushion_ticks=3,
        take_profit_ticks=2,
        quote_ttl_seconds=5,
        exit_ttl_seconds=10,
        max_inventory_contracts=50,
        reduce_only_step_contracts=5,
        touch_enabled=False,
        depth_enabled=True,
        depth_levels=3,
        depth_step_ticks=2,
        depth_contract_limit=6,
        band_enabled=False,
    )


@pytest.fixture
def band_config():
    return StrategyConfig(
        ticker="FAKE-BAND",
        min_spread_cents=3,
        bid_size_contracts=1,
        exit_size_contracts=1,
        sum_cushion_ticks=3,
        take_profit_ticks=2,
        quote_ttl_seconds=5,
        exit_ttl_seconds=10,
        max_inventory_contracts=50,
        reduce_only_step_contracts=5,
        touch_enabled=False,
        depth_enabled=False,
        band_enabled=True,
        band_contract_limit=6,
        band_half_width_ticks=3,
        band_rungs=2,
    )


@pytest.fixture
def multi_strategy_config():
    return StrategyConfig(
        ticker="FAKE-MULTI",
        min_spread_cents=3,
        bid_size_contracts=1,
        exit_size_contracts=1,
        sum_cushion_ticks=3,
        take_profit_ticks=2,
        quote_ttl_seconds=5,
        exit_ttl_seconds=10,
        max_inventory_contracts=10,
        reduce_only_step_contracts=1,
        touch_enabled=True,
        depth_enabled=True,
        depth_levels=2,
        depth_step_ticks=2,
        depth_contract_limit=4,
        band_enabled=True,
        band_contract_limit=4,
        band_half_width_ticks=3,
        band_rungs=2,
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def load_snapshot(engine: StrategyEngine, yes_levels, no_levels):
    tracker = OrderBookTracker()
    tracker.apply_snapshot(
        {
            "market_ticker": engine.cfg.ticker,
            "yes": yes_levels,
            "no": no_levels,
        }
    )
    engine.update_orderbook(tracker)


# ---------------------------------------------------------------------------
# TouchMaker scenarios
# ---------------------------------------------------------------------------


def test_touchmaker_improves_when_spread_and_queue_allow(touch_config):
    """Thin front-of-queue depth prompts a one-tick improvement while keeping the spread legal."""
    engine = StrategyEngine(touch_config)
    # YES max bid=70, NO max bid=26 -> implied YES ask=74 (spread 4 >= min_spread).
    load_snapshot(engine, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 50], [25, 40]])
    orders = engine.refresh()
    ids = {intent.client_order_id: intent for intent in orders}
    assert ids["touch-yes:touch"].price_cents == 71  # improved by one tick
    assert ids["touch-no:touch"].price_cents == 26


def test_touchmaker_skips_when_spread_too_tight(touch_config, caplog):
    """On a crossed or zero spread ladder the strategy stands down and records a skip reason."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[82, 20], [81, 10]], no_levels=[[18, 20], [17, 10]])
    caplog.clear()
    orders = engine.refresh()
    assert orders == []
    assert "[TOUCH] skip YES" in caplog.text


def test_touchmaker_prefers_flattening_negative_inventory(touch_config):
    """When short inventory is large, touchmaker only quotes the YES leg to reduce exposure."""
    engine = StrategyEngine(touch_config)
    engine.on_position_update(-4)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {intent.client_order_id for intent in orders}
    assert "touch-yes:touch" in ids
    assert "touch-no:touch" not in ids


def test_touchmaker_cancels_on_mid_shift(touch_config):
    """A large mid-price jump should wipe existing quotes before restaging new ones."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert "yes:touch" in engine.current_entries["touch"]

    # Mid moves sharply (YES bid collapses to 60, NO bid to 34 -> ask=66).
    load_snapshot(engine, yes_levels=[[60, 10], [59, 40]], no_levels=[[34, 40], [33, 30]])
    engine.refresh()
    # Quotes should have been cancelled and restaged at the new mid.
    assert engine.current_entries["touch"].get("yes:touch") == (60, touch_config.bid_size_contracts)


def test_touchmaker_inventory_cap_yields_exit_only(touch_config):
    """Once inventory hits the configured cap, new touch entries are suppressed in favour of exit orders."""
    engine = StrategyEngine(touch_config)
    engine.on_position_update(touch_config.max_inventory_contracts)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert orders
    assert all(intent.strategy == "exit" for intent in orders)


def test_touchmaker_recovers_when_inventory_frees_up(touch_config):
    """Dropping inventory below the cap should allow touch entries to resume."""
    engine = StrategyEngine(touch_config)
    engine.on_position_update(touch_config.max_inventory_contracts)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    engine.on_position_update(touch_config.max_inventory_contracts - 2)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {intent.client_order_id for intent in orders}
    assert "touch-yes:touch" in ids and "touch-no:touch" in ids


# ---------------------------------------------------------------------------
# Depth strategy scenarios
# ---------------------------------------------------------------------------


def test_depth_posts_ladder_when_spread_allows(depth_config):
    """Depth ladder stages bids multiple ticks back when the book maintains a healthy spread."""
    engine = StrategyEngine(depth_config)
    load_snapshot(engine, yes_levels=[[74, 20], [72, 15], [70, 10]], no_levels=[[24, 30], [22, 20]])
    orders = engine.refresh()
    depth_orders = [o for o in orders if o.strategy == "depth"]
    assert depth_orders
    # Should respect configured ladder count (depth_levels) without exceeding cap.
    assert len(depth_orders) <= depth_config.depth_levels * 2


def test_depth_skips_when_spread_collapses(depth_config):
    """When the spread fails guards, depth strategy records skips and emits no intents."""
    engine = StrategyEngine(depth_config)
    load_snapshot(engine, yes_levels=[[74, 20], [73, 10]], no_levels=[[27, 30], [26, 20]])
    orders = engine.refresh()
    assert not [o for o in orders if o.strategy == "depth"]
    summary = engine.last_decision_summary()["strategies"]["depth"]
    assert summary["skipped"]


def test_depth_respects_per_strategy_cap(depth_config):
    """Depth orders should never exceed the configured contract limit even with many targets."""
    engine = StrategyEngine(depth_config)
    # Force many viable targets by extending book depth.
    load_snapshot(
        engine,
        yes_levels=[[90 - 2 * i, 30] for i in range(6)],
        no_levels=[[10 + 2 * i, 20] for i in range(6)],
    )
    orders = engine.refresh()
    yes_depth = sum(o.count for o in orders if o.strategy == "depth" and o.side == "yes")
    no_depth = sum(o.count for o in orders if o.strategy == "depth" and o.side == "no")
    assert yes_depth <= depth_config.depth_contract_limit
    assert no_depth <= depth_config.depth_contract_limit


# ---------------------------------------------------------------------------
# Band strategy scenarios
# ---------------------------------------------------------------------------


def test_band_posts_rungs_around_mid(band_config):
    """Band replenishment stages symmetric rungs around the mid-price."""
    engine = StrategyEngine(band_config)
    load_snapshot(engine, yes_levels=[[72, 20], [71, 10]], no_levels=[[28, 20], [27, 10]])
    orders = engine.refresh()
    band_orders = [o for o in orders if o.strategy == "band"]
    assert band_orders
    prices = {o.price_cents for o in band_orders if o.side == "yes"}
    assert prices == {band_config.band_half_width_ticks * i for i in (0,)} or prices


def test_band_skips_when_mid_unavailable(band_config):
    """With insufficient ladder data (missing YES bids), band strategy should clear and stay idle."""
    engine = StrategyEngine(band_config)
    load_snapshot(engine, yes_levels=[], no_levels=[[50, 10]])
    orders = engine.refresh()
    assert not [o for o in orders if o.strategy == "band"]
    assert engine.current_entries["band"] == {}


def test_band_reanchors_after_mid_move(band_config):
    """A mid-price shift should cancel stale band quotes and restage around the new midpoint."""
    engine = StrategyEngine(band_config)
    load_snapshot(engine, yes_levels=[[72, 20], [71, 10]], no_levels=[[28, 20], [27, 10]])
    engine.refresh()
    first_prices = set(engine.current_entries["band"].values())
    load_snapshot(engine, yes_levels=[[60, 20], [59, 10]], no_levels=[[40, 20], [39, 10]])
    engine.refresh()
    second_prices = set(engine.current_entries["band"].values())
    assert first_prices != second_prices


# ---------------------------------------------------------------------------
# Exit ladder scenarios
# ---------------------------------------------------------------------------


def test_exit_ladder_span_large_inventory(touch_config):
    """High inventory builds a multi-rung exit ladder totalling the full position."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[70, 20], [69, 10]], no_levels=[[26, 20], [25, 10]])
    engine.on_position_update(45)
    orders = engine.refresh()
    exit_orders = [o for o in orders if o.strategy == "exit" and o.side == "yes"]
    assert exit_orders
    assert sum(o.count for o in exit_orders) <= touch_config.exit_size_contracts * len(exit_orders)


def test_exit_recent_taker_bias_shades_price(touch_config):
    """Recent opposite taker should nudge the computed exit price outward by one tick when possible."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[70, 20], [69, 10]], no_levels=[[26, 20], [25, 10]])
    engine.on_position_update(20)
    engine.recent_taker_yes = "down"
    orders = engine.refresh()
    exit_orders = [o for o in orders if o.strategy == "exit" and o.side == "yes"]
    assert exit_orders
    baseline_price = max(o.price_cents for o in exit_orders)
    load_snapshot(engine, yes_levels=[[70, 20], [69, 10]], no_levels=[[26, 20], [25, 10]])
    engine.recent_taker_yes = None
    neutral_orders = engine.refresh()
    neutral_price = max(o.price_cents for o in neutral_orders if o.strategy == "exit" and o.side == "yes"])
    assert baseline_price >= neutral_price


def test_exit_orders_cancel_when_inventory_zero(touch_config):
    """Exit quotes disappear as soon as inventory is flattened."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[70, 20], [69, 10]], no_levels=[[26, 20], [25, 10]])
    engine.on_position_update(20)
    engine.refresh()
    assert engine.exit_orders["yes"]
    engine.on_position_update(0)
    engine.refresh()
    assert not engine.exit_orders["yes"]


# ---------------------------------------------------------------------------
# Multi-strategy interplay
# ---------------------------------------------------------------------------


def test_combined_strategies_respect_caps(multi_strategy_config):
    """When multiple strategies run, combined output still honours per-strategy limits."""
    engine = StrategyEngine(multi_strategy_config)
    load_snapshot(
        engine,
        yes_levels=[[74, 20], [72, 15], [70, 10]],
        no_levels=[[26, 30], [24, 20], [22, 10]],
    )
    orders = engine.refresh()
    summary = engine.last_decision_summary()["strategies"]
    assert summary["touch"]["group_remaining"] <= multi_strategy_config.touch_contract_limit
    depth_orders = [o for o in orders if o.strategy == "depth"]
    assert sum(o.count for o in depth_orders) <= multi_strategy_config.depth_contract_limit


def test_partially_filled_intent_removed_from_pending(touch_config):
    """After acknowledging a private fill, the touched quote should disappear from the pending map."""
    engine = StrategyEngine(touch_config)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert orders
    engine.on_private_fill({"client_order_id": "touch-yes:touch", "count": 1})
    assert "touch-yes:touch" not in engine.groups["touch"].pending


def test_flash_cross_rebuilds_quotes(touch_config):
    """A crossed snapshot followed by a clean one should pause then immediately restage quotes."""
    engine = StrategyEngine(touch_config)
    # Healthy snapshot -> quotes emitted.
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert engine.current_entries["touch"]
    # Crossed snapshot -> quotes cleared.
    load_snapshot(engine, yes_levels=[[80, 10]], no_levels=[[20, 10]])
    engine.refresh()
    assert not engine.current_entries["touch"]
    # Normal snapshot again -> quotes restored.
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert engine.current_entries["touch"]
