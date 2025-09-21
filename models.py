"""
Data models for the Kalshi market making bot.
"""
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from kalshi_python.models.market import Market as KalshiMarket
from kalshi_python.models.event import Event as KalshiEvent
from pydantic import BaseModel, computed_field, field_validator, ValidationError

logger = logging.getLogger(__name__)

def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)

class Market(KalshiMarket):
    """Extended Kalshi Market model with additional helper methods."""
    
    # Additional fields not in the base KalshiMarket class
    open_interest: Optional[int] = None
    liquidity_dollars: Optional[float] = None  # Liquidity in dollars
    yes_sub_title: Optional[str] = None
    no_sub_title: Optional[str] = None
    category: Optional[str] = None
    settlement_value_dollars: Optional[float] = None  # Settlement value in dollars
    
    
    
    @computed_field
    @property
    def spread_percentage(self) -> Optional[float]:
        """Calculate the spread percentage for Yes market."""
        if self.yes_bid is None or self.yes_ask is None or self.yes_bid == 0:
            return None
        
        # Convert cents to dollars for calculation
        yes_bid_dollars = self.yes_bid / 100.0
        yes_ask_dollars = self.yes_ask / 100.0
        
        spread_pct = (yes_ask_dollars - yes_bid_dollars) / yes_bid_dollars
        return spread_pct

    @computed_field
    @property
    def spread_cents(self) -> Optional[int]:
        """Calculate the spread in cents for Yes market."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        
        # Values are already in cents
        spread_cents = self.yes_ask - self.yes_bid
        return spread_cents

    @computed_field
    @property
    def spread_dollars(self) -> Optional[float]:
        """Calculate the spread in dollars for Yes market."""
        spread_cents = self.spread_cents
        return spread_cents / 100.0 if spread_cents is not None else None

    @computed_field
    @property
    def mid_price_cents(self) -> Optional[int]:
        """Calculate the mid price in cents for Yes market."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        
        return (self.yes_bid + self.yes_ask) // 2

    @computed_field
    @property
    def mid_price_dollars(self) -> Optional[float]:
        """Calculate the mid price in dollars for Yes market."""
        mid_cents = self.mid_price_cents
        return mid_cents / 100.0 if mid_cents is not None else None

    @computed_field
    @property
    def yes_bid_dollars(self) -> Optional[float]:
        """Convert yes bid from cents to dollars."""
        return self.yes_bid / 100.0 if self.yes_bid is not None else None

    @computed_field
    @property
    def yes_ask_dollars(self) -> Optional[float]:
        """Convert yes ask from cents to dollars."""
        return self.yes_ask / 100.0 if self.yes_ask is not None else None

    @computed_field
    @property
    def no_bid_dollars(self) -> Optional[float]:
        """Convert no bid from cents to dollars."""
        return self.no_bid / 100.0 if self.no_bid is not None else None

    @computed_field
    @property
    def no_ask_dollars(self) -> Optional[float]:
        """Convert no ask from cents to dollars."""
        return self.no_ask / 100.0 if self.no_ask is not None else None

    @computed_field
    @property
    def last_price_dollars(self) -> Optional[float]:
        """Convert last price from cents to dollars."""
        return self.last_price / 100.0 if self.last_price is not None else None

    @computed_field
    @property
    def mid_price(self) -> Optional[float]:
        """Calculate the mid price for Yes market in dollars."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        
        # Convert cents to dollars for mid price
        return (self.yes_bid + self.yes_ask) / 200.0
    
    @computed_field
    @property
    def days_to_close(self) -> Optional[int]:
        """Calculate days until close."""
        if self.close_time is None:
            return None
        
        now = utc_now()
        return (self.close_time - now).days

    @computed_field
    @property
    def days_since_start(self) -> Optional[int]:
        """Calculate days since start."""
        if self.open_time is None:
            return None
        
        now = utc_now()
        return (now - self.open_time).days
    
    @computed_field
    @property
    def close_date(self) -> Optional[datetime]:
        """Alias for close_time for backward compatibility."""
        return self.close_time
    
    @computed_field
    @property
    def settlement_date(self) -> Optional[datetime]:
        """Alias for close_time for backward compatibility."""
        return self.close_time
    
    @computed_field
    @property
    def description(self) -> Optional[str]:
        """Alias for subtitle for backward compatibility."""
        return self.subtitle

@dataclass
class ScreeningCriteria:
    """Criteria for screening profitable markets."""
    min_volume: Optional[int] = None
    min_volume_24h: Optional[int] = None
    max_spread_percentage: Optional[float] = None
    max_spread_cents: Optional[int] = None  # Max spread in cents
    min_spread_cents: Optional[int] = None  # Min spread in cents
    min_liquidity_dollars: Optional[float] = None  # Min liquidity in dollars
    max_time_to_close_days: Optional[int] = None
    min_open_interest: Optional[int] = None
    categories: Optional[List[str]] = None
    
    def __post_init__(self):
        """Validate criteria after initialization."""
        if self.min_volume is not None and self.min_volume < 0:
            raise ValueError("Minimum volume must be non-negative")
        if self.min_volume_24h is not None and self.min_volume_24h < 0:
            raise ValueError("Minimum 24h volume must be non-negative")
        if self.max_spread_percentage is not None and (self.max_spread_percentage < 0 or self.max_spread_percentage > 1):
            raise ValueError("Max spread percentage must be between 0 and 1")
        if self.max_spread_cents is not None and self.max_spread_cents < 0:
            raise ValueError("Max spread cents must be non-negative")
        if self.min_spread_cents is not None and self.min_spread_cents < 0:
            raise ValueError("Min spread cents must be non-negative")
        if self.min_liquidity_dollars is not None and self.min_liquidity_dollars < 0:
            raise ValueError("Minimum liquidity must be non-negative")
        if self.max_time_to_close_days is not None and self.max_time_to_close_days < 0:
            raise ValueError("Max time to close must be non-negative")
        if self.min_open_interest is not None and self.min_open_interest < 0:
            raise ValueError("Minimum open interest must be non-negative")

class Event(KalshiEvent):
    """Extended Kalshi Event model with additional helper methods."""
    
    # Additional fields not in the base KalshiMarket class
    category: Optional[str] = None

    @field_validator('markets', mode='before')
    @classmethod
    def validate_markets(cls, v):
        """Validate and clean markets data before processing."""
        if v is None:
            return []
        
        if not isinstance(v, list):
            logger.debug(f"Markets field is not a list: {type(v)}, returning empty list")
            return []
        
        # If markets list is empty or contains no valid data, return empty list
        if not v:
            return []
        
        validated_markets = []
        for market_data in v:
            try:
                # Preprocess market data to handle known issues
                if isinstance(market_data, dict):
                    market_data = market_data.copy()
                    status = market_data.get('status')
                    valid_statuses = {'initialized', 'active', 'closed', 'settled', 'determined'}
                    if status and status not in valid_statuses:
                        logger.debug(f"Converting non-standard status '{status}' market {market_data.get('ticker', 'unknown')} to 'closed' in event validation")
                        market_data['status'] = 'closed'
                
                # Use strict=False to be more lenient with validation
                validated_markets.append(Market.model_validate(market_data, strict=False))
            except Exception as e:
                market_ticker = market_data.get('ticker', 'unknown') if isinstance(market_data, dict) else 'unknown'
                logger.debug(f"Skipping invalid market {market_ticker} in event: {e}")
                continue
        return validated_markets
    
    @computed_field
    @property
    def total_volume(self) -> int:
        """Calculate total volume across all markets in this event."""
        return sum(market.volume or 0 for market in self.markets)
    
    @computed_field
    @property
    def ticker(self) -> Optional[str]:
        """Alias for event_ticker for backward compatibility."""
        return self.event_ticker
    
    @computed_field
    @property
    def description(self) -> Optional[str]:
        """Alias for subtitle for backward compatibility."""
        return self.sub_title
    

@dataclass
class ScreeningResult:
    """Result of market screening."""
    market: Market
    event: Event
    score: float = 0.0
    reasons: List[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        """Set timestamp if not provided."""
        if not hasattr(self, 'timestamp') or self.timestamp is None:
            self.timestamp = utc_now()
        if self.reasons is None:
            self.reasons = []

class MarketPosition(BaseModel):
    """Market position data from Kalshi API."""
    fees_paid: int                        # Fees paid in cents
    fees_paid_dollars: str                # Fees paid in dollars (as string from API)
    last_updated_ts: str                  # Last updated timestamp
    market_exposure: int                  # Market exposure in cents
    market_exposure_dollars: str          # Market exposure in dollars (as string from API)
    position: int                         # Current position (positive = long, negative = short, 0 = flat)
    realized_pnl: int                     # Realized P&L in cents
    realized_pnl_dollars: str             # Realized P&L in dollars (as string from API)
    resting_orders_count: int             # Number of resting orders
    ticker: str                           # Market ticker
    total_traded: int                     # Total traded in cents
    total_traded_dollars: str             # Total traded in dollars (as string from API)
    
    @computed_field
    @property
    def fees_paid_dollars_float(self) -> float:
        """Convert fees paid from string to float."""
        try:
            return float(self.fees_paid_dollars)
        except (ValueError, TypeError):
            return 0.0
    
    @computed_field
    @property
    def market_exposure_dollars_float(self) -> float:
        """Convert market exposure from string to float."""
        try:
            return float(self.market_exposure_dollars)
        except (ValueError, TypeError):
            return 0.0
    
    @computed_field
    @property
    def realized_pnl_dollars_float(self) -> float:
        """Convert realized P&L from string to float."""
        try:
            return float(self.realized_pnl_dollars)
        except (ValueError, TypeError):
            return 0.0
    
    @computed_field
    @property
    def total_traded_dollars_float(self) -> float:
        """Convert total traded from string to float."""
        try:
            return float(self.total_traded_dollars)
        except (ValueError, TypeError):
            return 0.0
    
    @computed_field
    @property
    def net_realized_pnl_dollars(self) -> float:
        """Calculate net realized P&L after fees in dollars."""
        return self.realized_pnl_dollars_float - self.fees_paid_dollars_float
    
    @computed_field
    @property
    def is_closed_position(self) -> bool:
        """Check if this is a closed position (position = 0 but has trading history)."""
        return self.position == 0 and self.total_traded > 0

