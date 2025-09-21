"""
Kalshi API Client - Refactored version using functional modules.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from config import Config, setup_logging
from .http_client import KalshiHTTPClient
from .market_functions import (
    get_markets, get_market_by_ticker, get_markets_by_tickers, 
    get_market_orderbook, get_events, get_events_by_tickers
)
from .portfolio_functions import (
    get_balance_dollars, get_all_positions, get_settled_positions,
    get_fills, get_settlements, filter_market_positions_by_date, get_recent_pnl
)
from .data_enricher import enrich_positions, get_enriched_positions
from .metrics_calculator import calculate_portfolio_metrics, calculate_filtered_portfolio_metrics, get_recent_trading_metrics

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Refactored Kalshi API client using functional modules."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.http_client = KalshiHTTPClient(config)
        
    # Market Functions
    def get_markets(self, limit: int = 100, status: Optional[str] = None) -> List[Any]:
        """Fetch markets from Kalshi API."""
        return get_markets(self.http_client, limit, status)
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Any]:
        """Fetch a specific market by ticker."""
        return get_market_by_ticker(self.http_client, ticker)
    
    def get_markets_by_tickers(self, tickers: List[str]) -> Dict[str, Any]:
        """Fetch multiple markets by tickers in batch."""
        return get_markets_by_tickers(self.http_client, tickers)
    
    def get_market_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch orderbook for a specific market."""
        return get_market_orderbook(self.http_client, ticker)
    
    def get_events(self, limit: int = 100, status: Optional[str] = None, max_events: Optional[int] = None) -> List[Any]:
        """Fetch events from Kalshi API."""
        return get_events(self.http_client, limit, status, max_events)
    
    def get_events_by_tickers(self, event_tickers: List[str]) -> Dict[str, Any]:
        """Fetch multiple events by tickers in batch."""
        return get_events_by_tickers(self.http_client, event_tickers)
    
    # Portfolio Functions
    def get_balance_dollars(self) -> Optional[float]:
        """Get account balance in dollars."""
        return get_balance_dollars(self.http_client)
    
    def get_balance(self) -> Optional[float]:
        """Backward compatibility alias for get_balance_dollars()."""
        return self.get_balance_dollars()
    
    def get_all_positions(self) -> Optional[Dict[str, Any]]:
        """Get all portfolio positions."""
        return get_all_positions(self.http_client)
    
    def get_settled_positions(self) -> Optional[Dict[str, Any]]:
        """Get settled portfolio positions."""
        return get_settled_positions(self.http_client)
    
    def get_fills(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio fills (trade history)."""
        return get_fills(self.http_client, limit, cursor)
    
    def get_settlements(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio settlements."""
        return get_settlements(self.http_client, limit, cursor)
    
    def filter_market_positions_by_date(self, market_positions: List[Dict[str, Any]], start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Filter market positions by date range."""
        return filter_market_positions_by_date(market_positions, start_date, end_date)
    
    def get_recent_pnl(self, hours: int = 24) -> Optional[Dict[str, Any]]:
        """Get realized P&L from recent trading activity."""
        return get_recent_pnl(self.http_client, hours)
    
    # Data Enrichment Functions
    def enrich_positions(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich a list of positions with market and event data."""
        return enrich_positions(self.http_client, positions)
    
    def get_enriched_positions(self, include_closed: bool = True) -> Optional[List[Dict[str, Any]]]:
        """Get positions enriched with market and event data."""
        return get_enriched_positions(self.http_client, include_closed)
    
    # Portfolio Metrics Functions
    def get_portfolio_metrics(self) -> Optional[Dict[str, Any]]:
        """Get comprehensive portfolio metrics."""
        return calculate_portfolio_metrics(self.http_client)
    
    def get_filtered_portfolio_metrics(self, start_date=None, end_date=None) -> Optional[Dict[str, Any]]:
        """Get portfolio metrics filtered by date range."""
        return calculate_filtered_portfolio_metrics(self.http_client, start_date, end_date)
    
    # Cache Management
    def clear_cache(self, cache_type: Optional[str] = None):
        """Clear cache entries."""
        self.http_client.clear_cache(cache_type)
    
    def invalidate_positions_cache(self):
        """Invalidate positions-related cache when positions change."""
        self.http_client.invalidate_positions_cache()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        return self.http_client.get_cache_stats()
    
    # Health Check
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        return self.http_client.health_check()
