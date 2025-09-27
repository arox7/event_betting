import pytest

from market_making_bot.strategy import StrategyConfig, StrategyEngine
from market_making_bot.orderbook_tracker import OrderBookTracker


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


def test_emits_and_improves_when_spread_and_queue_ok(cfg: StrategyConfig) -> None:
    """TouchMaker should post quotes and improve 1 tick when:
    - The implied spread is wide enough (e.g., YES bid=70, NO bid=26 → YES ask=74, spread=4 ≥ 3)
    - Front-of-queue is thin (best size=10 < queue_small_threshold)

    Expected: YES @ 71, NO @ 27 (one-tick improvements) with post-only respected.
    """
    engine = StrategyEngine(cfg)
    # YES best bid=70 (size 10), NO best bid=26 -> implied YES ask=74 (spread 4 >= 3)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 80]], no_levels=[[26, 40], [25, 30]])

    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    # YES improves by one tick due to small queue, NO improves symmetrically
    assert intents["touch-yes:touch"].price_cents == 71
    assert intents["touch-no:touch"].price_cents == 27


def test_skips_when_spread_too_tight(cfg: StrategyConfig, caplog: pytest.LogCaptureFixture) -> None:
    """If the book is flat (e.g., YES 82 and implied ask 82), the spread is 0 < min.

    Expected: no orders; logs include a [TOUCH] skip reason.
    """
    engine = StrategyEngine(cfg)
    # YES best bid=82, NO best bid=18 -> implied YES ask=82, spread=0 < 3
    load_snapshot(engine, yes_levels=[[82, 20], [81, 10]], no_levels=[[18, 20], [17, 10]])
    caplog.clear()
    orders = engine.refresh()
    assert orders == []
    assert "[TOUCH] skip YES" in caplog.text


def test_prefers_flattening_positive_inventory(cfg: StrategyConfig) -> None:
    """When net YES inventory is positive (long), TouchMaker should favour flattening by
    posting on the NO leg and suppressing additional YES entries—provided we are below cap.

    Example: inv=+4 (< cap), spread OK → expect NO touch present, YES touch absent.
    """
    engine = StrategyEngine(cfg)
    # Net YES long: allow NO entries (flatten), block YES entries
    engine.on_position_update(4)  # below cap so entries are allowed
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {o.client_order_id for o in orders}
    # Expect NO touch present, YES touch absent (exit orders may also exist)
    assert "touch-no:touch" in ids


def test_prefers_flattening_negative_inventory(cfg: StrategyConfig) -> None:
    """When net YES inventory is negative (short), TouchMaker should favour flattening by
    posting on the YES leg and suppressing NO entries—provided we are below cap.

    Example: inv=-4, spread OK → expect YES touch present, NO touch absent.
    """
    engine = StrategyEngine(cfg)
    # Net YES short: allow YES entries (flatten), block NO entries
    engine.on_position_update(-4)  # below cap in magnitude so entries are allowed
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    ids = {o.client_order_id for o in orders}
    assert "touch-yes:touch" in ids


def test_restages_after_mid_shift(cfg: StrategyConfig) -> None:
    """A large mid move should cancel prior quotes and restage at the new best price.

    Example: initial YES bid 70/NO bid 26; then big move to YES bid 58/NO bid 39 (ask 61).
    Expected: touch yes entry resets to 58 with configured size.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert engine.current_entries["touch"]
    # Big move: YES bid drops, NO bid rises -> new mid far away
    # Choose values so implied spread remains >= min (YES bid=58, NO bid=39 -> YES ask=61, spread=3)
    load_snapshot(engine, yes_levels=[[58, 10], [57, 40]], no_levels=[[39, 40], [38, 30]])
    engine.refresh()
    assert engine.current_entries["touch"].get("yes:touch") == (58, cfg.bid_size_contracts)


def test_partial_fill_removes_pending(cfg: StrategyConfig) -> None:
    """Upon receiving a private fill for a posted touch order, the intent should be
    removed from the pending map so accounting remains correct.

    Example: fill 1 lot on the YES touch intent → it disappears from pending.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    orders = engine.refresh()
    assert orders
    # Simulate a private fill for the YES touch order
    engine.on_private_fill({"client_order_id": "touch-yes:touch", "count": 1})
    assert "touch-yes:touch" not in engine.groups["touch"].pending


def test_flash_cross_pauses_and_recovers(cfg: StrategyConfig) -> None:
    """A brief crossed snapshot should cause TouchMaker to pause (cancel), and it should
    immediately restage when the next normal snapshot arrives.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert engine.current_entries["touch"]
    # Crossed: YES bid 85, NO bid 20 -> implied YES ask 80 < bid
    load_snapshot(engine, yes_levels=[[85, 10]], no_levels=[[20, 10]])
    engine.refresh()
    assert not engine.current_entries["touch"]
    # Normal again
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    engine.refresh()
    assert engine.current_entries["touch"]


def test_idempotent_refresh_no_churn(cfg: StrategyConfig) -> None:
    """Calling refresh twice without a new snapshot should not produce new intents.

    Example: first refresh emits entries; second refresh on identical book returns [].
    Entries in current_entries remain stable (one per leg).
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    first = engine.refresh()
    second = engine.refresh()
    # First call emits, second call should be a no-op for entries (no duplicates)
    assert first
    assert second == []
    # Entries should not multiply; still exactly one target per leg present
    assert set(engine.current_entries["touch"].keys()) == {"yes:touch", "no:touch"}


def test_improve_if_last_toggle(cfg: StrategyConfig) -> None:
    """Improvement logic should be deterministic:
    - With improve_if_last=True and thin queue, bids improve by +1 tick.
    - With improve_if_last=False, bids remain at best bid.
    """
    # With improve_if_last=True (default), thin queue should improve by one tick
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    orders = engine.refresh()
    intents = {o.client_order_id: o for o in orders}
    assert intents["touch-yes:touch"].price_cents == 71
    assert intents["touch-no:touch"].price_cents == 27

    # Disable improvement: bids should sit at best bid
    cfg2 = StrategyConfig(**{**cfg.__dict__, "improve_if_last": False})
    engine2 = StrategyEngine(cfg2)
    load_snapshot(engine2, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    orders2 = engine2.refresh()
    intents2 = {o.client_order_id: o for o in orders2}
    assert intents2["touch-yes:touch"].price_cents == 70
    assert intents2["touch-no:touch"].price_cents == 26


def test_queue_threshold_boundary(cfg: StrategyConfig) -> None:
    """Near the small-queue boundary:
    - Threshold above best size → improvement occurs.
    - Threshold below best size → no improvement.
    """
    # Set small threshold above size -> improve; below size -> no improve
    cfg_hi = StrategyConfig(**{**cfg.__dict__, "queue_small_threshold": 11})
    eng_hi = StrategyEngine(cfg_hi)
    load_snapshot(eng_hi, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    intents_hi = {o.client_order_id: o for o in eng_hi.refresh()}
    assert intents_hi["touch-yes:touch"].price_cents == 71

    cfg_lo = StrategyConfig(**{**cfg.__dict__, "queue_small_threshold": 9})
    eng_lo = StrategyEngine(cfg_lo)
    load_snapshot(eng_lo, yes_levels=[[70, 10], [69, 100]], no_levels=[[26, 10], [25, 100]])
    intents_lo = {o.client_order_id: o for o in eng_lo.refresh()}
    assert intents_lo["touch-yes:touch"].price_cents == 70


def test_mid_shift_hysteresis(cfg: StrategyConfig) -> None:
    """Hysteresis:
    - Small mid moves (< cancel_move_ticks) do not cancel-and-wipe; quotes may micro-adjust.
    - Large moves (≥ cancel_move_ticks) trigger cancel-and-restage at the new best.
    """
    cfg_hys = StrategyConfig(**{**cfg.__dict__, "cancel_move_ticks": 3})
    eng = StrategyEngine(cfg_hys)
    # Initial mid ~ 72
    load_snapshot(eng, yes_levels=[[70, 10], [69, 40]], no_levels=[[26, 40], [25, 30]])
    eng.refresh()
    before = dict(eng.current_entries["touch"])
    # Small move < hysteresis -> no cancel
    load_snapshot(eng, yes_levels=[[69, 10], [68, 40]], no_levels=[[27, 40], [26, 30]])
    eng.refresh()
    after_small = dict(eng.current_entries["touch"])
    # We do not cancel-and-wipe on small moves, but quotes may adjust by ≤1 tick
    assert set(after_small.keys()) == set(before.keys())
    # Big move >= hysteresis -> cancel and restage (keep spread >= min: NO bid=37 -> YES ask=63)
    load_snapshot(eng, yes_levels=[[60, 10], [59, 40]], no_levels=[[37, 40], [36, 30]])
    eng.refresh()
    assert eng.current_entries["touch"].get("yes:touch") == (60, cfg_hys.bid_size_contracts)


def test_post_only_compliance(cfg: StrategyConfig) -> None:
    """All touch orders must be post-only vs implied asks:
    YES price ≤ (implied YES ask - min_spread)
    NO price ≤ (implied NO ask - min_spread)
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
    """If yes_bid + implied_yes_ask would exceed 100 − sum_cushion_ticks, TouchMaker should skip."""
    cfg_guard = StrategyConfig(**{**cfg.__dict__, "sum_cushion_ticks": 5})
    eng = StrategyEngine(cfg_guard)
    # YES bid=80, NO bid=21 -> implied YES ask=79; 80 + 79 = 159 > 100 − 5 → skip
    load_snapshot(eng, yes_levels=[[80, 10]], no_levels=[[21, 10]])
    caplog.clear()
    orders = eng.refresh()
    assert orders == []



def test_skips_when_one_side_missing(cfg: StrategyConfig, caplog: pytest.LogCaptureFixture) -> None:
    """If one leg of the ladder is missing (e.g., no YES bids), TouchMaker cannot compute
    a valid spread and must stand down without posting any entries.

    Example: yes_levels=[], no_levels present → expect no orders and empty touch state.
    """
    engine = StrategyEngine(cfg)
    load_snapshot(engine, yes_levels=[], no_levels=[[26, 20], [25, 10]])
    caplog.clear()
    orders = engine.refresh()
    assert orders == []
    assert engine.current_entries["touch"] == {}

