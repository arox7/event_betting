"""
Unit tests for Acadia strategy throttling logic.

Tests:
1. Minimum Quote Time (MQT) enforcement
2. Price delta hysteresis
3. Minimum spread check (opening orders only)
4. Order sync with throttling
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import sys
from pathlib import Path
# Add parent directory to path to import from market_making
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from market_making.market_types import (
    MarketConfig, MarketState, PositionState, OrderIntent, ActiveOrder
)
from market_making.acadia import AcadiaStrategy
from market_making.order_manager import OrderManager


class TestMinimumQuoteTime:
    """Test that orders respect minimum quote time (MQT) before being modified."""
    
    def test_order_cannot_be_modified_before_mqa(self):
        """Order should not be modifiable before MQT expires."""
        now = datetime.now(timezone.utc)
        order = ActiveOrder(
            order_id="test123",
            ticker="TEST",
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=now,
            remaining_size=10,
            last_modified_at=now
        )
        
        # Should not be modifiable immediately
        assert not order.can_modify(min_quote_time_seconds=1.0)
        
    def test_order_can_be_modified_after_mqa(self):
        """Order should be modifiable after MQT expires."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=2.0)
        order = ActiveOrder(
            order_id="test123",
            ticker="TEST",
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=past,
            remaining_size=10,
            last_modified_at=past
        )
        
        # Should be modifiable after 2 seconds with 1s MQT
        assert order.can_modify(min_quote_time_seconds=1.0)
        
    def test_order_at_exact_mqa_boundary(self):
        """Order at exactly MQT should be modifiable."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=1.0)
        order = ActiveOrder(
            order_id="test123",
            ticker="TEST",
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=past,
            remaining_size=10,
            last_modified_at=past
        )
        
        # Should be modifiable at exactly 1 second
        assert order.can_modify(min_quote_time_seconds=1.0)


class TestPriceDeltaHysteresis:
    """Test that orders only requote when price moves significantly."""
    
    def setup_method(self):
        """Set up order manager for tests."""
        self.order_manager = OrderManager()
        self.ticker = "TEST"
        
    def test_small_price_move_keeps_order(self):
        """Order should be kept when price moves less than threshold."""
        # Add existing order
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=2.0)
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=past,
            remaining_size=10,
            last_modified_at=past
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target is only 0.5 cents away (below 1 cent threshold)
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=50,  # No change
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1
        )
        
        # Should keep existing order (exact match)
        assert len(actions['keep']) == 1
        assert len(actions['cancel']) == 0
        assert len(actions['add']) == 0
        
    def test_large_price_move_replaces_order(self):
        """Order should be replaced when price moves >= threshold."""
        # Add existing order
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=2.0)
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=past,
            remaining_size=10,
            last_modified_at=past
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target is 2 cents away (above 1 cent threshold)
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=52,  # Moved 2 cents
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1
        )
        
        # Should cancel old and add new
        assert len(actions['keep']) == 0
        assert len(actions['cancel']) == 1
        assert len(actions['add']) == 1
        assert actions['cancel'][0].price_cents == 50
        assert actions['add'][0].price_cents == 52
        
    def test_price_at_exact_threshold(self):
        """Order should be replaced when price moves exactly at threshold."""
        # Add existing order
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=2.0)
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=past,
            remaining_size=10,
            last_modified_at=past
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target is exactly 1 cent away (at threshold)
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=51,  # Exactly 1 cent
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1
        )
        
        # Should replace (>= threshold)
        assert len(actions['cancel']) == 1
        assert len(actions['add']) == 1


class TestCombinedMQTAndHysteresis:
    """Test interaction between MQT and price hysteresis."""
    
    def setup_method(self):
        """Set up order manager for tests."""
        self.order_manager = OrderManager()
        self.ticker = "TEST"
        
    def test_large_move_replaces_immediately(self):
        """Large price move should replace order immediately (no MQT throttling)."""
        # Add existing order placed very recently
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=0.5)  # Only 0.5s ago
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=recent,
            remaining_size=10,
            last_modified_at=recent
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target is 5 cents away (large move)
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=55,  # Large move
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1
        )
        
        # Should replace immediately (price moved >= threshold)
        assert len(actions['keep']) == 0
        assert len(actions['cancel']) == 1
        assert len(actions['add']) == 1


class TestMinimumSpreadCheck:
    """Test that minimum spread check only applies to opening orders."""
    
    def setup_method(self):
        """Set up strategy for tests."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0
        )
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
        
    def test_opening_orders_blocked_when_spread_too_tight(self):
        """Opening orders should not be generated when spread < min_spread."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=49,
            yes_ask=50,  # 1 cent spread (below 2 cent min)
            no_bid=50,
            no_ask=51
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=0,  # No position
            position_limit=10,
            side="yes"
        )
        
        # Should return empty list (spread too tight)
        orders = self.strategy._generate_opening_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 0
        
    def test_opening_orders_allowed_when_spread_wide_enough(self):
        """Opening orders should be generated when spread >= min_spread."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=50,  # 2 cent spread (at min)
            no_bid=50,
            no_ask=52
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=0,  # No position
            position_limit=10,
            side="yes"
        )
        
        # Should generate opening orders
        orders = self.strategy._generate_opening_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].action == "buy"
        assert orders[0].side == "yes"
        assert orders[0].price_cents == 48  # At best bid
        
    def test_closing_orders_always_generated_regardless_of_spread(self):
        """Closing orders should always be generated, even with tight spread."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=49,
            yes_ask=50,  # 1 cent spread (below min)
            no_bid=50,
            no_ask=51
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,  # Have position
            position_limit=10,
            side="yes",
            avg_entry_price_yes=45.0  # Profitable position (5¢ edge)
        )
        
        # Should generate closing orders even with tight spread
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].action == "sell"
        assert orders[0].side == "yes"
        # With 5¢ edge, should undercut ask by 1¢ for faster fill
        assert orders[0].price_cents == 49  # Ask (50) - 1
        assert orders[0].size == 5
        
    def test_aggressive_exit_ignores_spread_check(self):
        """Aggressive exit orders should be generated even with 0 spread."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=50,
            yes_ask=50,  # 0 cent spread!
            no_bid=50,
            no_ask=50
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,  # Have position
            position_limit=10,
            side="yes",
            avg_entry_price_yes=70.0,  # Way underwater
            unrealized_pnl_cents=-100,
            position_return_pct=-28.5  # Down >25%, triggers aggressive exit
        )
        
        # Should generate aggressive exit even with 0 spread
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].action == "sell"
        assert orders[0].intent_type == "aggressive_exit"
        assert orders[0].price_cents == 50  # At bid (aggressive)


class TestOrderSyncWithMultipleOrders:
    """Test order sync with multiple orders on both sides."""
    
    def setup_method(self):
        """Set up order manager for tests."""
        self.order_manager = OrderManager()
        self.ticker = "TEST"
        
    def test_sync_with_buy_and_sell_orders(self):
        """Should handle both buy and sell orders correctly."""
        # Add existing orders
        now = datetime.now(timezone.utc)
        past = now - timedelta(seconds=2.0)
        
        buy_order = ActiveOrder(
            order_id="buy1",
            ticker=self.ticker,
            side="yes",
            price_cents=48,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=past,
            remaining_size=10,
            last_modified_at=past
        )
        
        sell_order = ActiveOrder(
            order_id="sell1",
            ticker=self.ticker,
            side="yes",
            price_cents=52,
            size=5,
            action="sell",
            intent_type="exit",
            placed_at=past,
            remaining_size=5,
            last_modified_at=past
        )
        
        self.order_manager.state.active_orders[self.ticker] = [buy_order, sell_order]
        
        # Targets: keep buy, move sell
        target_intents = [
            OrderIntent(
                ticker=self.ticker,
                side="yes",
                price_cents=48,  # Same as existing buy
                size=10,
                action="buy",
                intent_type="market_making"
            ),
            OrderIntent(
                ticker=self.ticker,
                side="yes",
                price_cents=54,  # Move sell by 2 cents
                size=5,
                action="sell",
                intent_type="exit"
            )
        ]
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=target_intents,
            min_price_delta_cents=1
        )
        
        # Buy order should be kept (exact match)
        # Sell order should be replaced (price moved 2 cents)
        assert len(actions['keep']) == 1
        assert len(actions['cancel']) == 1
        assert len(actions['add']) == 1
        assert actions['keep'][0].action == "buy"
        assert actions['cancel'][0].action == "sell"
        assert actions['add'][0].action == "sell"
        assert actions['add'][0].price_cents == 54


class TestStaleOrderCancellation:
    """Test that stale orders are cancelled regardless of throttling."""
    
    def setup_method(self):
        """Set up order manager for tests."""
        self.order_manager = OrderManager()
        self.ticker = "TEST"
        
    def test_stale_order_by_age_is_cancelled(self):
        """Order older than max_age should be cancelled even if price unchanged."""
        # Add existing order placed long ago
        now = datetime.now(timezone.utc)
        very_old = now - timedelta(seconds=65)  # Older than 60s limit
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=very_old,
            remaining_size=10,
            last_modified_at=very_old
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target is same price (no change)
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        # Market state for staleness check
        from market_types import MarketState
        market_state = MarketState(
            ticker=self.ticker,
            yes_bid=50,
            yes_ask=52,
            no_bid=48,
            no_ask=50
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1,
            market_state=market_state
        )
        
        # Should cancel stale order and add new one
        assert len(actions['cancel']) == 1
        assert len(actions['add']) == 1
        assert actions['cancel'][0].order_id == "order1"
        
    def test_stale_order_by_price_is_cancelled(self):
        """Order with price far from market should be cancelled."""
        # Add existing order with stale price
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=2)
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,  # Buy at 50
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=recent,
            remaining_size=10,
            last_modified_at=recent
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target at better price
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=44,  # Market moved down
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        # Market state shows our order is WAY above market (stale)
        from market_types import MarketState
        market_state = MarketState(
            ticker=self.ticker,
            yes_bid=44,
            yes_ask=38,  # Ask is 12 cents below our bid! (> 5 cent deviation)
            no_bid=56,
            no_ask=62
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1,
            market_state=market_state
        )
        
        # Should cancel stale order (price too far from market)
        assert len(actions['cancel']) == 1
        assert len(actions['add']) == 1
        
    def test_non_stale_order_follows_normal_throttling(self):
        """Recent order with good price should follow normal throttling rules."""
        # Add existing order placed recently
        now = datetime.now(timezone.utc)
        recent = now - timedelta(seconds=2)  # Only 2s old (< 60s limit)
        
        existing_order = ActiveOrder(
            order_id="order1",
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making",
            placed_at=recent,
            remaining_size=10,
            last_modified_at=recent
        )
        self.order_manager.state.active_orders[self.ticker] = [existing_order]
        
        # Target is same price
        target_intent = OrderIntent(
            ticker=self.ticker,
            side="yes",
            price_cents=50,
            size=10,
            action="buy",
            intent_type="market_making"
        )
        
        # Market state is normal
        from market_types import MarketState
        market_state = MarketState(
            ticker=self.ticker,
            yes_bid=50,
            yes_ask=52,
            no_bid=48,
            no_ask=50
        )
        
        actions = self.order_manager.sync_target_orders(
            ticker=self.ticker,
            target_orders=[target_intent],
            min_price_delta_cents=1,
            market_state=market_state
        )
        
        # Should keep order (not stale, exact match)
        assert len(actions['keep']) == 1
        assert len(actions['cancel']) == 0
        assert len(actions['add']) == 0


class TestIntegrationAcadiaStrategy:
    """Integration tests for full strategy flow."""
    
    def setup_method(self):
        """Set up strategy for tests."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="both",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0
        )
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
        
        # Initialize strategy
        self.strategy.initialize(
            current_positions={},
            outstanding_orders={}
        )
        
    def test_no_position_wide_spread_generates_opening_orders(self):
        """With no position and wide spread, should generate opening orders."""
        # Update market state
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,  # 4 cent spread
            no_bid=48,
            no_ask=52
        )
        
        # Generate orders
        actions = self.strategy.generate_orders("TEST")
        
        # Should add opening orders on both sides (side="both")
        assert len(actions['add']) == 2  # Buy YES and buy NO
        assert len(actions['cancel']) == 0
        assert len(actions['keep']) == 0
        
    def test_no_position_tight_spread_no_orders(self):
        """With no position and tight spread, should not generate orders."""
        # Update market state
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=49,
            yes_ask=50,  # 1 cent spread (below min)
            no_bid=50,
            no_ask=51
        )
        
        # Generate orders
        actions = self.strategy.generate_orders("TEST")
        
        # Should not add any orders (spread too tight)
        assert len(actions['add']) == 0
        assert len(actions['cancel']) == 0
        assert len(actions['keep']) == 0
        
    def test_with_position_generates_both_opening_and_closing(self):
        """With partial position and wide spread, should generate both types."""
        # Set position
        self.strategy.position_states["TEST"].position = 3
        self.strategy.position_states["TEST"].avg_entry_price_yes = 45.0
        
        # Update market state
        self.strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,  # 4 cent spread
            no_bid=48,
            no_ask=52
        )
        
        # Generate orders
        actions = self.strategy.generate_orders("TEST")
        
        # Should add both opening (buy more) and closing (sell existing)
        assert len(actions['add']) >= 2  # At least opening and closing
        
        # Check that we have both buy and sell actions
        add_actions = [order.action for order in actions['add']]
        assert "buy" in add_actions  # Opening orders
        assert "sell" in add_actions  # Closing orders


class TestPositionLimitWithOutstandingOrders:
    """Test that position limits are enforced when accounting for outstanding orders."""
    
    def test_sync_prevents_duplicate_orders_at_limit(self):
        """Order sync should prevent placing duplicate orders when at position limit."""
        market_config = MarketConfig(
            ticker="TEST",
            side="yes",
            position_limit=5,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0
        )
        
        strategy = AcadiaStrategy([market_config], dry_run=True)
        
        # Initialize market state with good spread
        strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        # Initialize position state - no position yet
        strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=0,
            position_limit=5,
            side="yes"
        )
        
        # First order generation - should create order for full capacity (5)
        actions = strategy.generate_orders("TEST")
        assert len(actions['add']) == 1
        assert actions['add'][0].size == 5
        assert actions['add'][0].action == "buy"
        assert actions['add'][0].side == "yes"
        
        # Simulate placing that order (add to tracking)
        strategy.order_manager.add_order("order_1", actions['add'][0])
        
        # Second order generation - should NOT create another order
        # because we already have 5 contracts worth of orders outstanding
        actions = strategy.generate_orders("TEST")
        assert len(actions['add']) == 0  # No new orders
        assert len(actions['keep']) == 1  # Keep existing order
        
    def test_partial_outstanding_orders_reduce_capacity(self):
        """With partial outstanding orders, remaining capacity should be reduced."""
        market_config = MarketConfig(
            ticker="TEST",
            side="yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0
        )
        
        strategy = AcadiaStrategy([market_config], dry_run=True)
        
        # Initialize market state
        strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        # Initialize position: 2 contracts, limit 10
        strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=2,
            position_limit=10,
            side="yes"
        )
        
        # First order generation - capacity = 10 - 2 = 8
        actions = strategy.generate_orders("TEST")
        assert len(actions['add']) == 1
        assert actions['add'][0].size == 8
        
        # Add order with size 5 to tracking
        intent = OrderIntent(
            ticker="TEST",
            side="yes",
            price_cents=48,
            size=5,
            action="buy",
            intent_type="market_making"
        )
        strategy.order_manager.add_order("order_1", intent)
        
        # Second order generation - capacity = 10 - 2 - 5 = 3
        actions = strategy.generate_orders("TEST")
        
        # Should replace the old order (cancel + add)
        new_orders = [o for o in actions['add'] if o.intent_type == "market_making"]
        if len(new_orders) > 0:
            # If it decided to replace, the new order should be for 3 contracts
            assert new_orders[0].size == 3
    
    def test_mixed_yes_no_orders_calculate_correctly(self):
        """Test that YES and NO orders correctly offset each other in effective position."""
        market_config = MarketConfig(
            ticker="TEST",
            side="both",
            position_limit=5,
            min_spread_cents=2,
            min_price_delta_cents=1,
            min_quote_time_seconds=1.0
        )
        
        strategy = AcadiaStrategy([market_config], dry_run=True)
        
        # Initialize market state
        strategy.market_states["TEST"] = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=52,
            no_bid=48,
            no_ask=52
        )
        
        # Initialize position: 2 YES contracts, limit 5
        strategy.position_states["TEST"] = PositionState(
            ticker="TEST",
            position=2,  # Long 2 YES
            position_limit=5,
            side="both"
        )
        
        # Add outstanding orders: 2 YES buy, 1 NO buy
        # Effective position = 2 + 2 - 1 = 3
        yes_intent = OrderIntent(
            ticker="TEST",
            side="yes",
            price_cents=48,
            size=2,
            action="buy",
            intent_type="market_making"
        )
        no_intent = OrderIntent(
            ticker="TEST",
            side="no",
            price_cents=48,
            size=1,
            action="buy",
            intent_type="market_making"
        )
        strategy.order_manager.add_order("order_yes", yes_intent)
        strategy.order_manager.add_order("order_no", no_intent)
        
        # Generate orders - effective position = 3, so remaining capacity = 2
        actions = strategy.generate_orders("TEST")
        
        # Total size of new market making orders should not exceed remaining capacity
        mm_orders = [o for o in actions['add'] if o.intent_type == "market_making"]
        total_yes_size = sum(o.size for o in mm_orders if o.side == "yes" and o.action == "buy")
        total_no_size = sum(o.size for o in mm_orders if o.side == "no" and o.action == "buy")
        
        # Effective position should not exceed limit
        # Current (2) + outstanding YES (2) - outstanding NO (1) + new YES - new NO <= 5
        effective_if_filled = 2 + 2 - 1 + total_yes_size - total_no_size
        assert effective_if_filled <= 5, f"Would exceed position limit: {effective_if_filled} > 5"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

