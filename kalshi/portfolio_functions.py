"""
Kalshi Portfolio Functions - Portfolio, positions, balance, fills, and settlements operations.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

from .http_client import KalshiHTTPClient
from .shared_utils import create_sdk_client

logger = logging.getLogger(__name__)

def get_balance_dollars(client: KalshiHTTPClient) -> Optional[float]:
    """Get account balance in dollars using raw HTTP requests."""
    # Check cache first
    cached_balance = client.get_cached('balance')
    if cached_balance is not None:
        return cached_balance
        
    try:
        # Make authenticated request
        path = "/portfolio/balance"
        response = client.make_authenticated_request("GET", path)
        
        if response.status_code != 200:
            logger.error(f"API call failed: {response.status_code} - {response.text}")
            return None
        
        data = response.json()
        balance_cents = data['balance']
        balance_dollars = balance_cents / 100.0  # Convert cents to dollars
        
        # Cache the result
        client.set_cache('balance', balance_dollars)
        return balance_dollars
        
    except Exception as e:
        logger.error(f"Failed to get balance: {e}")
        return None

def get_all_positions(client: KalshiHTTPClient) -> Optional[Dict[str, Any]]:
    """Get all portfolio positions using raw HTTP requests with pagination."""
    # Check cache first
    cached_positions = client.get_cached('positions')
    if cached_positions is not None:
        return cached_positions
        
    try:
        all_market_positions = []
        all_event_positions = []
        cursor = None
        
        while True:
            # Build request parameters
            params = {
                'limit': 200,
                'settlement_status': 'all',
                'count_filter': "position,total_traded,resting_order_count"
            }
            if cursor:
                params['cursor'] = cursor
            
            # Make authenticated request
            path = "/portfolio/positions"
            response = client.make_authenticated_request("GET", path, params)
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            market_positions = data.get('market_positions', [])
            event_positions = data.get('event_positions', [])
            
            # Add to our collections
            all_market_positions.extend(market_positions)
            all_event_positions.extend(event_positions)
            
            # Check if there are more pages
            cursor = data.get('cursor')
            if not cursor:
                break
        
        # Filter client-side for positions with actual position != 0
        # The API count_up/count_down parameters might include resting orders
        active_positions = [pos for pos in all_market_positions if pos['position'] != 0]
        
        result = {
            'active_positions': active_positions,  # Only positions with actual holdings
            'all_market_positions': all_market_positions,  # All market positions from API
            'all_event_positions': all_event_positions,    # Event-level position data
            'positions': active_positions,  # Alias for backward compatibility
            'market_positions': all_market_positions,  # Alias for backward compatibility
            'event_positions': all_event_positions,    # Alias for backward compatibility
            'cursor': None  # No cursor since we got everything
        }
        
        # Cache the result
        client.set_cache('positions', result)
        return result
        
    except Exception as e:
        logger.error(f"Failed to get all positions: {e}")
        return None

def get_settled_positions(client: KalshiHTTPClient) -> Optional[Dict[str, Any]]:
    """Get settled portfolio positions using raw HTTP requests with pagination."""
    try:
        all_settlements = []
        cursor = None
        
        while True:
            # Build request parameters
            params = {
                'limit': 200,
            }
            if cursor:
                params['cursor'] = cursor
            
            # Make authenticated request
            path = "/portfolio/settlements"
            response = client.make_authenticated_request("GET", path, params)
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            settlements = data.get('settlements', [])
            
            # Add to our collections
            all_settlements.extend(settlements)
            
            # Check if there are more pages
            cursor = data.get('cursor')
            if not cursor:
                break
        
        result = {
            'all_settlements': all_settlements,  # All settlements from API
            'cursor': None  # No cursor since we got everything
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get settled positions: {e}")
        return None

def get_fills(client: KalshiHTTPClient, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get portfolio fills (trade history) using the official SDK."""
    try:
        # Use shared SDK client factory
        sdk_client = create_sdk_client(client)
        
        response = sdk_client.get_fills(limit=limit, cursor=cursor)
        return {
            'fills': response.fills or [],
            'cursor': response.cursor
        }
    except Exception as e:
        logger.error(f"Failed to get fills: {e}")
        return None

def get_settlements(client: KalshiHTTPClient, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get portfolio settlements using the official SDK."""
    try:
        # Use shared SDK client factory
        sdk_client = create_sdk_client(client)
        
        response = sdk_client.get_settlements(limit=limit, cursor=cursor)
        return {
            'settlements': [settlement.to_dict() for settlement in response.settlements] if response.settlements else [],
            'cursor': response.cursor
        }
    except Exception as e:
        logger.error(f"Failed to get settlements: {e}")
        return None

def filter_market_positions_by_date(market_positions: List[Dict[str, Any]], start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Filter market positions by date range based on last_updated_ts."""
    if not start_date and not end_date:
        return market_positions
    
    filtered_positions = []
    excluded_count = 0
    
    for pos in market_positions:
        try:
            # Parse the timestamp
            last_updated_str = pos['last_updated_ts']
            if last_updated_str.endswith('Z'):
                last_updated_str = last_updated_str[:-1] + '+00:00'
            
            pos_datetime = datetime.fromisoformat(last_updated_str)
            pos_date = pos_datetime.date()
            
            # Filter by date range (compare dates, not datetime objects)
            include_position = True
            if start_date:
                # Convert start_date to date if it's a datetime
                start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
                if pos_date < start_date_only:
                    include_position = False
                    excluded_count += 1
            if end_date and include_position:
                # Convert end_date to date if it's a datetime
                end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
                if pos_date > end_date_only:
                    include_position = False
                    excluded_count += 1
            
            if include_position:
                filtered_positions.append(pos)
                
        except Exception as e:
            logger.warning(f"Error parsing date for position {pos.get('ticker', 'Unknown')}: {e}")
            excluded_count += 1
            continue
    
    return filtered_positions