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

class MarketCategory(Enum):
    """Market category enumeration."""
    POLITICS = "politics"
    ECONOMICS = "economics"
    SPORTS = "sports"
    ENTERTAINMENT = "entertainment"
    TECHNOLOGY = "technology"
    OTHER = "other"

@dataclass
class Market:
    """Represents a Kalshi market."""
    ticker: str
    title: str
    description: str
    category: MarketCategory
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
        return (self.expiry_date - datetime.now()).days

@dataclass
class ScreeningCriteria:
    """Criteria for screening profitable markets."""
    min_volume: int
    max_spread_percentage: float
    min_liquidity: int
    max_time_to_expiry_days: int
    min_open_interest: int
    categories: List[MarketCategory]
    
    def __post_init__(self):
        """Validate criteria after initialization."""
        if self.min_volume < 0:
            raise ValueError("Minimum volume must be non-negative")
        if self.max_spread_percentage < 0 or self.max_spread_percentage > 1:
            raise ValueError("Max spread percentage must be between 0 and 1")
        if self.min_liquidity < 0:
            raise ValueError("Minimum liquidity must be non-negative")

@dataclass
class ScreeningResult:
    """Result of market screening."""
    market: Market
    score: float
    reasons: List[str]
    is_profitable: bool
    timestamp: datetime
    
    def __post_init__(self):
        """Set timestamp if not provided."""
        if not hasattr(self, 'timestamp') or self.timestamp is None:
            self.timestamp = datetime.now()

