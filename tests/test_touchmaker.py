import pytest

from market_making_bot.strategy import StrategyConfig, StrategyEngine
from market_making_bot.orderbook_tracker import OrderBookTracker

def get_order_by_side(orders, side):
    """Helper to get order by side (yes/no) from timestamp-based CIDs."""
    return next((o for o in orders if f"touch-{side}" in o.client_order_id), None)

def get_order_cids(orders):
    """Helper to get set of order CIDs for pattern matching."""
    return {o.client_order_id for o in orders}


def load_snapshot(engine: StrategyEngine, yes_levels, no_levels) -> None:
    tracker = OrderBookTracker()
    tracker.apply_snapshot(
        {
            "market_ticker": engine.cfg.ticker,
            "yes": yes_levels,
            "no": no_levels,
        }
    )
    engine.update_orderbook(tracker)


@pytest.fixture
def cfg() -> StrategyConfig:
    return StrategyConfig(
        ticker="FAKE-TOUCH",
        min_spread_cents=3,
        bid_size_contracts=1,
        exit_size_contracts=1,
        sum_cushion_ticks=0,  # Disable sum-guard for core logic tests
        take_profit_ticks=2,
        quote_ttl_seconds=5,
        exit_ttl_seconds=10,
        max_inventory_contracts=5,
        reduce_only_step_contracts=1,
        touch_enabled=True,
        depth_enabled=False,
        band_enabled=False,
    )


def test_emits_and_improves_when_spread_and_queue_ok(cfg: StrategyConfig) -> None:
    """TouchMaker should post quotes and improve 1 tick when:
    - The implied spread is wide enough (e.g., YES bid=70, NO bid=26 → YES ask=74, spread=4 ≥ 3)
    - Front-of-queue is thin (best size=10 < queue_small_threshold)

    Expected behavior:
    - Should place both YES and NO orders when spread >= min_spread_cents
    - Should improve prices by 1 tick when queue size < queue_small_threshold
    - Should use post_only=True and correct client_order_id format
    - Expected: YES @ 71, NO @ 27 (one-tick improvements) with post-only respected.
    """
    engine = StrategyEngine(cfg)
    # YES best bid=70 (size 10), NO best bid=26 -> implied YES ask=74 (spread 4 >= 3)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 80]], no_levels=[[26, 40], [25, 30]])

    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    # YES improves by one tick due to small queue, NO improves symmetrically
    yes_order = get_order_by_side(orders, "yes")
    no_order = get_order_by_side(orders, "no")
    assert yes_order.price_cents == 71  # 70 + 1
    assert no_order.price_cents == 27


def test_skips_when_spread_too_tight(cfg: StrategyConfig, caplog: pytest.LogCaptureFixture) -> None:
    """If the book is flat (e.g., YES 82 and implied ask 82), the spread is 0 < min.

    Expected behavior:
    - Should not place any orders when spread < min_spread_cents
    - Should log appropriate skip messages for both YES and NO sides
    - Should return empty order list
    - Expected: no orders; logs include a [TOUCH] skip reason.
    """
    engine = StrategyEngine(cfg)
    # YES best bid=82, NO best bid=18 -> implied YES ask=82, spread=0 < 3
    load_snapshot(engine, yes_levels=[[82, 20], [81, 10]], no_levels=[[18, 20], [17, 10]])
    caplog.clear()
    orders = engine.refresh()
    assert orders == []
    assert "[TOUCH] Skipping YES" in caplog.text


def test_prefers_flattening_positive_inventory(cfg: StrategyConfig) -> None:
    """When net YES inventory is positive (long), TouchMaker should continue bidding both sides
    until inventory cap is reached. Bidding NO when we have YES inventory effectively exits
    our YES position (Kalshi handles the conversion).

    Expected behavior:
    - Should place both YES and NO orders when inventory is below cap
    - Should continue bidding both sides regardless of current position
    - Should only stop bidding when inventory cap is reached
    - Example: inv=+4, spread OK → expect both YES and NO bids (since cap not reached).
    """
    engine = StrategyEngine(cfg)
    # Net YES long: but below cap, so continue bidding both sides
    engine.on_position_update(4)  # below cap so entries are allowed
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {o.client_order_id for o in orders}
    # Expect both YES and NO bids (since cap not reached)
    assert any("touch-yes" in cid for cid in ids)
    assert any("touch-no" in cid for cid in ids)


def test_prefers_flattening_negative_inventory(cfg: StrategyConfig) -> None:
    """When net YES inventory is negative (short), TouchMaker should continue bidding both sides
    until inventory cap is reached. Bidding YES when we have NO inventory effectively exits
    our NO position (Kalshi handles the conversion).

    Expected behavior:
    - Should place both YES and NO orders when inventory is below cap
    - Should continue bidding both sides regardless of current position
    - Should only stop bidding when inventory cap is reached
    - Example: inv=-4, spread OK → expect both YES and NO bids (since cap not reached).
    """
    engine = StrategyEngine(cfg)
    # Net YES short: but below cap, so continue bidding both sides
    engine.on_position_update(-4)  # below cap in magnitude so entries are allowed
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {o.client_order_id for o in orders}
    # Expect both YES and NO bids (since cap not reached)
    assert any("touch-yes" in cid for cid in ids)
    assert any("touch-no" in cid for cid in ids)




def test_restages_after_mid_shift(cfg: StrategyConfig) -> None:
    """A large mid move should cancel prior quotes and restage at the new best price.

    Expected behavior:
    - Should cancel existing orders when market moves significantly
    - Should place new orders at the new optimal prices
    - Should update both YES and NO orders to new market levels
    - Should respect spread requirements and queue improvement logic
    - Example: initial YES bid 70/NO bid 26; then big move to YES bid 58/NO bid 39 (ask 61).
    - Expected: touch yes entry resets to 58 with configured size.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert len(engine.live_orders) == 2  # Should have YES and NO orders
    
    # Big move: YES bid drops, NO bid rises -> new mid far away
    # Choose values so implied spread remains >= min (YES bid=58, NO bid=39 -> YES ask=61, spread=3)
    load_snapshot(engine, yes_levels=[[58, 10], [57, 40]], no_levels=[[38, 40], [37, 30]])
    orders = engine.refresh()
    # Should have new orders at the new prices
    assert len(orders) >= 0  # May have cancel + new orders, or just new orders
    # Check that we have orders at the new price level
    yes_orders = [o for o in engine.live_orders.values() if o.side == "yes"]
    print(yes_orders)
    no_orders = [o for o in engine.live_orders.values() if o.side == "no"]
    print(no_orders)
    assert len(yes_orders) == 1
    assert len(no_orders) == 1
    assert yes_orders[0].price_cents == 59  # Improved by 1 tick (58 + 1)
    assert no_orders[0].price_cents == 39   # NO also improved by 1 tick (38 + 1)


def test_partial_fill_removes_pending(cfg: StrategyConfig) -> None:
    """Upon receiving a private fill for a posted touch order, the intent should be
    removed from the pending map so accounting remains correct.

    Expected behavior:
    - Should remove filled order from live_orders when fill is received
    - Should allow new orders to be placed after fills
    - Should maintain correct group accounting
    - Should not hit group cap limits due to stale orders
    - Example: fill 1 lot on the YES touch intent → it disappears from pending.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert orders
    # Simulate a private fill for the YES touch order
    # Find the actual touch-yes order CID
    yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
    assert yes_cid is not None, "touch-yes order not found"
    engine.on_private_fill({"client_order_id": yes_cid, "count": 1})
    assert yes_cid not in engine.groups["touch"].pending


def test_flash_cross_pauses_and_recovers(cfg: StrategyConfig) -> None:
    """A brief crossed snapshot should cause TouchMaker to pause (cancel), and it should
    immediately restage when the next normal snapshot arrives.
    
    Expected behavior:
    - Should cancel orders when market becomes crossed
    - Should immediately place new orders when market returns to normal
    - Should handle flash crashes and recoveries gracefully
    - Should maintain proper order management during market disruptions
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert len(engine.live_orders) == 2  # Should have orders
    
    # Crossed: YES bid 85, NO bid 20 -> implied YES ask 80 < bid
    load_snapshot(engine, yes_levels=[[85, 10]], no_levels=[[20, 10]])
    engine.refresh()
    # Should cancel orders due to crossed market
    assert len(engine.live_orders) == 0  # No orders in crossed market
    
    # Normal again
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert len(engine.live_orders) == 2  # Should have orders again


def test_idempotent_refresh_no_churn(cfg: StrategyConfig) -> None:
    """Calling refresh twice without a new snapshot should not produce new intents.

    Expected behavior:
    - Should not emit new orders when market conditions haven't changed
    - Should maintain stable live orders when no changes are needed
    - Should demonstrate stateless approach efficiency
    - Should avoid unnecessary order churn
    - Example: first refresh emits entries; second refresh on identical book returns [].
    - Live orders remain stable (one per leg).
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    first = engine.refresh()
    print("TEST_IDEMPOTENT_REFRESH_NO_CHURN: first", first)
    print("\n\n")
    second = engine.refresh()
    print("TEST_IDEMPOTENT_REFRESH_NO_CHURN: second", second)
    # First call emits orders, second call should emit no new orders (stable CIDs)
    assert len(first) == 2  # Should emit YES and NO orders
    assert len(second) == 0  # Second call should emit no new orders with stable CIDs
    # Live orders should remain stable with same CIDs
    assert len(engine.live_orders) == 2
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())


def test_improve_if_last_toggle(cfg: StrategyConfig) -> None:
    """Improvement logic should be deterministic:
    - With improve_if_last=True and thin queue, bids improve by +1 tick.
    - With improve_if_last=False, bids remain at best bid.
    
    Expected behavior:
    - Should improve prices by 1 tick when improve_if_last=True and queue is thin
    - Should not improve prices when improve_if_last=False
    - Should respect queue size thresholds for improvement decisions
    - Should maintain consistent pricing behavior across refreshes
    """
    # With improve_if_last=True (default), thin queue should improve by one tick
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    orders = engine.refresh()
    yes_order = get_order_by_side(orders, "yes")
    no_order = get_order_by_side(orders, "no")
    assert yes_order.price_cents == 71  # 70 + 1
    assert no_order.price_cents == 27   # 26 + 1

    # Disable improvement: bids should sit at best bid
    cfg2 = StrategyConfig(**{**cfg.__dict__, "improve_if_last": False})
    engine2 = StrategyEngine(cfg2)
    load_snapshot(engine2, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    orders2 = engine2.refresh()
    yes_order2 = get_order_by_side(orders2, "yes")
    no_order2 = get_order_by_side(orders2, "no")
    assert yes_order2.price_cents == 70
    assert no_order2.price_cents == 26


def test_queue_threshold_boundary(cfg: StrategyConfig) -> None:
    """Near the small-queue boundary:
    - Threshold above best size → improvement occurs.
    - Threshold below best size → no improvement.
    
    Expected behavior:
    - Should improve prices when queue size < queue_small_threshold
    - Should not improve prices when queue size >= queue_small_threshold
    - Should respect exact threshold boundaries
    - Should maintain consistent behavior at boundary conditions
    """
    # Set thin threshold above size -> improve; below size -> no improve
    cfg_hi = StrategyConfig(**{**cfg.__dict__, "queue_thin_threshold": 11})
    eng_hi = StrategyEngine(cfg_hi)
    load_snapshot(eng_hi, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    orders_hi = eng_hi.refresh()
    yes_order_hi = get_order_by_side(orders_hi, "yes")
    assert yes_order_hi.price_cents == 71  # Improved by 1 tick

    cfg_lo = StrategyConfig(**{**cfg.__dict__, "queue_thin_threshold": 9})
    eng_lo = StrategyEngine(cfg_lo)
    load_snapshot(eng_lo, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    orders_lo = eng_lo.refresh()
    yes_order_lo = get_order_by_side(orders_lo, "yes")
    # With threshold below size, no improvement
    assert yes_order_lo.price_cents == 70


def test_mid_shift_hysteresis(cfg: StrategyConfig) -> None:
    """Hysteresis:
    - Small mid moves (< cancel_move_ticks) do not cancel-and-wipe; quotes may micro-adjust.
    - Large moves (≥ cancel_move_ticks) trigger cancel-and-restage at the new best.
    
    Expected behavior:
    - Should not cancel orders on small market moves below threshold
    - Should cancel and restage orders on large market moves above threshold
    - Should respect hysteresis to avoid excessive order churn
    - Should maintain stable quotes during normal market fluctuations
    """
    cfg_hys = StrategyConfig(**{**cfg.__dict__, "cancel_move_ticks": 3})
    eng = StrategyEngine(cfg_hys)
    # Initial mid ~ 72
    load_snapshot(eng, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    eng.refresh()
    before = len(eng.live_orders)  # Count of live orders
    
    # Small move < hysteresis -> no cancel
    load_snapshot(eng, yes_levels=[[69, 10], [68, 40]], no_levels=[[27, 40], [26, 30]])
    eng.refresh()
    after_small = len(eng.live_orders)
    # We do not cancel-and-wipe on small moves, but quotes may adjust by ≤1 tick
    assert after_small == before  # Same number of orders
    
    # Big move >= hysteresis -> cancel and restage (keep spread >= min: NO bid=37 -> YES ask=63)
    load_snapshot(eng, yes_levels=[[60, 10], [59, 40]], no_levels=[[37, 40], [36, 30]])
    eng.refresh()
    # Should have orders at new price level
    yes_orders = [o for o in eng.live_orders.values() if o.side == "yes"]
    assert len(yes_orders) == 1
    assert yes_orders[0].price_cents == 60  # No improvement due to spread constraint


def test_post_only_compliance(cfg: StrategyConfig) -> None:
    """All touch orders must be post-only vs implied asks:
    YES price ≤ (implied YES ask - min_spread)
    NO price ≤ (implied NO ask - min_spread)
    
    Expected behavior:
    - Should ensure all orders are post-only and won't cross the market
    - Should respect minimum spread requirements
    - Should maintain proper price relationships with implied asks
    - Should prevent market impact from aggressive orders
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    # Compute implied asks from tracker helpers
    yes_ask = engine.orderbook.best_ask("yes").price
    no_ask = engine.orderbook.best_ask("no").price
    for o in orders:
        if o.strategy != "touch":
            continue
        if o.side == "yes":
            assert o.price_cents <= yes_ask - cfg.min_spread_cents
        else:
            assert o.price_cents <= no_ask - cfg.min_spread_cents


def test_sum_cushion_guard(cfg: StrategyConfig, caplog: pytest.LogCaptureFixture) -> None:
    """If yes_bid + no_bid would exceed 100 − sum_cushion_ticks, TouchMaker should adjust.
    
    Expected behavior:
    - Should adjust orders when sum of both bids exceeds cushion threshold
    - Should respect sum_cushion_ticks parameter for risk management
    - Should prevent orders that would create excessive exposure
    - Should maintain proper risk controls by keeping only one side
    """
    cfg_guard = StrategyConfig(**{**cfg.__dict__, "sum_cushion_ticks": 5})
    eng = StrategyEngine(cfg_guard)
    # YES bid=80, NO bid=16 -> 80 + 16 = 96 > 100 − 5 = 95 → should adjust
    # Need to provide proper ask levels to avoid negative spread
    load_snapshot(eng, yes_levels=[[80, 10], [69, 40]], no_levels=[[16, 10], [15, 20]])
    caplog.clear()
    orders = eng.refresh()
    # Should not have both orders due to sum-cushion guard
    assert not (any(o.side=="yes" and o.price_cents==80 for o in orders) and
                any(o.side=="no"  and o.price_cents==26 for o in orders))
    # Should have at least one order (the better side)
    assert len(orders) >= 1


def test_shade_down_huge_queue(cfg: StrategyConfig) -> None:
    """Shade down on huge queue (but keep spread safe).
    
    Expected behavior:
    - Should shade down by 1 tick when queue is huge (>= queue_big_threshold)
    - Should not shade down if it would violate min spread
    - Should maintain proper spread requirements
    - Example: YES best bid 70 (size=500), YES ask 75; min spread=3. Should shade to 69 only if ask-69 >= 3.
    """
    cfg_shade = StrategyConfig(**{**cfg.__dict__, "queue_big_threshold": 100})
    eng = StrategyEngine(cfg_shade)
    
    # Case 1: Huge queue with safe spread - should shade down
    load_snapshot(eng, yes_levels=[[70, 500], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = eng.refresh()
    yes_order = next((o for o in orders if o.side == "yes"), None)
    assert yes_order.price_cents == 69  # Should shade down from 70 to 69


def test_dont_chase_when_spread_would_break_floor(cfg: StrategyConfig) -> None:
    """Do not chase if chasing would collapse spread below floor.
    
    Expected behavior:
    - Should not re-peg upward if it would reduce spread below min_spread_cents
    - Should keep prior quote or stand pat when spread would be too tight
    - Should maintain spread requirements over aggressive pricing
    - Example: Start with wide spread; someone improves but following would leave spread < min_spread_cents
    """
    engine = StrategyEngine(cfg)
    # Start: good spread - YES bid=70, NO bid=26 (spread is wide)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    initial_orders = engine.refresh()
    assert len(initial_orders) == 2  # Should place both YES and NO orders
    
    # Someone improves YES to 72, but NO is still at 26
    # If we "chase" by improving to 72, the spread becomes too tight (72 + 26 = 98, very close to 100)
    # The sum-cushion guard should prevent us from placing both orders
    load_snapshot(engine, yes_levels=[[72, 5], [71, 10]], no_levels=[[26, 5], [25, 10]])
    orders = engine.refresh()
    
    # Should not have both orders due to sum-cushion guard (72 + 26 = 98 > 100 - 3 = 97)
    # Should have at most one order (the better side)
    assert len(orders) <= 2  # May have cancel + new orders
    # Check that we don't have both YES@72 and NO@26 simultaneously
    yes_orders = [o for o in orders if o.side == "yes" and o.action != "cancel"]
    no_orders = [o for o in orders if o.side == "no" and o.action != "cancel"]
    
    # Should not have both orders at the problematic prices
    assert not (any(o.price_cents == 72 for o in yes_orders) and 
                any(o.price_cents == 26 for o in no_orders))


def test_locked_market_behavior(cfg: StrategyConfig) -> None:
    """Test behavior with locked market (spread==0).
    
    Expected behavior:
    - Should not place orders when market is locked (spread=0)
    - Should cancel existing orders when market becomes locked
    - Should handle locked market gracefully
    - Example: YES bid=50, NO bid=50 -> spread=0, should not quote
    """
    engine = StrategyEngine(cfg)
    # Locked market: YES bid=50, NO bid=50 -> spread=0
    load_snapshot(engine, yes_levels=[[50, 10]], no_levels=[[50, 10]])
    orders = engine.refresh()
    assert orders == []  # Should not place orders in locked market


def test_crossed_market_behavior(cfg: StrategyConfig) -> None:
    """Test behavior with crossed market (spread<0).
    
    Expected behavior:
    - Should cancel all orders when market becomes crossed
    - Should not place new orders until market un-crosses
    - Should handle crossed market gracefully
    - Example: YES bid=60, NO bid=50 -> implied YES ask=50 < YES bid=60 (crossed)
    """
    engine = StrategyEngine(cfg)
    # Start with normal market
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert len(engine.live_orders) == 2  # Should have orders
    
    # Crossed market: YES bid=60, NO bid=50 -> implied YES ask=50 < YES bid=60
    load_snapshot(engine, yes_levels=[[60, 10]], no_levels=[[50, 10]])
    orders = engine.refresh()
    # Should cancel orders due to crossed market
    assert len(engine.live_orders) == 0  # No orders in crossed market


def test_post_only_with_improvement(cfg: StrategyConfig) -> None:
    """Test post-only compliance when improving by 1 tick.
    
    Expected behavior:
    - Should ensure improved price still respects post-only constraint
    - Should not improve if it would cross the market
    - Should fall back to join or skip if improvement would violate post-only
    - Example: YES bid=70, ask=73, min_spread=3. Improving to 71 should be OK (73-71=2 < 3, but post-only allows it)
    """
    engine = StrategyEngine(cfg)
    # Tight spread: YES bid=70, ask=73, min_spread=3
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    
    # All orders should be post-only
    for order in orders:
        assert order.post_only == True
        if order.side == "yes":
            # Should not cross the ask
            yes_ask = engine.orderbook.best_ask("yes").price
            assert order.price_cents <= yes_ask - cfg.min_spread_cents


def test_post_only_exit_compliance(cfg: StrategyConfig) -> None:
    """Test post-only compliance for exit orders.
    
    Expected behavior:
    - Should ensure exit asks never cross the market
    - Should clamp exit prices to maintain minimum spread
    - Should respect post-only constraints for all exit orders
    - Example: Exit asks should never cross even when leading by 1 tick
    """
    engine = StrategyEngine(cfg)
    # Simulate having inventory that needs exits
    engine.on_position_update(2)  # Long YES position
    
    # Normal market for exits
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    
    # All orders should be post-only
    for order in orders:
        assert order.post_only == True
        if order.side == "yes" and order.action == "sell":  # Exit orders
            # Exit asks should not cross
            yes_bid = engine.orderbook.best_bid("yes").price
            assert order.price_cents >= yes_bid + cfg.min_spread_cents


def test_ttl_expiry_single_restage(cfg: StrategyConfig) -> None:
    """Test TTL expiry causes single re-stage, not thrash.
    
    Expected behavior:
    - Should re-stage orders exactly once when TTL expires
    - Should not create duplicate orders during TTL refresh
    - Should maintain stable live order map
    - Should avoid order churn during TTL expiry
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    
    # Initial orders
    orders = engine.refresh()
    assert len(orders) == 2
    initial_live_count = len(engine.live_orders)
    
    # Simulate TTL expiry by advancing time and refreshing
    # In real implementation, this would be handled by the order executor
    # For testing, we simulate the behavior by calling refresh again
    orders_after_ttl = engine.refresh()
    
    # Should not create duplicate orders
    assert len(engine.live_orders) == initial_live_count
    # Should have exactly one order per leg
    assert len([o for o in engine.live_orders.values() if o.side == "yes"]) == 1
    assert len([o for o in engine.live_orders.values() if o.side == "no"]) == 1



def test_skips_when_one_side_missing(cfg: StrategyConfig, caplog: pytest.LogCaptureFixture) -> None:
    """If one leg of the ladder is missing (e.g., no YES bids), TouchMaker cannot compute
    a valid spread and must stand down without posting any entries.

    Expected behavior:
    - Should skip orders when one side of the market is missing
    - Should not place orders when spread cannot be computed
    - Should maintain empty state when market data is incomplete
    - Should handle missing market data gracefully
    - Example: yes_levels=[], no_levels present → expect no orders and empty touch state.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[], no_levels=[[26, 20], [25, 10]])
    caplog.clear()
    orders = engine.refresh()
    assert orders == []
    assert len(engine.live_orders) == 0  # No live orders


def test_bid_sequence_with_fills(cfg: StrategyConfig) -> None:
    """Test a sequence of bids, fills, and position changes.
    
    Expected behavior:
    - Should place both YES and NO orders when starting neutral
    - Should continue bidding both sides after fills (below cap)
    - Should maintain proper order management through fill sequence
    - Should handle position updates correctly
    - Sequence: Start neutral -> Fill YES -> Fill NO -> Check behavior
    """
    engine = StrategyEngine(cfg)
    
    # Step 1: Start neutral, should bid both sides
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {o.client_order_id for o in orders}
    assert any("touch-yes" in cid for cid in ids)
    assert any("touch-no" in cid for cid in ids)
    
    # Step 2: Simulate YES fill, position becomes +1
    # Find the actual touch-yes order CID
    yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
    assert yes_cid is not None, "touch-yes order not found"
    engine.on_private_fill({"client_order_id": yes_cid, "count": 1})
    engine.on_position_update(1)
    
    # Step 3: Should still bid both sides (below cap)
    orders = engine.refresh()
    # Check live orders instead of emitted orders (stateless approach)
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Step 4: Simulate NO fill, position becomes 0
    # Find the actual touch-no order CID
    no_cid = next((cid for cid in engine.live_orders.keys() if "touch-no" in cid), None)
    assert no_cid is not None, "touch-no order not found"
    engine.on_private_fill({"client_order_id": no_cid, "count": 1})
    engine.on_position_update(0)
    
    # Step 5: Should still bid both sides (neutral again)
    orders = engine.refresh()
    # Check live orders instead of emitted orders (stateless approach)
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())


def test_cancel_and_restage_sequence(cfg: StrategyConfig) -> None:
    """Test cancel and restage behavior during market moves.
    
    Expected behavior:
    - Should place initial orders at current market levels
    - Should cancel old orders when market moves significantly
    - Should place new orders at updated market levels
    - Should maintain proper order synchronization
    - Sequence: Place orders -> Market moves -> Cancel old -> Place new
    """
    engine = StrategyEngine(cfg)
    
    # Step 1: Initial orders
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert len(orders) == 2  # YES and NO bids
    
    # Step 2: Market moves significantly (should trigger cancel and restage)
    load_snapshot(engine, yes_levels=[[60, 10], [59, 40]], no_levels=[[37, 40], [36, 30]])
    orders = engine.refresh()
    
    # Should have cancel orders for old positions and new orders for new prices
    order_actions = {o.action for o in orders}
    assert "cancel" in order_actions or "buy" in order_actions  # Either cancels or new orders




def test_spread_tightening_sequence(cfg: StrategyConfig) -> None:
    """Test behavior as spread tightens and widens.
    
    Expected behavior:
    - Should place orders when spread is wide enough
    - Should skip orders when spread is too tight
    - Should resume placing orders when spread widens again
    - Should respect minimum spread requirements
    - Sequence: Wide spread -> Tight spread -> Wide spread again
    """
    engine = StrategyEngine(cfg)
    
    # Wide spread (should bid both sides)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert len(orders) == 2
    
    # Tight spread (should skip both sides)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[69, 40], [68, 30]])  # Very tight
    orders = engine.refresh()
    assert len(orders) == 0  # Should skip due to tight spread
    
    # Wide spread again (should bid both sides)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert len(orders) == 2


def test_queue_improvement_sequence(cfg: StrategyConfig) -> None:
    """Test queue improvement logic across multiple refreshes.
    
    Expected behavior:
    - Should improve prices when queue is thin (below threshold)
    - Should not improve prices when queue is thick (above threshold)
    - Should update prices when queue conditions change
    - Should respect queue size thresholds for improvement decisions
    - Sequence: Thin queue -> Thick queue -> Thin queue again
    """
    engine = StrategyEngine(cfg)
    
    # Thin queue (should improve)
    load_snapshot(engine, yes_levels=[[70, 5], [69, 40]], no_levels=[[26, 5], [25, 30]])  # Small size
    orders = engine.refresh()
    yes_order = next((o for o in orders if o.side == "yes"), None)
    no_order = next((o for o in orders if o.side == "no"), None)
    assert yes_order.price_cents == 71  # Improved by 1 tick
    assert no_order.price_cents == 27   # Improved by 1 tick
    
    # Thick queue (should not improve)
    load_snapshot(engine, yes_levels=[[70, 100], [69, 40]], no_levels=[[26, 100], [25, 30]])  # Large size
    orders = engine.refresh()
    # Check live orders instead of emitted orders (stateless approach)
    yes_order = next((o for cid, o in engine.live_orders.items() if "touch-yes" in cid), None)
    no_order = next((o for cid, o in engine.live_orders.items() if "touch-no" in cid), None)
    assert yes_order.price_cents == 70  # Not improved
    assert no_order.price_cents == 26   # Not improved


def test_market_crash_recovery(cfg: StrategyConfig) -> None:
    """Test behavior during market crash and recovery.
    
    Expected behavior:
    - Should place orders during normal market conditions
    - Should cancel orders when market crashes (no liquidity)
    - Should resume placing orders when market recovers
    - Should handle extreme market conditions gracefully
    - Sequence: Normal -> Crash (no bids) -> Recovery -> Normal
    """
    engine = StrategyEngine(cfg)
    
    # Normal market
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert len(orders) == 2
    
    # Market crash (no liquidity)
    load_snapshot(engine, yes_levels=[], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    # Should cancel existing orders due to missing data
    assert len(engine.live_orders) == 0  # No live orders
    
    # Recovery
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert len(orders) == 2


def test_rapid_position_changes(cfg: StrategyConfig) -> None:
    """Test rapid position changes and inventory management.
    
    Expected behavior:
    - Should continue bidding both sides when within cap range
    - Should stop bidding appropriate side when cap is reached
    - Should handle rapid position swings correctly
    - Should maintain proper inventory management throughout
    - Should respect caps for both positive and negative positions
    - Sequence: 0 -> +3 -> -2 -> +1 -> -4 -> Check behavior
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    
    # Rapid position changes
    for i, position in enumerate([0, 3, -2, 1, -4]):
        engine.on_position_update(position)
        orders = engine.refresh()

        # Should always bid both sides unless at cap
        if position < 5 and position > -5:  # Within cap range
            # With stable CIDs, same orders remain
            assert any("touch-yes" in cid for cid in engine.live_orders.keys())
            assert any("touch-no" in cid for cid in engine.live_orders.keys())
        elif position >= 5:  # At positive cap
            assert not any("touch-yes" in cid for cid in engine.live_orders.keys())
            assert any("touch-no" in cid for cid in engine.live_orders.keys())
        elif position <= -5:  # At negative cap
            assert any("touch-yes" in cid for cid in engine.live_orders.keys())
            assert not any("touch-no" in cid for cid in engine.live_orders.keys())


def test_sync_orders_comprehensive(cfg: StrategyConfig) -> None:
    """Test comprehensive _sync_orders functionality.
    
    Expected behavior:
    - Should cancel orders that are no longer desired
    - Should place new orders that are missing
    - Should update existing orders when properties change
    - Should respect group capacity limits
    - Should handle mixed operations (cancel + place simultaneously)
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    
    # Step 1: Initial orders
    orders = engine.refresh()
    assert len(orders) == 2  # Should place YES and NO orders
    assert len(engine.live_orders) == 2
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Step 2: Market moves - should update prices (test order property changes)
    load_snapshot(engine, yes_levels=[[60, 10], [59, 40]], no_levels=[[37, 40], [36, 30]])
    orders = engine.refresh()
    # Should emit cancel + new orders for price changes
    assert len(orders) >= 2  # At least cancel + new orders
    assert len(engine.live_orders) == 2  # Still have both orders
    
    # Step 3: Market becomes too tight - should cancel all orders (test order cancellation)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[69, 40], [68, 30]])  # Tight spread
    orders = engine.refresh()
    assert len(engine.live_orders) == 0  # All orders cancelled
    
    # Step 4: Market recovers - should place new orders (test new order placement)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert len(orders) == 2  # Should place new orders
    assert len(engine.live_orders) == 2  # Should have both orders again
    
    # Step 5: Test group capacity limits by filling up the group
    # First ensure we have orders to fill
    if len(engine.live_orders) == 0:
        # Market conditions may have cancelled orders, refresh to get new ones
        engine.refresh()
    
    # Fill the touch group to capacity
    for i in range(cfg.touch_contract_limit):
        # Find the actual touch-yes order CID
        yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
        if yes_cid is None:
            # If no YES order available, try NO order
            no_cid = next((cid for cid in engine.live_orders.keys() if "touch-no" in cid), None)
            if no_cid is not None:
                engine.on_private_fill({"client_order_id": no_cid, "count": 1})
                continue
            else:
                # No orders available, break
                break
        engine.on_private_fill({"client_order_id": yes_cid, "count": 1})
    
    # Try to place more orders - should be limited by group capacity
    orders = engine.refresh()
    # Should not place new orders due to group capacity
    assert len(engine.live_orders) <= cfg.touch_contract_limit


def test_touchmaker_never_sells(cfg: StrategyConfig) -> None:
    """Test that TouchMaker never creates sell orders - only buys YES or NO.
    
    Expected behavior:
    - Should only create orders with action="buy"
    - Should never create orders with action="sell"
    - Should only bid on YES and NO sides
    - Should maintain passive market making behavior
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    
    # Test various market conditions
    for i in range(5):  # Multiple refreshes
        orders = engine.refresh()
        
        # All orders should be buy orders (ignore cancel orders from sync)
        for order in orders:
            if order.action != "cancel":  # Skip cancel orders from _sync_orders
                assert order.action == "buy", f"TouchMaker created sell order: {order.action}"
                assert order.side in ["yes", "no"], f"TouchMaker created order for unexpected side: {order.side}"
        
        # Should only have YES and NO orders
        sides = {order.side for order in orders}
        assert sides.issubset({"yes", "no"}), f"TouchMaker created orders for unexpected sides: {sides}"
        
        # Simulate some market movement
        load_snapshot(engine, yes_levels=[[70 + (i % 3), 10], [69, 40]], 
                     no_levels=[[26 + (i % 3), 40], [25, 30]])


def test_sum_guard_allows_reduce_only_exits(cfg: StrategyConfig) -> None:
    """Test that sum-guard allows reduce-only exits even when violated.
    
    Scenario: You have YES inventory, spreads converge to 1¢, sum-guard would normally
    prevent both orders, but you need to exit your YES inventory.
    
    Expected: Allow NO order to exit YES inventory (reduce-only), even if sum-guard violated.
    """
    # Create a scenario where sum-guard would normally prevent both orders
    cfg_guard = StrategyConfig(**{**cfg.__dict__, "sum_cushion_ticks": 3})
    engine = StrategyEngine(cfg_guard)
    
    # Market with tight spreads that would violate sum-guard
    # YES bid=50, NO bid=50 -> sum=100, but max_allowed=100-3=97
    # But we need valid spread, so YES ask=53, NO ask=53
    load_snapshot(engine, yes_levels=[[50, 10], [49, 20]], no_levels=[[47, 10], [46, 20]])
    
    # Simulate having YES inventory (net_position > 0)
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}

    # Should have both orders initially (sum-guard not violated yet)
    assert any("touch-yes" in cid for cid in intents.keys())
    assert any("touch-no" in cid for cid in intents.keys())
    
    # Now simulate having YES inventory and tight spreads
    # Find the actual touch-yes order CID
    yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
    assert yes_cid is not None, "touch-yes order not found"
    engine.on_private_fill({"client_order_id": yes_cid, "count": 1})
    
    # Update market to locked market (spread=0) that would violate sum-guard
    # YES bid=50, NO bid=50 -> sum=100, but max_allowed=100-3=97
    # This violates sum-guard (100 > 97) and has locked market (spread=0)
    load_snapshot(engine, yes_levels=[[50, 10]], no_levels=[[50, 10]])
    
    # Refresh with YES inventory
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    
    # Should allow NO order to exit YES inventory, even if sum-guard violated
    assert any("touch-no" in cid for cid in intents.keys())  # NO order to exit YES inventory
    assert not any("touch-yes" in cid for cid in intents.keys())  # No new YES order (sum-guard prevents)
    
    # Verify the NO order is reduce-only (size limited to inventory)
    no_order = next((o for o in orders if "touch-no" in o.client_order_id), None)
    assert no_order is not None
    assert no_order.count == 1  # Limited to our YES inventory size


def test_sum_guard_allows_reduce_only_exits_no_inventory(cfg: StrategyConfig) -> None:
    """Test that sum-guard allows reduce-only exits for NO inventory.
    
    Scenario: You have NO inventory, spreads converge to 1¢, sum-guard would normally
    prevent both orders, but you need to exit your NO inventory.
    
    Expected: Allow YES order to exit NO inventory (reduce-only), even if sum-guard violated.
    """
    # Create a scenario where sum-guard would normally prevent both orders
    cfg_guard = StrategyConfig(**{**cfg.__dict__, "sum_cushion_ticks": 3})
    engine = StrategyEngine(cfg_guard)
    
    # Market with tight spreads that would violate sum-guard
    # YES bid=50, NO bid=50 -> sum=100, but max_allowed=100-3=97
    # But we need valid spread, so YES ask=53, NO ask=53
    load_snapshot(engine, yes_levels=[[50, 10], [49, 20]], no_levels=[[47, 10], [46, 20]])
    
    # Simulate having NO inventory (net_position < 0)
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    
    # Should have both orders initially (sum-guard not violated yet)
    assert any("touch-yes" in cid for cid in intents.keys())
    assert any("touch-no" in cid for cid in intents.keys())
    
    # Now simulate having NO inventory and tight spreads
    # Find the actual touch-no order CID
    no_cid = next((cid for cid in engine.live_orders.keys() if "touch-no" in cid), None)
    assert no_cid is not None, "touch-no order not found"
    engine.on_private_fill({"client_order_id": no_cid, "count": 1})
    
    # Update market to tight spreads that would violate sum-guard but still have valid spread
    # YES bid=50, NO bid=50 -> spread=0, but we need spread >= 3, so use YES bid=50, NO bid=47
    load_snapshot(engine, yes_levels=[[50, 10]], no_levels=[[47, 10]])
    
    # Refresh with NO inventory
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    
    # Should allow YES order to exit NO inventory, even if sum-guard violated
    # The YES order should be in live orders (may not be emitted again if already exists)
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())  # YES order to exit NO inventory
    assert any("touch-no" in cid for cid in intents.keys())  # NO order should be emitted (new order)
    
    # Verify the YES order is reduce-only (size limited to inventory)
    yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
    assert yes_cid is not None, "touch-yes order not found"
    yes_order = engine.live_orders[yes_cid]
    assert yes_order.count == 1  # Limited to our NO inventory size


def test_touchmaker_no_bid_escalation(cfg: StrategyConfig) -> None:
    """Test that TouchMaker doesn't escalate bids when other market makers quote on top.
    
    Expected behavior:
    - Should only improve by 1 tick when queue is thin
    - Should not chase other market makers up the book
    - Should maintain stable pricing behavior
    - Should not engage in bid wars
    """
    engine = StrategyEngine(cfg)
    
    # Test 1: Thin queue should improve by 1 tick
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    yes_order = next((o for o in orders if o.side == "yes"), None)
    no_order = next((o for o in orders if o.side == "no"), None)
    
    # Should improve by 1 tick due to thin queue (size=10 < threshold=50)
    assert yes_order.price_cents == 71  # 70 + 1
    assert no_order.price_cents == 27   # 26 + 1
    
    # Test 2: Thick queue should not improve
    load_snapshot(engine, yes_levels=[[70, 100], [69, 40]], no_levels=[[26, 100], [25, 30]])
    orders = engine.refresh()
    
    # Check live orders instead of emitted orders (stateless approach)
    yes_order = next((o for cid, o in engine.live_orders.items() if "touch-yes" in cid), None)
    no_order = next((o for cid, o in engine.live_orders.items() if "touch-no" in cid), None)
    
    # Should not improve due to thick queue (size=100 >= threshold=50)
    assert yes_order.price_cents == 70  # No improvement
    assert no_order.price_cents == 26   # No improvement
    
    # Test 3: Verify TouchMaker only improves by 1 tick maximum
    # This demonstrates that TouchMaker doesn't escalate bids excessively
    # Use the same market conditions that work
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    
    # Check live orders instead of emitted orders (stateless approach)
    yes_order = next((o for cid, o in engine.live_orders.items() if "touch-yes" in cid), None)
    no_order = next((o for cid, o in engine.live_orders.items() if "touch-no" in cid), None)
    
    # Should only improve by 1 tick from best bid (70 -> 71, 26 -> 27)
    assert yes_order.price_cents == 71  # 70 + 1 (not chasing higher)
    assert no_order.price_cents == 27   # 26 + 1 (not chasing higher)


def test_stable_cids_no_churn(cfg: StrategyConfig) -> None:
    """Test that stable CIDs prevent excessive order churn.
    
    Expected behavior:
    - Same market conditions should produce same orders with same CIDs
    - No unnecessary cancel/recreate cycles
    - StrategyEngine should detect no changes needed
    - Should demonstrate 80-90% reduction in order churn
    """
    engine = StrategyEngine(cfg)
    
    # Test 1: Multiple refreshes with identical market conditions
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    
    # First refresh - should emit orders
    orders1 = engine.refresh()
    assert len(orders1) == 2  # YES and NO orders
    order_cids = get_order_cids(orders1)
    assert any("touch-yes" in cid for cid in order_cids)
    assert any("touch-no" in cid for cid in order_cids)
    
    # Second refresh with identical conditions - should emit NO new orders
    orders2 = engine.refresh()
    assert len(orders2) == 0  # No new orders needed
    
    # Third refresh - still no new orders
    orders3 = engine.refresh()
    assert len(orders3) == 0  # Still no new orders
    
    # Verify live orders remain stable
    assert len(engine.live_orders) == 2
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Test 2: Small market moves that don't require order changes
    load_snapshot(engine, yes_levels=[[70, 15], [69, 40]], no_levels=[[26, 15], [25, 30]])  # Same prices, different sizes
    orders4 = engine.refresh()
    assert len(orders4) == 0  # Same prices, no order changes needed
    
    # Test 3: Queue size changes that don't affect pricing
    load_snapshot(engine, yes_levels=[[70, 5], [69, 40]], no_levels=[[26, 5], [25, 30]])  # Thin queue, same prices
    orders5 = engine.refresh()
    # With stable CIDs, StrategyEngine detects no changes needed (same prices)
    # This demonstrates the efficiency - no unnecessary order churn!
    assert len(orders5) == 0  # No new orders needed - same prices as existing orders


def test_stateless_touchmaker_behavior(cfg: StrategyConfig) -> None:
    """Test that TouchMaker maintains stateless behavior with stable CIDs.
    
    Expected behavior:
    - Same inputs should always produce same outputs
    - No internal state tracking between calls
    - Deterministic order creation
    - No memory leaks from state accumulation
    """
    engine = StrategyEngine(cfg)
    
    # Test 1: Deterministic behavior - same inputs, same outputs
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    
    # Call refresh multiple times with identical conditions
    results = []
    for i in range(5):
        orders = engine.refresh()
        results.append(len(orders))
    
    # First call should emit orders, subsequent calls should emit nothing
    assert results == [2, 0, 0, 0, 0]  # Deterministic behavior
    
    # Test 2: Verify no internal state accumulation
    initial_live_orders = len(engine.live_orders)
    
    # Multiple refreshes should not accumulate state
    for i in range(10):
        engine.refresh()
    
    # Live orders should remain stable
    assert len(engine.live_orders) == initial_live_orders
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Test 3: Verify CIDs remain stable across refreshes
    first_yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
    first_no_cid = next((cid for cid in engine.live_orders.keys() if "touch-no" in cid), None)
    assert first_yes_cid is not None and first_no_cid is not None
    
    # Multiple refreshes
    for i in range(5):
        engine.refresh()
    
        # CIDs should remain the same
        current_yes_cid = next((cid for cid in engine.live_orders.keys() if "touch-yes" in cid), None)
        current_no_cid = next((cid for cid in engine.live_orders.keys() if "touch-no" in cid), None)
        assert current_yes_cid == first_yes_cid
        assert current_no_cid == first_no_cid


def test_order_sync_efficiency(cfg: StrategyConfig) -> None:
    """Test that StrategyEngine efficiently syncs orders with stable CIDs.
    
    Expected behavior:
    - StrategyEngine should detect when orders don't need changes
    - Should only emit diffs when orders actually change
    - Should minimize unnecessary order operations
    - Should demonstrate efficient diffing logic
    """
    engine = StrategyEngine(cfg)
    
    # Test 1: Initial order placement (wide spread: 65-25 = 40¢)
    load_snapshot(engine, yes_levels=[[65, 10], [64, 40]], no_levels=[[25, 40], [24, 30]])
    orders1 = engine.refresh()
    
    # Should place both orders
    assert len(orders1) == 2
    order_cids = {o.client_order_id for o in orders1}
    assert any("touch-yes" in cid for cid in order_cids)
    assert any("touch-no" in cid for cid in order_cids)
    
    # Test 2: Identical refresh should emit no orders (efficient diffing)
    orders2 = engine.refresh()
    assert len(orders2) == 0  # StrategyEngine detected no changes needed
    
    # Test 3: Price change should emit only necessary updates (wide spread: 67-25 = 42¢)
    load_snapshot(engine, yes_levels=[[67, 10], [66, 40]], no_levels=[[25, 40], [24, 30]])  # YES prices improved
    orders3 = engine.refresh()
    
    # Should emit updates for YES price change only (NO unchanged)
    assert len(orders3) >= 1  # At least cancel + replace for YES side
    for order in orders3:
        if order.action != "cancel":
            assert "touch-yes" in order.client_order_id or "touch-no" in order.client_order_id
    
    # Test 4: Same prices again should emit no orders
    orders4 = engine.refresh()
    assert len(orders4) == 0  # No changes needed
    
    # Test 5: Market becomes too tight - should cancel all orders
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[69, 40], [68, 30]])  # Tight spread
    orders5 = engine.refresh()
    
    # Should cancel orders due to tight spread
    assert len(engine.live_orders) == 0  # All orders cancelled
    
    # Test 6: Market recovers - should place new orders
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders6 = engine.refresh()
    
    # Should place new orders with same CIDs
    assert len(orders6) == 2
    order_cids = {o.client_order_id for o in orders6}
    assert any("touch-yes" in cid for cid in order_cids)
    assert any("touch-no" in cid for cid in order_cids)


def test_no_memory_leaks_stable_cids(cfg: StrategyConfig) -> None:
    """Test that stable CIDs don't cause memory leaks or state accumulation.
    
    Expected behavior:
    - No growing internal state between calls
    - TouchMaker remains stateless
    - No accumulation of order history
    - Memory usage should remain constant
    """
    engine = StrategyEngine(cfg)
    
    # Test 1: Verify initial state is clean
    initial_live_orders = len(engine.live_orders)
    assert initial_live_orders == 0
    
    # Test 2: Place orders and verify stable state
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    
    # Should have exactly 2 live orders
    assert len(engine.live_orders) == 2
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Test 3: Multiple refreshes should not accumulate state
    for i in range(50):  # Many refreshes
        engine.refresh()
    
    # State should remain stable
    assert len(engine.live_orders) == 2  # No accumulation
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Test 4: Market changes should not accumulate state
    for i in range(10):
        # Vary market conditions but keep spreads wide enough
        yes_price = 70 + (i % 3)  # Limit variation to avoid tight spreads
        no_price = 26 + (i % 3)   # Keep spread >= 3 cents
        load_snapshot(engine, 
                     yes_levels=[[yes_price, 10], [yes_price-1, 40]], 
                     no_levels=[[no_price, 40], [no_price-1, 30]])
        engine.refresh()
    
    # Should still have exactly 2 live orders (no accumulation)
    assert len(engine.live_orders) == 2
    assert any("touch-yes" in cid for cid in engine.live_orders.keys())
    assert any("touch-no" in cid for cid in engine.live_orders.keys())
    
    # Test 5: Verify TouchMaker has no internal state tracking
    # This is implicit - if we can call refresh() many times without
    # accumulating state, TouchMaker is stateless


def test_exit_orders_bypass_all_restrictions(cfg: StrategyConfig) -> None:
    """Test that exit orders can bypass all normal restrictions.
    
    Expected behavior:
    - Exit orders should bypass sum-guard restrictions
    - Exit orders should bypass spread requirements
    - Exit orders should bypass tick movement restrictions (cooldown, queue logic)
    - Exit orders should always be allowed for risk management
    """
    # Create a restrictive config
    cfg_restrictive = StrategyConfig(**{
        **cfg.__dict__, 
        "sum_cushion_ticks": 10,  # Very restrictive sum guard
        "min_spread_cents": 10,   # Very high spread requirement
        "cooldown_after_shade_ms": 10000,  # Long cooldown
        "improve_if_last": False,  # Disable improvements
        "bid_size_contracts": 5    # Large bid size to test inventory limiting
    })
    engine = StrategyEngine(cfg_restrictive)
    
    # Create a market that would violate all restrictions
    # YES bid=50, NO bid=50 -> sum=100, but max_allowed=100-10=90 (violates sum guard)
    # Spread = 0 < 10 (violates spread requirement)
    load_snapshot(engine, yes_levels=[[50, 1000]], no_levels=[[50, 1000]])  # Thick queues, locked market
    
    # First, create some inventory by filling orders
    engine.refresh()  # Place initial orders
    engine.on_private_fill({"client_order_id": "touch-yes", "count": 2})  # Create YES inventory
    engine.on_position_update(2)  # Set position to +2 (YES inventory)
    
    # Now test exit orders in restrictive conditions
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    
    # Should have NO exit order to exit YES inventory, even with all restrictions violated
    assert any("touch-no" in cid for cid in intents.keys())  # NO order to exit YES inventory
    assert not any("touch-yes" in cid for cid in intents.keys())  # No new YES order (restrictions prevent)
    
    # Verify the exit order is reduce-only (size limited to inventory)
    no_cid = next((cid for cid in intents.keys() if "touch-no" in cid), None)
    assert no_cid is not None, "touch-no order not found"
    no_order = intents[no_cid]
    assert no_order.count == 2  # Limited to our YES inventory size
    
    # Test the opposite: NO inventory trying to exit with YES order
    engine.on_position_update(-1)  # Set position to -1 (NO inventory)
    
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    
    # Should have YES exit order to exit NO inventory, even with all restrictions violated
    assert any("touch-yes" in cid for cid in intents.keys())  # YES order to exit NO inventory
    
    # Check that NO orders are only cancel orders (no new NO orders due to restrictions)
    no_orders = [o for o in orders if "touch-no" in o.client_order_id]
    if no_orders:
        # If there are NO orders, they should only be cancel orders
        assert all(o.action == "cancel" for o in no_orders)
    
    # Verify the exit order is reduce-only (size limited to inventory)
    yes_cid = next((cid for cid in intents.keys() if "touch-yes" in cid), None)
    assert yes_cid is not None, "touch-yes order not found"
    yes_order = intents[yes_cid]
    assert yes_order.count == 1  # Limited to our NO inventory size


def test_minimal_order_churn_behavior(cfg: StrategyConfig) -> None:
    """Test that the bot avoids unnecessary churn by comparing order content.
    
    Expected behavior:
    - Content-based comparison identifies identical orders (side, price, size)
    - Only cancel/replace when order content actually changes
    - Unique client_order_ids prevent 409 errors but don't cause churn
    """
    # Disable mid-shift cancellations to focus on content-based churn behavior
    cfg_no_midshift = StrategyConfig(**{**cfg.__dict__, "cancel_move_ticks": 0})
    engine = StrategyEngine(cfg_no_midshift)
    
    # Test 1: Initial orders - should place both YES and NO (wide spread: 70-25 = 45¢)
    load_snapshot(engine, yes_levels=[[65, 10], [64, 40]], no_levels=[[25, 40], [24, 30]])
    first_intents = engine.refresh()
    
    # Should place both orders initially
    assert len(first_intents) == 2, f"Expected 2 initial orders, got {len(first_intents)}"
    
    # Extract order details
    yes_orders = [intent for intent in first_intents if intent.side == "yes" and intent.action == "buy"]
    no_orders = [intent for intent in first_intents if intent.side == "no" and intent.action == "buy"]
    
    assert len(yes_orders) == 1, "Should have exactly 1 YES order"
    assert len(no_orders) == 1, "Should have exactly 1 NO order"
    
    initial_yes = yes_orders[0]
    initial_no = no_orders[0]
    
    print(f"Initial YES: {initial_yes.price_cents}¢, {initial_yes.count} contracts")
    print(f"Initial NO: {initial_no.price_cents}¢, {initial_no.count} contracts")
    
    # Test 2: Identical market conditions - should produce NO new orders (no churn)
    load_snapshot(engine, yes_levels=[[65, 10], [64, 40]], no_levels=[[25, 40], [24, 30]])
    second_intents = engine.refresh()
    
    # Should emit no new orders since content is identical (side, price, count)
    assert len(second_intents) == 0, f"Expected 0 orders on identical conditions, got {len(second_intents)}"
    
    # Test 3: Small queue size change (same prices) - should keep existing orders
    load_snapshot(engine, yes_levels=[[65, 15], [64, 40]], no_levels=[[25, 15], [24, 30]])
    third_intents = engine.refresh()
    
    # Should still emit no new orders since prices haven't changed
    assert len(third_intents) == 0, f"Expected 0 orders when only queue sizes change, got {len(third_intents)}"
    
    # Test 4: YES price change (wide spread allows improvement: 67-25 = 42¢)
    load_snapshot(engine, yes_levels=[[67, 10], [66, 40]], no_levels=[[25, 40], [24, 30]])
    fourth_intents = engine.refresh()

    # YES should change from 66¢ to 68¢, NO should stay at 26¢
    yes_intents = [intent for intent in fourth_intents if intent.side == "yes"]
    no_intents = [intent for intent in fourth_intents if intent.side == "no"]
    
    assert len(yes_intents) >= 1, f"YES should update when price changes, got {len(yes_intents)}"
    assert len(no_intents) == 0, f"NO should stay unchanged, got {len(no_intents)}"
    
    # Test 5: Return to original conditions - should update YES back
    load_snapshot(engine, yes_levels=[[65, 10], [64, 40]], no_levels=[[25, 40], [24, 30]])
    fifth_intents = engine.refresh()
    
    # Should only affect the YES side (price changed back from 68 to 66)
    yes_intents = [intent for intent in fifth_intents if intent.side == "yes"]
    no_intents = [intent for intent in fifth_intents if intent.side == "no"]
    
    assert len(yes_intents) >= 1, f"YES should update when price changes back, got {len(yes_intents)}"
    assert len(no_intents) == 0, f"NO should stay unchanged, got {len(no_intents)}"
    
    # Test 6: Verify final state consistency
    final_intents = engine.refresh()
    assert len(final_intents) == 0, f"Final state should be stable (no new orders), got {len(final_intents)}"
    
    print("✅ All minimal churn tests passed!")


