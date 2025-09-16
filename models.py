"""
Data models for the Kalshi market making bot.
"""
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime
from enum import Enum

class MarketStatus(Enum):
    """Market status enumeration."""
    OPEN = "open"
    CLOSED = "closed"
    SUSPENDED = "suspended"

@dataclass
class Market:
    """Represents a Kalshi market."""
    ticker: str
    title: str
    description: str
    status: MarketStatus
    volume: int
    open_interest: int
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]
    expiry_date: datetime
    settlement_date: datetime
    min_tick_size: float
    max_order_size: int
    
    @property
    def spread_percentage(self) -> Optional[float]:
        """Calculate the spread percentage for Yes market."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        if self.yes_bid == 0:
            return None
        return (self.yes_ask - self.yes_bid) / self.yes_bid
    
    @property
    def mid_price(self) -> Optional[float]:
        """Calculate the mid price for Yes market."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2
    
    @property
    def days_to_expiry(self) -> int:
        """Calculate days until expiry."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return (self.expiry_date - now).days

@dataclass
class ScreeningCriteria:
    """Criteria for screening profitable markets."""
    min_volume: int
    max_spread_percentage: float
    min_liquidity: int
    max_time_to_expiry_days: int
    min_open_interest: int
    categories: Optional[List[str]] = None
    
    def __post_init__(self):
        """Validate criteria after initialization."""
        if self.min_volume < 0:
            raise ValueError("Minimum volume must be non-negative")
        if self.max_spread_percentage < 0 or self.max_spread_percentage > 1:
            raise ValueError("Max spread percentage must be between 0 and 1")
        if self.min_liquidity < 0:
            raise ValueError("Minimum liquidity must be non-negative")

@dataclass
class Event:
    """Represents a Kalshi event containing multiple markets."""
    ticker: str
    title: str
    description: str
    category: str
    series_ticker: str
    markets: List[Market]
    open_date: datetime
    close_date: datetime
    
    @property
    def open_markets(self) -> List[Market]:
        """Get only open markets from this event."""
        return [market for market in self.markets if market.status == MarketStatus.OPEN]
    
    @property
    def total_volume(self) -> int:
        """Calculate total volume across all markets in this event."""
        return sum(market.volume for market in self.markets)
    
    @property
    def total_open_interest(self) -> int:
        """Calculate total open interest across all markets in this event."""
        return sum(market.open_interest for market in self.markets)
    

@dataclass
class ScreeningResult:
    """Result of market screening."""
    market: Market
    event: Optional[Event] = None
    score: float = 0.0
    reasons: List[str] = None
    is_profitable: bool = False
    timestamp: datetime = None
    
    def __post_init__(self):
        """Set timestamp if not provided."""
        if not hasattr(self, 'timestamp') or self.timestamp is None:
            from datetime import timezone
            self.timestamp = datetime.now(timezone.utc)
        if self.reasons is None:
            self.reasons = []

