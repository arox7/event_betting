"""
Kalshi Data Enricher - Functions for enriching positions with market and event data.
"""
import logging
from typing import List, Dict, Any, Optional

from .http_client import KalshiHTTPClient
from .market_functions import get_markets_by_tickers, get_events_by_tickers
from .portfolio_functions import get_all_positions
from .shared_utils import fetch_event_by_ticker

logger = logging.getLogger(__name__)

def get_event_from_market(client: KalshiHTTPClient, market):
    """Get event data from market's event_ticker."""
    try:
        event_ticker = market.event_ticker
        if not event_ticker:
            logger.warning(f"Market {market.ticker} has no event_ticker")
            return None
        
        # Check cache first
        cached_event = client.get_cached('event', event_ticker)
        if cached_event is not None:
            return cached_event
        
        # Fetch event data from API
        event = fetch_event_by_ticker(client, event_ticker)
        if event:
            # Cache the event
            client.set_cache('event', event, event_ticker)
            return event
        else:
            logger.error(f"Failed to fetch event {event_ticker} from API")
            return None
        
    except Exception as e:
        logger.error(f"Failed to get event for market {market.ticker}: {e}")
        return None


def enrich_positions(client: KalshiHTTPClient, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich a list of positions with market and event data.
    
    Args:
        client: KalshiHTTPClient instance
        positions: List of position dictionaries from the API
        
    Returns:
        List of enriched position dictionaries with market and event data
    """
    if not positions:
        return []
    
    # Create a cache key based on the tickers in the positions
    position_tickers = list(set(pos.get('ticker') for pos in positions if pos.get('ticker')))
    tickers_hash = hash(tuple(sorted(position_tickers))) if position_tickers else 0
    cache_key = f"enriched_positions_{tickers_hash}"
    
    cached_enriched = client.get_cached('enriched_positions', cache_key)
    if cached_enriched is not None:
        return cached_enriched
    
    try:
        if not position_tickers:
            return []
        
        # Use batch fetching instead of sequential API calls
        markets_dict = get_markets_by_tickers(client, position_tickers)
        
        # Extract unique event tickers from markets for batch fetching
        event_tickers = []
        market_lookup = {}
        
        for ticker, market in markets_dict.items():
            if market and hasattr(market, 'event_ticker') and market.event_ticker:
                event_tickers.append(market.event_ticker)
                market_lookup[ticker] = {'market': market, 'event': None}  # Will be filled later
            elif market:
                pass  # Market missing event_ticker
        
        # Batch fetch all events
        if event_tickers:
            events_dict = get_events_by_tickers(client, event_tickers)
            
            # Map events back to markets
            for ticker, market_info in market_lookup.items():
                market = market_info['market']
                if market and hasattr(market, 'event_ticker'):
                    event = events_dict.get(market.event_ticker)
                    if event:
                        market_lookup[ticker]['event'] = event
                    else:
                        # Remove this market from lookup since we can't get its event
                        del market_lookup[ticker]
        
        # Enrich positions with market data
        enriched_positions = []
        for position in positions:
            ticker = position.get('ticker')
            if not ticker:
                continue
            
            market_info = market_lookup.get(ticker)
            if market_info:
                # Calculate unrealized P&L based on current market price vs cost basis
                market = market_info['market']
                current_position = position['position']
                market_exposure = position['market_exposure']  # Current market value in cents
                total_traded = position['total_traded']  # Cost basis in cents
                
                # Calculate unrealized P&L: current market value - cost basis
                # For short positions, unrealized P&L = cost basis - current market value
                # For closed positions (position == 0), unrealized P&L should be 0
                unrealized_pnl = 0
                if current_position != 0 and total_traded != 0:
                    if current_position > 0:  # Long position
                        unrealized_pnl = market_exposure - total_traded
                    else:  # Short position
                        unrealized_pnl = total_traded - market_exposure
                
                enriched_position = {
                    'position': position,
                    'market': market_info['market'],
                    'event': market_info['event'],
                    'ticker': ticker,
                    'quantity': position['position'],
                    'market_value': position['market_exposure'],  # Kalshi uses market_exposure
                    'total_cost': position['total_traded'],  # Use total_traded as cost basis
                    'unrealized_pnl': unrealized_pnl,  # Calculated unrealized P&L
                    'realized_pnl': position['realized_pnl'],
                    'is_closed': current_position == 0 and total_traded > 0  # Flag for closed positions
                }
                enriched_positions.append(enriched_position)
            else:
                continue  # Skip positions without market/event data
        
        # Cache the enriched positions
        client.set_cache('enriched_positions', enriched_positions, cache_key)
        
        return enriched_positions
        
    except Exception as e:
        logger.error(f"Failed to enrich positions: {e}")
        return []

def get_enriched_positions(client: KalshiHTTPClient, include_closed: bool = True) -> Optional[List[Dict[str, Any]]]:
    """Get positions enriched with market and event data.
    
    Args:
        client: KalshiHTTPClient instance
        include_closed: If True, include both open and closed positions. If False, only open positions.
        
    Returns:
        List of enriched positions, or None if unable to fetch positions
    """
    try:
        # Get positions
        positions_data = get_all_positions(client)
        if not positions_data:
            return None
        
        positions = positions_data.get('all_market_positions', [])
        if not positions:
            return []
        
        # Filter positions based on include_closed parameter
        if include_closed:
            # Include all positions that have trading history (total_traded > 0)
            # This includes both open positions (position != 0) and closed positions (position == 0)
            relevant_positions = [pos for pos in positions if pos.get('total_traded', 0) > 0]
        else:
            # Only include positions with actual holdings (position != 0)
            relevant_positions = [pos for pos in positions if pos.get('position', 0) != 0]
        
        # Use the enrich_positions function
        return enrich_positions(client, relevant_positions)
        
    except Exception as e:
        logger.error(f"Failed to get enriched positions: {e}")
        return None
