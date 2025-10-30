from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime, timezone

@dataclass
class MarketConfig:
    """Configuration for a single market to trade."""
    
    ticker: str
    side: str
    position_limit: int
    
    # Quote management parameters
    min_spread_cents: int = 2  # Only quote when spread >= 2 cents
    min_price_delta_cents: int = 1  # Only requote if price moves >= 1 cent (hysteresis)
    min_quote_time_seconds: float = 1.0  # Don't touch order for at least 1 second (MQT)
    
    # Exit parameters
    exit_edge_threshold_cents: int = 2  # Undercut exit price if edge >= this many cents

@dataclass
class MarketState:
    """Current state of a market for strategy decisions."""
    ticker: str
    yes_bid: Optional[int] = None
    yes_ask: Optional[int] = None
    no_bid: Optional[int] = None
    no_ask: Optional[int] = None
    last_price: Optional[int] = None


@dataclass
class PositionState:
    """Current position state for a market."""
    ticker: str
    position: int  # Net position (positive = long YES, negative = long NO)
    position_limit: int  # Maximum allowed position
    side: str  # "Yes", "No", or "Both"
    
    # Entry price tracking
    avg_entry_price_yes: Optional[float] = None  # Average entry price for YES contracts
    avg_entry_price_no: Optional[float] = None   # Average entry price for NO contracts
    
    # Position return tracking
    unrealized_pnl_cents: Optional[int] = None   # Current unrealized P&L in cents
    position_return_pct: Optional[float] = None  # Position return as percentage


@dataclass
class OrderIntent:
    """Intent to place an order."""
    ticker: str
    side: str  # "yes" or "no"
    price_cents: int
    size: int
    action: str  # "buy" or "sell"
    intent_type: str = "market_making"  # "market_making", "exit", "aggressive_exit"


@dataclass
class ActiveOrder:
    """Track an active order on the exchange."""
    order_id: str
    ticker: str
    side: str  # "yes" or "no"
    price_cents: int
    size: int
    action: str  # "buy" or "sell"
    intent_type: str
    placed_at: datetime
    remaining_size: int
    last_modified_at: Optional[datetime] = None  # Track when order was last modified
    
    def __post_init__(self):
        """Initialize last_modified_at if not provided."""
        if self.last_modified_at is None:
            self.last_modified_at = self.placed_at
    
    def is_stale(self, max_age_seconds: int = 60) -> bool:
        """Check if order is stale based on age."""
        age = (datetime.now(timezone.utc) - self.placed_at).total_seconds()
        return age > max_age_seconds
    
    def can_modify(self, min_quote_time_seconds: float) -> bool:
        """Check if order can be modified based on minimum quote time."""
        if self.last_modified_at is None:
            return True
        time_since_modified = (datetime.now(timezone.utc) - self.last_modified_at).total_seconds()
        return time_since_modified >= min_quote_time_seconds
    
    def is_price_stale(self, current_bid: Optional[int], current_ask: Optional[int], max_price_deviation: int = 5) -> bool:
        """Check if order price is stale based on market movement."""
        if self.action == "buy":
            # For buy orders, check if current ask is much lower than our bid
            if current_ask is not None and self.price_cents > current_ask + max_price_deviation:
                return True
        elif self.action == "sell":
            # For sell orders, check if current bid is much higher than our ask
            if current_bid is not None and self.price_cents < current_bid - max_price_deviation:
                return True
        return False


@dataclass
class OrderManagerState:
    """State for managing orders across all markets."""
    active_orders: Dict[str, List[ActiveOrder]]  # ticker -> list of active orders