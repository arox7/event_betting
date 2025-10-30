"""
Tests for exit order generation logic.

Tests the _generate_closing_orders method to ensure it handles:
- Profitable exits with good edge (undercut for better fill)
- Small edge exits (take best price)
- Underwater exits (minimize loss)
- Aggressive exits (cross spread when down >25%)
- Edge cases (no entry price, missing market data)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime, timezone
from market_making.market_types import MarketConfig, MarketState, PositionState
from market_making.acadia import AcadiaStrategy


class TestYESExitLogic:
    """Test exit logic for long YES positions."""
    
    def setup_method(self):
        """Set up strategy for tests."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            exit_edge_threshold_cents=2
        )
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
    
    def test_good_edge_undercuts_for_faster_fill(self):
        """With >=2¢ edge, should undercut ask by 1¢ for faster fill."""
        # Entry at 50¢, current ask at 53¢ → edge = 3¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=51,
            yes_ask=53,
            no_bid=47,
            no_ask=49
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].action == "sell"
        assert orders[0].side == "yes"
        assert orders[0].price_cents == 52  # Ask (53) - 1
        assert orders[0].size == 5
        assert orders[0].intent_type == "exit"
    
    def test_small_edge_takes_ask(self):
        """With 0-1¢ edge, should take ask without undercutting."""
        # Entry at 50¢, current ask at 51¢ → edge = 1¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=49,
            yes_ask=51,
            no_bid=49,
            no_ask=51
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 51  # Take the ask
        assert orders[0].intent_type == "exit"
    
    def test_breakeven_takes_ask(self):
        """At breakeven (0¢ edge), should take ask."""
        # Entry at 50¢, current ask at 50¢ → edge = 0¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=50,
            no_bid=50,
            no_ask=52
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 50  # Take breakeven
        assert orders[0].intent_type == "exit"
    
    def test_underwater_takes_ask_to_minimize_loss(self):
        """When underwater, should take ask to minimize loss (don't wait)."""
        # Entry at 50¢, current ask at 48¢ → edge = -2¢ (underwater)
        market_state = MarketState(
            ticker="TEST",
            yes_bid=46,
            yes_ask=48,
            no_bid=52,
            no_ask=54
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0,
            unrealized_pnl_cents=-10,
            position_return_pct=-4.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 48  # Take ask, minimize loss
        assert orders[0].intent_type == "exit"
    
    def test_disaster_crosses_spread(self):
        """When down >25%, should cross spread (sell at bid immediately)."""
        # Down 28% → aggressive exit
        market_state = MarketState(
            ticker="TEST",
            yes_bid=46,
            yes_ask=48,
            no_bid=52,
            no_ask=54
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=60.0,
            unrealized_pnl_cents=-70,
            position_return_pct=-28.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 46  # Cross spread (bid)
        assert orders[0].intent_type == "aggressive_exit"
    
    def test_no_entry_price_uses_ask(self):
        """Without entry price data, should conservatively use ask."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=50,
            no_bid=50,
            no_ask=52
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=None  # No entry data
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 50  # Use ask conservatively
        assert orders[0].intent_type == "exit"
    
    def test_no_position_no_orders(self):
        """With zero position, should generate no exit orders."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=50,
            no_bid=50,
            no_ask=52
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=0,  # No position
            position_limit=10,
            side="yes"
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 0


class TestNOExitLogic:
    """Test exit logic for long NO positions (negative YES position)."""
    
    def setup_method(self):
        """Set up strategy for tests."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="no",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            exit_edge_threshold_cents=2
        )
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
    
    def test_good_edge_undercuts_bid(self):
        """With >=2¢ edge on NO, should undercut bid by 1¢."""
        # Entry NO at 50¢, current NO bid at 53¢ → edge = 3¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=47,
            yes_ask=49,
            no_bid=53,
            no_ask=55
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=-5,  # Negative = long NO
            position_limit=10,
            side="no",
            avg_entry_price_no=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].action == "sell"
        assert orders[0].side == "no"
        assert orders[0].price_cents == 52  # Bid (53) - 1
        assert orders[0].size == 5
        assert orders[0].intent_type == "exit"
    
    def test_small_edge_takes_bid(self):
        """With 0-1¢ edge on NO, should take bid."""
        # Entry NO at 50¢, current NO bid at 51¢ → edge = 1¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=49,
            yes_ask=51,
            no_bid=51,
            no_ask=53
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=-5,
            position_limit=10,
            side="no",
            avg_entry_price_no=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 51  # Take the bid
        assert orders[0].intent_type == "exit"
    
    def test_underwater_takes_bid(self):
        """When NO position underwater, should take bid to minimize loss."""
        # Entry NO at 50¢, current NO bid at 48¢ → edge = -2¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=52,
            yes_ask=54,
            no_bid=48,
            no_ask=50
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=-5,
            position_limit=10,
            side="no",
            avg_entry_price_no=50.0,
            unrealized_pnl_cents=-10,
            position_return_pct=-4.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 48  # Take bid, minimize loss
        assert orders[0].intent_type == "exit"
    
    def test_disaster_crosses_spread_to_ask(self):
        """When NO down >25%, should cross spread (sell NO at ask)."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=52,
            yes_ask=54,
            no_bid=46,
            no_ask=48
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=-5,
            position_limit=10,
            side="no",
            avg_entry_price_no=60.0,
            unrealized_pnl_cents=-70,
            position_return_pct=-28.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 48  # Cross spread (ask)
        assert orders[0].intent_type == "aggressive_exit"
    
    def test_no_entry_price_uses_bid(self):
        """Without NO entry price data, should use bid conservatively."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=48,
            yes_ask=50,
            no_bid=50,
            no_ask=52
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=-5,
            position_limit=10,
            side="no",
            avg_entry_price_no=None  # No entry data
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 50  # Use bid conservatively
        assert orders[0].intent_type == "exit"


class TestEdgeCases:
    """Test edge cases in exit logic."""
    
    def setup_method(self):
        """Set up strategy for tests."""
        self.market_config = MarketConfig(
            ticker="TEST",
            side="yes",
            position_limit=10,
            min_spread_cents=2,
            min_price_delta_cents=1,
            exit_edge_threshold_cents=2
        )
        self.strategy = AcadiaStrategy([self.market_config], dry_run=True)
    
    def test_missing_market_data_no_orders(self):
        """When market data is missing, should not generate orders."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=None,  # Missing
            yes_ask=None,  # Missing
            no_bid=50,
            no_ask=52
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 0
    
    def test_exact_2_cent_edge_undercuts(self):
        """At exactly 2¢ edge, should undercut (boundary test)."""
        # Entry at 50¢, ask at 52¢ → edge = exactly 2¢
        market_state = MarketState(
            ticker="TEST",
            yes_bid=50,
            yes_ask=52,
            no_bid=48,
            no_ask=50
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].price_cents == 51  # Undercut (52 - 1)
    
    def test_exact_threshold_25_percent_down(self):
        """At exactly -25% should NOT be aggressive (boundary test)."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=46,
            yes_ask=48,
            no_bid=52,
            no_ask=54
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0,
            unrealized_pnl_cents=-62,
            position_return_pct=-25.0  # Exactly -25%
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        # Should NOT be aggressive (threshold is < -25%, not <=)
        assert orders[0].intent_type == "exit"
        assert orders[0].price_cents == 48  # Take ask (underwater logic)
    
    def test_just_over_25_percent_is_aggressive(self):
        """At -25.1% should be aggressive."""
        market_state = MarketState(
            ticker="TEST",
            yes_bid=46,
            yes_ask=48,
            no_bid=52,
            no_ask=54
        )
        
        position_state = PositionState(
            ticker="TEST",
            position=5,
            position_limit=10,
            side="yes",
            avg_entry_price_yes=50.0,
            unrealized_pnl_cents=-63,
            position_return_pct=-25.1  # Just over threshold
        )
        
        orders = self.strategy._generate_closing_orders(
            market_state, self.market_config, position_state
        )
        
        assert len(orders) == 1
        assert orders[0].intent_type == "aggressive_exit"
        assert orders[0].price_cents == 46  # Cross spread (bid)

