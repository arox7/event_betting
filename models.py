"""
Data models for the Kalshi market making bot.
"""
import logging
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timezone
from kalshi_python.models.market import Market as KalshiMarket
from kalshi_python.models.event import Event as KalshiEvent
from pydantic import computed_field, field_validator, ValidationError

logger = logging.getLogger(__name__)

def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)

class Market(KalshiMarket):
    """Extended Kalshi Market model with additional helper methods."""
    
    # Additional fields not in the base KalshiMarket class
    open_interest: Optional[int] = None
    liquidity_dollars: Optional[float] = None
    
    @computed_field
    @property
    def spread_percentage(self) -> Optional[float]:
        """Calculate the spread percentage for Yes market."""
        try:
            # Check if yes_bid and yes_ask exist and are not None
            if not hasattr(self, 'yes_bid') or not hasattr(self, 'yes_ask'):
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} missing yes_bid or yes_ask attributes. "
                           f"Available attributes: {[attr for attr in dir(self) if not attr.startswith('_')]}")
                return None
            
            if self.yes_bid is None or self.yes_ask is None:
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} has None values - "
                           f"yes_bid: {self.yes_bid}, yes_ask: {self.yes_ask}, status: {getattr(self, 'status', 'unknown')}")
                return None
            
            # Convert cents to dollars for calculation
            yes_bid_dollars = self.yes_bid / 100.0
            yes_ask_dollars = self.yes_ask / 100.0
            
            if yes_bid_dollars == 0:
                return 0
            
            spread_pct = (yes_ask_dollars - yes_bid_dollars) / yes_bid_dollars
            return spread_pct
        except Exception as e:
            logger.error(f"Error calculating spread percentage for market {getattr(self, 'ticker', 'unknown')}: {e}")
            return None

    @computed_field
    @property
    def spread_cents(self) -> Optional[int]:
        """Calculate the spread cents for Yes market."""
        try:
            # Check if yes_bid and yes_ask exist and are not None
            if not hasattr(self, 'yes_bid') or not hasattr(self, 'yes_ask'):
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} missing yes_bid or yes_ask attributes. "
                           f"Available attributes: {[attr for attr in dir(self) if not attr.startswith('_')]}")
                return None
            
            if self.yes_bid is None or self.yes_ask is None:
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} has None values - "
                           f"yes_bid: {self.yes_bid}, yes_ask: {self.yes_ask}, status: {getattr(self, 'status', 'unknown')}")
                return None
            
            # Values are already in cents
            spread_cents = self.yes_ask - self.yes_bid
            return spread_cents
        except Exception as e:
            logger.error(f"Error calculating spread cents for market {getattr(self, 'ticker', 'unknown')}: {e}")
            return None

    @computed_field
    @property
    def mid_price(self) -> Optional[float]:
        """Calculate the mid price for Yes market."""
        try:
            # Check if yes_bid and yes_ask exist and are not None
            if not hasattr(self, 'yes_bid') or not hasattr(self, 'yes_ask'):
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} missing yes_bid or yes_ask attributes. "
                           f"Available attributes: {[attr for attr in dir(self) if not attr.startswith('_')]}")
                return None
            
            if self.yes_bid is None or self.yes_ask is None:
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} has None values - "
                           f"yes_bid: {self.yes_bid}, yes_ask: {self.yes_ask}, status: {getattr(self, 'status', 'unknown')}")
                return None
            
            # Convert cents to dollars for mid price
            return (self.yes_bid + self.yes_ask) / 200.0
        except Exception as e:
            logger.error(f"Error calculating mid price for market {getattr(self, 'ticker', 'unknown')}: {e}")
            return None
    
    @computed_field
    @property
    def days_to_close(self) -> Optional[int]:
        """Calculate days until close."""
        try:
            if self.close_time is None:
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} has no close_time. "
                           f"Status: {getattr(self, 'status', 'unknown')}, "
                           f"Open time: {getattr(self, 'open_time', 'None')}, "
                           f"Expiration time: {getattr(self, 'expiration_time', 'None')}")
                return None
            
            now = utc_now()
            return (self.close_time - now).days
        except Exception as e:
            logger.error(f"Error calculating days to close for market {getattr(self, 'ticker', 'unknown')}: {e}")
            return None

    @computed_field
    @property
    def days_since_start(self) -> Optional[int]:
        """Calculate days since start."""
        try:
            if self.open_time is None:
                logger.error(f"MARKET_PROPERTY_ERROR: Market {getattr(self, 'ticker', 'unknown')} has no open_time. "
                           f"Status: {getattr(self, 'status', 'unknown')}, "
                           f"Close time: {getattr(self, 'close_time', 'None')}")
                return None
            
            now = utc_now()
            return (now - self.open_time).days
        except Exception as e:
            logger.error(f"Error calculating days since start for market {getattr(self, 'ticker', 'unknown')}: {e}")
            return None
    
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
    max_spread_cents: Optional[int] = None
    min_spread_cents: Optional[int] = None
    min_liquidity: Optional[int] = None
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
        if self.min_liquidity is not None and self.min_liquidity < 0:
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
            logger.warning(f"Markets field is not a list: {type(v)}")
            return []
        
        validated_markets = []
        for market_data in v:
            validated_markets.append(Market.model_validate(market_data, strict=False))
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
    event: Optional[Event] = None
    score: float = 0.0
    reasons: List[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        """Set timestamp if not provided."""
        if not hasattr(self, 'timestamp') or self.timestamp is None:
            self.timestamp = utc_now()
        if self.reasons is None:
            self.reasons = []

