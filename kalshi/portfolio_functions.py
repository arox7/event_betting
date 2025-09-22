"""
Kalshi Portfolio Functions - Portfolio, positions, balance, fills, and settlements operations.
"""
import logging
import time
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

from .http_client import KalshiHTTPClient
from .shared_utils import create_sdk_client

logger = logging.getLogger(__name__)

def _make_request_with_retry(client: KalshiHTTPClient, method: str, path: str, params: Optional[Dict] = None, max_retries: int = 3) -> Optional[Any]:
    """Make a request with exponential backoff retry logic for rate limiting."""
    for attempt in range(max_retries + 1):
        try:
            response = client.make_authenticated_request(method, path, params)
            
            # If we get a 429 (rate limited), retry with exponential backoff
            if response.status_code == 429:
                if attempt < max_retries:
                    delay = (2 ** attempt) + (0.1 * attempt)  # Exponential backoff: 1s, 2.1s, 4.2s
                    logger.warning(f"Rate limited (429), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Rate limited (429) after {max_retries + 1} attempts")
                    return None
            
            # For other errors, return immediately
            return response
            
        except Exception as e:
            if attempt < max_retries:
                delay = (2 ** attempt) + (0.1 * attempt)
                logger.warning(f"Request failed: {e}, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(delay)
                continue
            else:
                logger.error(f"Request failed after {max_retries + 1} attempts: {e}")
                return None
    
    return None

def get_balance_dollars(client: KalshiHTTPClient) -> Optional[float]:
    """Get account balance in dollars using raw HTTP requests."""
    # Check cache first
    cached_balance = client.get_cached('balance')
    if cached_balance is not None:
        return cached_balance
        
    try:
        # Make authenticated request with retry logic
        path = "/portfolio/balance"
        response = _make_request_with_retry(client, "GET", path)
        
        if response is None:
            logger.error("Failed to get balance after retries")
            return None
        
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

def get_active_positions_only(client: KalshiHTTPClient) -> Optional[Dict[str, Any]]:
    """Get active positions and resting orders using optimized API parameters.
    
    Returns positions that have:
    - Non-zero position values (actual holdings)
    - Resting orders (pending orders)
    
    This is optimized for market making bots that need to track both
    current positions and pending orders to avoid conflicts.
    """
    # Check cache first
    cached_positions = client.get_cached('active_positions')
    if cached_positions is not None:
        return cached_positions
        
    try:
        all_market_positions = []
        cursor = None
        request_count = 0
        max_requests = 10  # Even more conservative for active positions only
        
        while request_count < max_requests:
            # Optimized parameters for active positions and resting orders
            params = {
                'limit': 1000,  # Maximum allowed by API
                'settlement_status': 'unsettled',  # Only unsettled positions (active markets)
                'count_filter': "position,resting_order_count"  # Positions with holdings OR resting orders
            }
            if cursor:
                params['cursor'] = cursor
            
            # Make authenticated request with retry logic
            path = "/portfolio/positions"
            response = _make_request_with_retry(client, "GET", path, params)
            
            if response is None:
                logger.error("Failed to get active positions after retries")
                return None
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            market_positions = data.get('market_positions', [])
            
            # Add to our collections
            all_market_positions.extend(market_positions)
            
            # Check if there are more pages
            cursor = data.get('cursor')
            if not cursor:
                break
                
            request_count += 1
            
            # Add delay between requests to avoid rate limiting
            if cursor:  # Only delay if there are more pages
                time.sleep(1.0)  # Increased to 1 second delay between requests
        
        # Filter for positions with actual holdings OR resting orders
        active_positions = [pos for pos in all_market_positions if 
                           pos.get('position', 0) != 0 or pos.get('resting_orders_count', 0) > 0]
        
        result = {
            'active_positions': active_positions,
            'all_market_positions': all_market_positions,
            'market_positions': all_market_positions,  # Alias for backward compatibility
            'positions': active_positions,  # Alias for backward compatibility
            'cursor': None
        }
        
        # Cache the result
        client.set_cache('active_positions', result)
        return result
        
    except Exception as e:
        logger.error(f"Failed to get active positions: {e}")
        return None

def get_all_positions(client: KalshiHTTPClient) -> Optional[Dict[str, Any]]:
    """Get all portfolio positions using raw HTTP requests with pagination and rate limiting."""
    # Check cache first
    cached_positions = client.get_cached('positions')
    if cached_positions is not None:
        return cached_positions
        
    try:
        all_market_positions = []
        all_event_positions = []
        cursor = None
        request_count = 0
        max_requests = 20  # Reduced limit to be more conservative with rate limiting
        
        while request_count < max_requests:
            # Build request parameters - optimized based on API documentation
            params = {
                'limit': 1000,  # Maximum allowed by API to minimize requests
                'settlement_status': 'all',  # Get both settled and unsettled positions
                'count_filter': "position,total_traded"  # Only positions with activity
            }
            if cursor:
                params['cursor'] = cursor
            
            # Make authenticated request with retry logic
            path = "/portfolio/positions"
            response = _make_request_with_retry(client, "GET", path, params)
            
            if response is None:
                logger.error("Failed to get positions after retries")
                return None
            
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
                
            request_count += 1
            
            # Add delay between requests to avoid rate limiting
            if cursor:  # Only delay if there are more pages
                time.sleep(1.0)  # Increased to 1 second delay between requests
        
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
    """Get settled portfolio positions using raw HTTP requests with pagination and rate limiting."""
    try:
        all_settlements = []
        cursor = None
        request_count = 0
        max_requests = 20  # Reduced limit to be more conservative with rate limiting
        
        while request_count < max_requests:
            # Build request parameters - optimized based on API documentation
            params = {
                'limit': 1000,  # Maximum allowed by API to minimize requests
            }
            if cursor:
                params['cursor'] = cursor
            
            # Make authenticated request with retry logic
            path = "/portfolio/settlements"
            response = _make_request_with_retry(client, "GET", path, params)
            
            if response is None:
                logger.error("Failed to get settlements after retries")
                return None
            
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
                
            request_count += 1
            
            # Add delay between requests to avoid rate limiting
            if cursor:  # Only delay if there are more pages
                time.sleep(1.0)  # Increased to 1 second delay between requests
        
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

def get_recent_pnl(client: KalshiHTTPClient, hours: int = 24) -> Optional[Dict[str, Any]]:
    """Get realized P&L from recent trading activity using position data."""
    try:
        # Get all positions and filter by recent activity
        positions_data = get_all_positions(client)
        if not positions_data:
            return {
                'recent_realized_pnl': 0.0,
                'recent_trading_volume': 0.0,
                'recent_trades_count': 0,
                'hours': hours
            }
        
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(hours=hours)
        
        total_realized_pnl = 0.0
        total_volume = 0.0
        recent_positions = 0
        
        # Check all market positions for recent activity
        for pos in positions_data.get('all_market_positions', []):
            try:
                # Parse last updated timestamp
                last_updated_str = pos['last_updated_ts']
                if last_updated_str.endswith('Z'):
                    last_updated_str = last_updated_str[:-1] + '+00:00'
                
                pos_datetime = datetime.fromisoformat(last_updated_str)
                
                # If position was updated within the time window
                if pos_datetime >= cutoff_time:
                    recent_positions += 1
                    realized_pnl_dollars = float(pos['realized_pnl_dollars'])
                    total_traded_dollars = float(pos['total_traded_dollars'])
                    
                    total_realized_pnl += realized_pnl_dollars
                    total_volume += total_traded_dollars
                    
            except Exception as e:
                logger.warning(f"Error parsing position timestamp: {e}")
                continue
        
        return {
            'recent_realized_pnl': total_realized_pnl,
            'recent_trading_volume': total_volume,
            'recent_trades_count': recent_positions,
            'hours': hours
        }
        
    except Exception as e:
        logger.error(f"Failed to get recent P&L: {e}")
        return None

def calculate_unrealized_pnl(client: KalshiHTTPClient, ticker: str) -> Optional[Dict[str, Any]]:
    """Calculate unrealized P&L for a specific position using position data and current market price."""
    try:
        # Get position data first
        positions_data = get_all_positions(client)
        if not positions_data:
            return None
        
        # Find the position for this ticker
        position = None
        for pos in positions_data.get('active_positions', []):
            if pos.get('ticker') == ticker:
                position = pos
                break
        
        if not position:
            return None
        
        # Get current market price
        from .market_functions import get_market_by_ticker
        market = get_market_by_ticker(client, ticker)
        if not market:
            return None
        
        # Use last price as the most accurate current price, fallback to mid/bid/ask
        # Market is a Market object, so access attributes directly
        current_price = market.last_price_dollars
        
        if current_price is None:
            return None
        
        # Extract position data
        position_size = position['position']  # Current position (positive = YES, negative = NO)
        market_exposure_dollars = float(position['market_exposure_dollars'])  # Total cost basis
        realized_pnl_dollars = float(position['realized_pnl_dollars'])  # Already realized P&L
        fees_paid_dollars = float(position['fees_paid_dollars'])  # Fees paid
        
        # Calculate cost basis per share
        if position_size != 0:
            cost_basis_per_share = market_exposure_dollars / abs(position_size)
        else:
            cost_basis_per_share = 0
        
        # Calculate unrealized P&L
        if position_size > 0:
            # Long YES position
            unrealized_pnl = (current_price - cost_basis_per_share) * position_size
        elif position_size < 0:
            # Short YES position (or long NO position)
            current_price = 1 - current_price
            unrealized_pnl = (current_price - cost_basis_per_share) * abs(position_size)
        else:
            # No position
            unrealized_pnl = 0
        
        # Calculate market value
        market_value = current_price * abs(position_size)
        
        # Calculate unrealized P&L percentage
        if market_exposure_dollars > 0:
            unrealized_pnl_percentage = (unrealized_pnl / market_exposure_dollars) * 100
        else:
            unrealized_pnl_percentage = 0
        
        return {
            'ticker': ticker,
            'position_size': position_size,
            'cost_basis_per_share': cost_basis_per_share,
            'total_cost_basis': market_exposure_dollars,
            'current_price': current_price,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl_dollars,
            'fees_paid': fees_paid_dollars,
            'net_pnl': unrealized_pnl + realized_pnl_dollars - fees_paid_dollars,
            'unrealized_pnl_percentage': unrealized_pnl_percentage,
            'market_value': market_value,
            'position_data': position
        }
        
    except Exception as e:
        logger.error(f"Failed to calculate unrealized P&L for {ticker}: {e}")
        return None

def get_all_unrealized_pnl(client: KalshiHTTPClient) -> Optional[Dict[str, Any]]:
    """Calculate unrealized P&L for all positions."""
    try:
        # Get all active positions
        positions_data = get_all_positions(client)
        if not positions_data:
            return None
        
        active_positions = positions_data.get('active_positions', [])
        
        unrealized_pnl_data = {}
        total_unrealized_pnl = 0.0
        total_market_value = 0.0
        
        for position in active_positions:
            ticker = position.get('ticker')
            if not ticker:
                continue
            
            # Calculate unrealized P&L for this position
            pnl_data = calculate_unrealized_pnl(client, ticker)
            if pnl_data:
                unrealized_pnl_data[ticker] = pnl_data
                total_unrealized_pnl += pnl_data['unrealized_pnl']
                total_market_value += pnl_data['market_value']
        
        return {
            'total_unrealized_pnl': total_unrealized_pnl,
            'total_market_value': total_market_value,
            'positions': unrealized_pnl_data,
            'position_count': len(unrealized_pnl_data)
        }
        
    except Exception as e:
        logger.error(f"Failed to get all unrealized P&L: {e}")
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