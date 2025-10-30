"""
Tests for fill-triggered order regeneration logic.

Tests that orders are properly regenerated after fills to ensure:
- Exit orders are placed immediately when entering a position
- Market making orders are updated when position changes
- Order generation respects throttling (MQT, hysteresis)
- Works correctly for both YES and NO positions

All tests use dry_run=True to avoid any actual API calls. In dry run mode,
orders are still tracked locally with fake IDs, allowing us to verify the
logic without side effects.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from typing import Dict, Any, List

from market_making.market_types import MarketConfig, MarketState, PositionState, ActiveOrder, OrderIntent
from market_making.acadia import AcadiaStrategy
from market_making.acadia_listener import MarketListener
from market_making.orderbook_tracker import OrderBookTracker


class MockAPIClient:
    """Mock Kalshi API client for testing."""
    
    def __init__(self):
        self.created_orders = []
        self.cancelled_orders = []
        
    def create_order(self, ticker, action, side, count, price_cents, order_type):
        """Mock order creation."""
        order_id = f"test_order_{len(self.created_orders)}"
        self.created_orders.append({
            'order_id': order_id,
            'ticker': ticker,
            'action': action,
            'side': side,
            'count': count,
            'price_cents': price_cents,
            'order_type': order_type
        })
        return {'order_id': order_id}
    
    def cancel_order(self, order_id):
        """Mock order cancellation."""
        self.cancelled_orders.append(order_id)
        return True


class MockWSClient:
    """Mock WebSocket client for testing."""
    pass


class TestFillTriggeredOrderGeneration:
    """Test that fills trigger immediate order regeneration."""
    
    def setup_method(self):
        """Set up test environment."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="Yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0,
            exit_edge_threshold_cents=2
        )
        
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
        self.api_client = MockAPIClient()
        self.ws_client = MockWSClient()
        
        self.listener = MarketListener(
            market_config=self.market_config,
            ws_client=self.ws_client,
            api_client=self.api_client,
            strategy=self.strategy
        )
        
        # Initialize market state
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        # Initialize position state
        self.strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=0,
            position_limit=10,
            side="Yes",
            avg_entry_price_yes=None,
            avg_entry_price_no=None
        )
    
    @pytest.mark.asyncio
    async def test_fill_entering_position_generates_exit_orders(self):
        """When a fill enters a position, exit orders should be immediately placed."""
        # Simulate a buy fill that enters a position
        fill_payload = {
            "msg": {
                "market_ticker": "TEST",
                "side": "yes",
                "action": "buy",
                "count": 5,
                "price": 50,  # Bought at 50¢
                "order_id": "test_order_0"
            }
        }
        
        # Set up an initial market making order
        initial_order = OrderIntent(
            ticker="TEST",
            side="yes",
            price_cents=48,
            size=5,
            action="buy",
            intent_type="market_making"
        )
        self.strategy.order_manager.add_order("test_order_0", initial_order)
        
        # Process the fill
        await self.listener._on_fill(fill_payload)
        
        # Verify position was updated
        position_state = self.strategy.position_states["TEST"]
        assert position_state.position == 5
        assert position_state.avg_entry_price_yes == 50.0
        
        # In dry run mode, check the order manager's tracked orders instead of API calls
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Should have exit orders tracked
        exit_orders = [o for o in active_orders if o.action == 'sell' and o.side == 'yes']
        assert len(exit_orders) >= 1, f"Expected exit orders after fill, got: {active_orders}"
        
        # Exit order should be at or near the ask
        assert exit_orders[0].price_cents >= 51  # Should be at/near the ask (52)
    
    @pytest.mark.asyncio
    async def test_fill_exiting_position_regenerates_mm_orders(self):
        """When a fill exits a position, market making orders should be regenerated."""
        # Start with a position
        position_state = self.strategy.position_states["TEST"]
        position_state.position = 5
        position_state.avg_entry_price_yes = 45.0
        
        # Add an exit order
        exit_order = OrderIntent(
            ticker="TEST",
            side="yes",
            price_cents=52,
            size=5,
            action="sell",
            intent_type="exit"
        )
        self.strategy.order_manager.add_order("exit_order_1", exit_order)
        
        # Simulate a sell fill that exits the position
        fill_payload = {
            "msg": {
                "market_ticker": "TEST",
                "side": "yes",
                "action": "sell",
                "count": 5,
                "price": 53,  # Sold at 53¢ (8¢ profit!)
                "order_id": "exit_order_1"
            }
        }
        
        # Process the fill
        await self.listener._on_fill(fill_payload)
        
        # Verify position was updated
        assert position_state.position == 0
        
        # In dry run mode, check the order manager's tracked orders
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Should have generated new market making orders (not exit orders anymore)
        mm_orders = [o for o in active_orders if o.action == 'buy']
        assert len(mm_orders) >= 1, "Should have generated market making orders after exiting position"
    
    @pytest.mark.asyncio
    async def test_partial_fill_updates_orders(self):
        """Partial fills should trigger order updates with correct sizes."""
        # Start with no position
        position_state = self.strategy.position_states["TEST"]
        position_state.position = 0
        
        # Add an initial market making order
        mm_order = OrderIntent(
            ticker="TEST",
            side="yes",
            price_cents=48,
            size=10,
            action="buy",
            intent_type="market_making"
        )
        self.strategy.order_manager.add_order("mm_order_1", mm_order)
        
        # Simulate a partial fill (5 out of 10)
        fill_payload = {
            "msg": {
                "market_ticker": "TEST",
                "side": "yes",
                "action": "buy",
                "count": 5,
                "price": 48,
                "order_id": "mm_order_1"
            }
        }
        
        # Process the fill
        await self.listener._on_fill(fill_payload)
        
        # Verify position was updated
        assert position_state.position == 5
        assert position_state.avg_entry_price_yes == 48.0
        
        # In dry run mode, check the order manager's tracked orders
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Verify exit orders were placed for the filled amount
        exit_orders = [o for o in active_orders if o.action == 'sell' and o.side == 'yes']
        assert len(exit_orders) >= 1
        
        # Exit order size should match filled amount (5)
        total_exit_size = sum(o.size for o in exit_orders)
        assert total_exit_size == 5
    
    @pytest.mark.asyncio
    async def test_no_fill_no_opposite_ticker(self):
        """Fills for different tickers should not affect this market."""
        initial_position = self.strategy.position_states["TEST"].position
        
        # Simulate a fill for a different ticker
        fill_payload = {
            "msg": {
                "market_ticker": "OTHER_TICKER",
                "side": "yes",
                "action": "buy",
                "count": 5,
                "price": 50,
                "order_id": "other_order"
            }
        }
        
        # Process the fill
        await self.listener._on_fill(fill_payload)
        
        # Verify position was NOT updated
        assert self.strategy.position_states["TEST"].position == initial_position
        
        # Verify no orders were placed
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        assert len(active_orders) == 0
    
    @pytest.mark.asyncio
    async def test_fill_with_missing_market_data_skips_generation(self):
        """If market data is missing, order generation should be skipped gracefully."""
        # Clear market data
        self.strategy.market_states["TEST"].yes_bid = None
        self.strategy.market_states["TEST"].yes_ask = None
        
        # Simulate a fill
        fill_payload = {
            "msg": {
                "market_ticker": "TEST",
                "side": "yes",
                "action": "buy",
                "count": 5,
                "price": 50,
                "order_id": "test_order"
            }
        }
        
        # Process the fill - should not crash
        await self.listener._on_fill(fill_payload)
        
        # Position should still be updated
        assert self.strategy.position_states["TEST"].position == 5
        
        # But no orders should be placed (no market data)
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        assert len(active_orders) == 0


class TestFillWithDryRun:
    """Test that dry run mode prevents actual API calls while still tracking orders."""
    
    def setup_method(self):
        """Set up test environment with dry run enabled."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="Yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0,
            exit_edge_threshold_cents=2
        )
        
        # Enable dry run mode
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
        self.api_client = MockAPIClient()
        self.ws_client = MockWSClient()
        
        self.listener = MarketListener(
            market_config=self.market_config,
            ws_client=self.ws_client,
            api_client=self.api_client,
            strategy=self.strategy
        )
        
        # Initialize states
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        self.strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=0,
            position_limit=10,
            side="Yes"
        )
    
    @pytest.mark.asyncio
    async def test_dry_run_logs_but_does_not_place_orders(self):
        """In dry run mode, orders should be logged but not actually placed."""
        # Simulate a fill
        fill_payload = {
            "msg": {
                "market_ticker": "TEST",
                "side": "yes",
                "action": "buy",
                "count": 5,
                "price": 50,
                "order_id": "test_order"
            }
        }
        
        # Process the fill
        await self.listener._on_fill(fill_payload)
        
        # Position should be updated
        assert self.strategy.position_states["TEST"].position == 5
        
        # In dry run mode, no actual API calls should be made
        assert len(self.api_client.created_orders) == 0
        
        # But orders should be tracked locally with fake IDs
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Should have dry run orders in tracking
        dry_run_orders = [o for o in active_orders if o.order_id.startswith("dry_run_")]
        assert len(dry_run_orders) >= 1, "Should have fake orders in dry run mode"


class TestNOPositionFills:
    """Test fill-triggered order generation for NO positions."""
    
    def setup_method(self):
        """Set up test environment for NO side trading."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="No",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0,
            exit_edge_threshold_cents=2
        )
        
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
        self.api_client = MockAPIClient()
        self.ws_client = MockWSClient()
        
        self.listener = MarketListener(
            market_config=self.market_config,
            ws_client=self.ws_client,
            api_client=self.api_client,
            strategy=self.strategy
        )
        
        # Initialize states
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        self.strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=0,
            position_limit=10,
            side="No"
        )
    
    @pytest.mark.asyncio
    async def test_no_position_fill_generates_exit_orders(self):
        """When buying NO contracts, exit orders should be placed."""
        # Simulate buying NO contracts
        fill_payload = {
            "msg": {
                "market_ticker": "TEST",
                "side": "no",
                "action": "buy",
                "count": 5,
                "price": 50,
                "order_id": "test_order"
            }
        }
        
        # Process the fill
        await self.listener._on_fill(fill_payload)
        
        # Verify position was updated (negative for NO)
        position_state = self.strategy.position_states["TEST"]
        assert position_state.position == -5
        assert position_state.avg_entry_price_no == 50.0
        
        # In dry run mode, check the order manager's tracked orders
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Verify exit orders were placed
        exit_orders = [o for o in active_orders if o.action == 'sell' and o.side == 'no']
        assert len(exit_orders) >= 1, "Should have generated NO exit orders"


class TestPositionSyncTriggeredOrders:
    """Test that position sync messages also trigger order regeneration."""
    
    def setup_method(self):
        """Set up test environment."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="Yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0,
            exit_edge_threshold_cents=2
        )
        
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
        self.api_client = MockAPIClient()
        self.ws_client = MockWSClient()
        
        self.listener = MarketListener(
            market_config=self.market_config,
            ws_client=self.ws_client,
            api_client=self.api_client,
            strategy=self.strategy
        )
        
        # Initialize states
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        self.strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=0,
            position_limit=10,
            side="Yes"
        )
    
    @pytest.mark.asyncio
    async def test_position_sync_with_new_position_generates_orders(self):
        """Position sync that reveals a new position should trigger order generation."""
        # Simulate position sync showing we have a position (maybe from another session)
        position_payload = {
            "msg": {
                "market_ticker": "TEST",
                "position": 5,
                "position_cost": 250000  # 5 contracts @ 50¢ = $2.50 = 250000 centi-cents
            }
        }
        
        # Process the position update
        await self.listener._on_market_position(position_payload)
        
        # Verify position was updated
        assert self.strategy.position_states["TEST"].position == 5
        
        # In dry run mode, check the order manager's tracked orders
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Verify orders were generated
        # Should have exit orders for the position
        assert len(active_orders) >= 1, "Should generate orders on position sync"
    
    @pytest.mark.asyncio
    async def test_position_sync_no_change_skips_generation(self):
        """Position sync with no change should not trigger order generation."""
        # Set initial position
        self.strategy.position_states["TEST"].position = 5
        
        # Simulate position sync with same position
        position_payload = {
            "msg": {
                "market_ticker": "TEST",
                "position": 5,
                "position_cost": 250000
            }
        }
        
        # Process the position update
        await self.listener._on_market_position(position_payload)
        
        # Position unchanged
        assert self.strategy.position_states["TEST"].position == 5
        
        # In dry run mode, check the order manager's tracked orders
        active_orders = self.strategy.order_manager.state.active_orders.get("TEST", [])
        
        # Should not have placed any orders (position unchanged)
        assert len(active_orders) == 0


def run_async_test(test_func):
    """Helper to run async tests."""
    asyncio.run(test_func())


if __name__ == "__main__":
    print("Running fill-triggered order generation tests...")
    
    # Test fill entering position
    test1 = TestFillTriggeredOrderGeneration()
    test1.setup_method()
    run_async_test(test1.test_fill_entering_position_generates_exit_orders)
    print("✓ Fill entering position generates exit orders")
    
    # Test fill exiting position
    test2 = TestFillTriggeredOrderGeneration()
    test2.setup_method()
    run_async_test(test2.test_fill_exiting_position_regenerates_mm_orders)
    print("✓ Fill exiting position regenerates market making orders")
    
    # Test partial fill
    test3 = TestFillTriggeredOrderGeneration()
    test3.setup_method()
    run_async_test(test3.test_partial_fill_updates_orders)
    print("✓ Partial fills update orders correctly")
    
    # Test wrong ticker
    test4 = TestFillTriggeredOrderGeneration()
    test4.setup_method()
    run_async_test(test4.test_no_fill_no_opposite_ticker)
    print("✓ Fills for other tickers are ignored")
    
    # Test missing market data
    test5 = TestFillTriggeredOrderGeneration()
    test5.setup_method()
    run_async_test(test5.test_fill_with_missing_market_data_skips_generation)
    print("✓ Missing market data handled gracefully")
    
    # Test dry run
    test6 = TestFillWithDryRun()
    test6.setup_method()
    run_async_test(test6.test_dry_run_logs_but_does_not_place_orders)
    print("✓ Dry run mode works correctly")
    
    # Test NO positions
    test7 = TestNOPositionFills()
    test7.setup_method()
    run_async_test(test7.test_no_position_fill_generates_exit_orders)
    print("✓ NO position fills generate exit orders")
    
    # Test position sync
    test8 = TestPositionSyncTriggeredOrders()
    test8.setup_method()
    run_async_test(test8.test_position_sync_with_new_position_generates_orders)
    print("✓ Position sync triggers order generation")
    
    test9 = TestPositionSyncTriggeredOrders()
    test9.setup_method()
    run_async_test(test9.test_position_sync_no_change_skips_generation)
    print("✓ Position sync with no change skips generation")
    
    print("\n✅ All fill-triggered order generation tests passed!")

