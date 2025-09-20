"""
Kalshi API client using the official kalshi-python SDK.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import kalshi_python
import requests
import base64
import time
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from config import Config
from models import Market, Event


logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Kalshi API client using the official kalshi-python SDK."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.client = self._initialize_client()
        self._private_key = None
        self._load_private_key()
        
        # Simple in-memory cache with TTL
        self._cache = {}
        self._cache_ttl = {
            'market': 300,      # 5 minutes for market data
            'balance': 300,      # 1 minute for balance
            'positions': 300,    # 30 seconds for positions
            'events': 300       # 10 minutes for events
        }
        
    def _initialize_client(self):
        """Initialize the official Kalshi client."""
        if not (self.config.KALSHI_API_KEY_ID and self.config.KALSHI_PRIVATE_KEY_PATH):
            logger.warning("No API credentials provided - client will not work for authenticated endpoints")
            return None
            
        try:
            # Configure the official SDK
            configuration = kalshi_python.Configuration(
                host=self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                     else self.config.KALSHI_API_HOST
            )
            
            # Read private key from file
            with open(self.config.KALSHI_PRIVATE_KEY_PATH, 'r') as f:
                private_key = f.read()
            
            configuration.api_key_id = self.config.KALSHI_API_KEY_ID
            configuration.private_key_pem = private_key
            
            return kalshi_python.KalshiClient(configuration)
            
        except Exception as e:
            logger.error(f"Failed to initialize Kalshi client: {e}")
            raise
    
    def _load_private_key(self):
        """Load the private key for raw API authentication."""
        if not self.config.KALSHI_PRIVATE_KEY_PATH:
            logger.warning("No private key path provided - raw API authentication will not work")
            return
            
        try:
            with open(self.config.KALSHI_PRIVATE_KEY_PATH, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(), 
                    password=None, 
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            self._private_key = None
    
    def _get_cache_key(self, cache_type: str, identifier: str = "") -> str:
        """Generate cache key."""
        return f"{cache_type}:{identifier}" if identifier else cache_type
    
    def _is_cache_valid(self, cache_key: str, cache_type: str) -> bool:
        """Check if cached data is still valid."""
        if cache_key not in self._cache:
            return False
        
        cached_time, _ = self._cache[cache_key]
        ttl = self._cache_ttl.get(cache_type, 300)
        return (time.time() - cached_time) < ttl
    
    def _get_cached(self, cache_type: str, identifier: str = ""):
        """Get cached data if valid."""
        cache_key = self._get_cache_key(cache_type, identifier)
        if self._is_cache_valid(cache_key, cache_type):
            _, data = self._cache[cache_key]
            logger.debug(f"Cache hit for {cache_key}")
            return data
        return None
    
    def _set_cache(self, cache_type: str, data: Any, identifier: str = ""):
        """Set cached data."""
        cache_key = self._get_cache_key(cache_type, identifier)
        self._cache[cache_key] = (time.time(), data)
        logger.debug(f"Cache set for {cache_key}")
    
    def clear_cache(self, cache_type: Optional[str] = None):
        """Clear cache entries. If cache_type is None, clear all cache."""
        if cache_type is None:
            self._cache.clear()
            logger.info("Cleared all cache")
        else:
            keys_to_remove = [key for key in self._cache.keys() if key.startswith(f"{cache_type}:")]
            for key in keys_to_remove:
                del self._cache[key]
            logger.info(f"Cleared {len(keys_to_remove)} cache entries for {cache_type}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        stats = {
            'total_entries': len(self._cache),
            'by_type': {},
            'expired_entries': 0
        }
        
        for cache_key in self._cache.keys():
            cache_type = cache_key.split(':')[0]
            stats['by_type'][cache_type] = stats['by_type'].get(cache_type, 0) + 1
            
            # Check if expired
            if not self._is_cache_valid(cache_key, cache_type):
                stats['expired_entries'] += 1
        
        return stats
    
    def _create_signature(self, timestamp: str, method: str, path: str) -> str:
        """Create the request signature for Kalshi API authentication."""
        if not self._private_key:
            raise Exception("Private key not loaded")
            
        message = f"{timestamp}{method}{path}".encode('utf-8')
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode('utf-8')
    
    def _make_authenticated_request(self, method: str, path: str, params: Optional[Dict] = None) -> requests.Response:
        """Make an authenticated request to the Kalshi API using raw HTTP."""
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            raise Exception("API credentials not properly configured")
        
        # Determine base URL
        base_url = (self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                   else self.config.KALSHI_API_HOST)
        
        # Create timestamp
        timestamp = str(int(datetime.now().timestamp() * 1000))
        
        # For signature, we need the full API path
        signature_path = f"/trade-api/v2{path}"
        signature = self._create_signature(timestamp, method, signature_path)
        
        # Set up headers
        headers = {
            'KALSHI-ACCESS-KEY': self.config.KALSHI_API_KEY_ID,
            'KALSHI-ACCESS-SIGNATURE': signature,
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
        
        # Make request - base URL already includes /trade-api/v2
        url = base_url.rstrip('/') + path
        return requests.request(method, url, headers=headers, params=params)
    
    def _get_event_from_market(self, market):
        """Get event data from market's event_ticker."""
        try:
            # For now, create a minimal but complete event from market data
            # In a full implementation, we'd fetch the actual event data
            event = Event(
                event_ticker=market.event_ticker,
                title=market.title,  # Market title contains the event context
                category=None,  # Category is optional
                markets=[],
                series_ticker=market.event_ticker,
                total_volume=getattr(market, 'volume', 0) or 0
            )
            return event
            
        except Exception as e:
            logger.error(f"Failed to create event from market data: {e}")
            return None
    
    def _preprocess_market_data(self, data):
        """Recursively preprocess market data to handle known API inconsistencies."""
        if isinstance(data, dict):
            # Create a copy to avoid modifying the original
            cleaned = data.copy()
            
            # Handle status field - map non-standard values to valid enum values
            status = cleaned.get('status')
            valid_statuses = {'initialized', 'active', 'closed', 'settled', 'determined'}
            if status and status not in valid_statuses:
                logger.info(f"Converting non-standard status '{status}' to 'closed' for ticker: {cleaned.get('ticker', 'unknown')}")
                cleaned['status'] = 'closed'
            
            # Recursively clean nested structures
            for key, value in cleaned.items():
                cleaned[key] = self._preprocess_market_data(value)
            
            return cleaned
        elif isinstance(data, list):
            return [self._preprocess_market_data(item) for item in data]
        else:
            return data
    
    def get_markets(self, limit: int = 100, status: Optional[str] = None) -> List[Market]:
        """Fetch markets from Kalshi API."""
        if not self.client:
            logger.error("Client not initialized")
            return []
            
        try:
            response = self.client.get_markets(limit=limit, status=status)
            markets = []
            
            for market_data in response.markets or []:
                try:
                    # Convert the SDK response to our Market model
                    market_dict = market_data.to_dict()
                    
                    # Preprocess market data to handle known issues
                    cleaned_market_dict = self._preprocess_market_data(market_dict)
                    
                    markets.append(Market.model_validate(cleaned_market_dict, strict=False))
                except Exception as e:
                    ticker = getattr(market_data, 'ticker', 'unknown')
                    logger.warning(f"Skipping invalid market {ticker}: {e}")
                    continue
            
            return markets
            
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []
    
    def get_events(self, limit: int = 100, status: Optional[str] = None, max_events: Optional[int] = None) -> List[Event]:
        """Fetch events from Kalshi API using direct HTTP calls with nested markets."""
        if not self.client:
            logger.error("Client not initialized")
            return []
            
        try:
            import requests
            
            all_events = []
            cursor = None
            
            while True:
                # Build request parameters
                params = {
                    'limit': limit,
                    'with_nested_markets': 'true'
                }
                if status:
                    params['status'] = status
                if cursor:
                    params['cursor'] = cursor
                
                # Make direct API call
                url = f"{self.client.api_client.configuration.host}/events"
                headers = self._get_auth_headers()
                
                response = requests.get(url, headers=headers, params=params)
                
                if response.status_code != 200:
                    logger.error(f"API call failed: {response.status_code} - {response.text}")
                    break
                
                data = response.json()
                
                # Process events from this batch
                for event_raw in data.get('events', []):
                    try:
                        # Preprocess to handle status validation issues
                        cleaned_event_dict = self._preprocess_market_data(event_raw)
                        event = Event.model_validate(cleaned_event_dict, strict=False)
                        all_events.append(event)
                    except Exception as e:
                        event_ticker = event_raw.get('event_ticker', 'unknown')
                        logger.warning(f"Skipping event {event_ticker}: {e}")
                        continue
                
                # Check pagination
                cursor = data.get('cursor')
                if not cursor or (max_events and len(all_events) >= max_events):
                    break
            
            return all_events
            
        except Exception as e:
            logger.error(f"Error fetching events: {e}")
            return []
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for direct API calls."""
        # The Kalshi SDK handles authentication, we can reuse its token
        headers = {}
        if hasattr(self.client.api_client.configuration, 'access_token'):
            headers['Authorization'] = f'Bearer {self.client.api_client.configuration.access_token}'
        return headers
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Market]:
        """Fetch a specific market by ticker."""
        # Check cache first
        cached_market = self._get_cached('market', ticker)
        if cached_market is not None:
            return cached_market
        
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_market(ticker=ticker)
            if response.market:
                market_dict = response.market.to_dict()
                market = Market.model_validate(market_dict)
                # Cache the result
                self._set_cache('market', market, ticker)
                return market
            return None
        except Exception as e:
            logger.error(f"Failed to fetch market {ticker}: {e}")
            return None
    
    def get_market_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch orderbook for a specific market."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_market_orderbook(ticker=ticker)
            if response.orderbook:
                orderbook = response.orderbook
                return {
                    'yes_bid': orderbook.yes_bid,
                    'yes_ask': orderbook.yes_ask,
                    'no_bid': orderbook.no_bid,
                    'no_ask': orderbook.no_ask,
                    'timestamp': datetime.now(timezone.utc)
                }
            return None
        except Exception as e:
            logger.error(f"Failed to fetch orderbook for {ticker}: {e}")
            return None
    
    def get_balance(self) -> Optional[float]:
        """Get account balance in dollars using raw HTTP requests."""
        # Check cache first
        cached_balance = self._get_cached('balance')
        if cached_balance is not None:
            return cached_balance
        
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            logger.error("API credentials not properly configured")
            return None
            
        try:
            # Make authenticated request
            path = "/portfolio/balance"
            response = self._make_authenticated_request("GET", path)
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            balance_cents = data.get('balance', 0)
            balance = balance_cents / 100.0  # Convert cents to dollars
            
            # Cache the result
            self._set_cache('balance', balance)
            return balance
            
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def get_all_positions(self) -> Optional[Dict[str, Any]]:
        """Get all portfolio positions using raw HTTP requests with pagination."""
        # Check cache first
        cached_positions = self._get_cached('positions')
        if cached_positions is not None:
            return cached_positions
        
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            logger.error("API credentials not properly configured")
            return None
            
        try:
            all_market_positions = []
            all_event_positions = []
            cursor = None
            
            while True:
                # Build request parameters
                params = {
                    'limit': 200,
                    'count_up': 1,      # Minimum count up value (YES positions)
                    'count_down': 1     # Minimum count down value (NO positions)
                }
                if cursor:
                    params['cursor'] = cursor
                
                # Make authenticated request
                path = "/portfolio/positions"
                response = self._make_authenticated_request("GET", path, params)
                
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
            active_positions = [pos for pos in all_market_positions if pos.get('position', 0) != 0]
            
            result = {
                'positions': active_positions,  # Only positions with actual holdings
                'market_positions': all_market_positions,  # All market positions from API
                'event_positions': all_event_positions,    # Event-level position data
                'cursor': None  # No cursor since we got everything
            }
            
            # Cache the result
            self._set_cache('positions', result)
            return result
            
        except Exception as e:
            logger.error(f"Failed to get all positions: {e}")
            return None
    
    def get_enriched_positions(self) -> Optional[List[Dict[str, Any]]]:
        """Get positions enriched with market and event data."""
        try:
            # Get positions
            positions_data = self.get_all_positions()
            if not positions_data:
                return None
            
            positions = positions_data.get('positions', [])
            if not positions:
                return []
            
            # Extract unique tickers from positions that have actual holdings (position != 0)
            positions_with_holdings = [pos for pos in positions if pos.get('position', 0) != 0]
            position_tickers = set(pos.get('ticker') for pos in positions_with_holdings if pos.get('ticker'))
            
            if not position_tickers:
                logger.warning("No valid tickers found in positions")
                return []
            
            logger.info(f"Fetching market data for {len(position_tickers)} position tickers")
            
            # Create lookup dictionaries
            market_lookup = {}
            
            # Get market data for each ticker we have positions in
            for ticker in position_tickers:
                try:
                    market = self.get_market_by_ticker(ticker)
                    if market:
                        # Every market MUST have an event - get it from the event_ticker
                        if not hasattr(market, 'event_ticker') or not market.event_ticker:
                            logger.error(f"Market {ticker} missing event_ticker - this should not happen")
                            continue
                        
                        # Get the actual event data - we need to implement this properly
                        event = self._get_event_from_market(market)
                        if not event:
                            logger.error(f"Failed to get event data for market {ticker} - this should not happen")
                            continue
                        
                        market_lookup[ticker] = {
                            'market': market,
                            'event': event
                        }
                    else:
                        logger.warning(f"Could not find market data for ticker: {ticker}")
                except Exception as e:
                    logger.warning(f"Failed to get market data for {ticker}: {e}")
                    continue
            
            # Enrich positions with market data (only those with actual holdings)
            enriched_positions = []
            for position in positions_with_holdings:
                ticker = position.get('ticker')
                if not ticker:
                    continue
                
                market_info = market_lookup.get(ticker)
                if market_info:
                    enriched_position = {
                        'position': position,
                        'market': market_info['market'],
                        'event': market_info['event'],
                        'ticker': ticker,
                        'quantity': position.get('position', 0),
                        'market_value': position.get('market_exposure', 0),  # Kalshi uses market_exposure
                        'total_cost': position.get('total_cost', 0),
                        'unrealized_pnl': 0,  # Calculate this based on current market price vs cost
                        'realized_pnl': position.get('realized_pnl', 0)
                    }
                    enriched_positions.append(enriched_position)
                else:
                    # Position without matching market (might be settled/closed)
                    enriched_position = {
                        'position': position,
                        'market': None,
                        'event': None,
                        'ticker': ticker,
                        'quantity': position.get('position', 0),
                        'market_value': position.get('market_exposure', 0),  # Kalshi uses market_exposure
                        'total_cost': position.get('total_cost', 0),
                        'unrealized_pnl': 0,  # Can't calculate without market data
                        'realized_pnl': position.get('realized_pnl', 0)
                    }
                    enriched_positions.append(enriched_position)
            
            return enriched_positions
            
        except Exception as e:
            logger.error(f"Failed to get enriched positions: {e}")
            return None
    
    def get_settlements(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio settlements."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_settlements(limit=limit, cursor=cursor)
            return {
                'settlements': [settlement.to_dict() for settlement in response.settlements] if response.settlements else [],
                'cursor': response.cursor
            }
        except Exception as e:
            logger.error(f"Failed to get settlements: {e}")
            return None
    
    def get_fills(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio fills (trade history)."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_fills(limit=limit, cursor=cursor)
            return {
                'fills': response.fills or [],
                'cursor': response.cursor
            }
        except Exception as e:
            logger.error(f"Failed to get fills: {e}")
            return None
    
    def get_portfolio_summary(self) -> Optional[Dict[str, Any]]:
        """Get comprehensive portfolio summary including positions and PnL."""
        try:
            # Get cash balance
            cash_balance = self.get_balance()
            if cash_balance is None:
                return None
            
            # Get positions
            positions_data = self.get_all_positions()
            if positions_data is None:
                return None
            
            positions = positions_data.get('positions', [])
            
            # Calculate position values
            total_position_value = 0
            total_unrealized_pnl = 0
            position_count = len(positions)
            
            for position in positions:
                # Position value should use market_exposure (already in cents from API)
                market_exposure_cents = position.get('market_exposure', 0)
                position_value = abs(market_exposure_cents) / 100.0  # Convert cents to dollars
                total_position_value += position_value
                
                # Unrealized PnL (if available in the response)
                unrealized_pnl_cents = position.get('unrealized_pnl', 0)
                total_unrealized_pnl += unrealized_pnl_cents / 100.0
            
            total_balance = cash_balance + total_position_value
            
            return {
                'cash_balance': cash_balance,
                'total_position_value': total_position_value,
                'total_balance': total_balance,
                'unrealized_pnl': total_unrealized_pnl,
                'position_count': position_count,
                'positions': positions
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return None
    
    def get_portfolio_metrics(self) -> Optional[Dict[str, Any]]:
        """Get comprehensive portfolio metrics including cash, positions, and P&L."""
        try:
            # Get cash balance
            cash_balance = self.get_balance()
            if cash_balance is None:
                return None
            
            # Get enriched positions for detailed calculations
            enriched_positions = self.get_enriched_positions()
            if enriched_positions is None:
                return None
            
            # Calculate metrics from enriched positions
            total_positions = len(enriched_positions)
            total_market_value = sum(abs(pos.get('market_value', 0)) for pos in enriched_positions) / 100.0
            total_unrealized_pnl = sum(pos.get('unrealized_pnl', 0) for pos in enriched_positions) / 100.0
            total_realized_pnl = sum(pos.get('realized_pnl', 0) for pos in enriched_positions) / 100.0
            
            # Calculate portfolio totals
            total_portfolio_value = cash_balance + total_market_value
            
            # Calculate win/loss metrics
            winning_positions = len([pos for pos in enriched_positions if pos.get('unrealized_pnl', 0) > 0])
            losing_positions = len([pos for pos in enriched_positions if pos.get('unrealized_pnl', 0) < 0])
            win_rate = (winning_positions / total_positions) * 100 if total_positions > 0 else 0
            portfolio_return = (total_unrealized_pnl / total_market_value) * 100 if total_market_value > 0 else 0
            
            return {
                'cash_balance': cash_balance,
                'total_market_value': total_market_value,
                'total_portfolio_value': total_portfolio_value,
                'total_unrealized_pnl': total_unrealized_pnl,
                'total_realized_pnl': total_realized_pnl,
                'total_positions': total_positions,
                'winning_positions': winning_positions,
                'losing_positions': losing_positions,
                'win_rate': win_rate,
                'portfolio_return': portfolio_return,
                'enriched_positions': enriched_positions
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio metrics: {e}")
            return None

    def get_recent_pnl(self, hours: int = 24) -> Optional[Dict[str, Any]]:
        """Get realized P&L from recent trading activity."""
        try:
            from datetime import datetime, timezone, timedelta
            
            # Get recent fills to calculate realized PnL
            fills_data = self.get_fills(limit=200)
            if fills_data is None:
                return {'realized_pnl': 0, 'trade_count': 0, 'trades': []}
            
            fills = fills_data.get('fills', [])
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            recent_fills = []
            total_realized_pnl = 0
            
            for fill in fills:
                # fill is already a Fill object from the SDK, not a dict
                if not fill.created_time:
                    continue
                    
                try:
                    if fill.created_time >= cutoff_time:
                        recent_fills.append(fill)
                        
                        # Extract fill details - these are already the correct types
                        ticker = fill.ticker
                        side = fill.side  # 'yes' or 'no'
                        count = fill.count or 0
                        price = fill.price or 0  # in cents
                        
                        if not ticker or count == 0:
                            continue
                        
                        # Calculate trade value in dollars
                        trade_value = (count * price) / 100.0
                        
                        # Calculate realized P&L from trading activity
                        # Buying costs money (negative P&L), selling generates revenue (positive P&L)
                        if side == 'yes':
                            # Bought Yes shares - this is a cost
                            total_realized_pnl -= trade_value
                        else:
                            # Selling positions or buying No shares - treat as revenue
                            total_realized_pnl += trade_value
                            
                except Exception as e:
                    logger.warning(f"Failed to parse fill: {e}")
                    continue
            
            # Calculate some basic stats
            trade_volume = sum(((fill.count or 0) * (fill.price or 0)) / 100.0 for fill in recent_fills)
            
            return {
                'realized_pnl': total_realized_pnl,
                'trade_count': len(recent_fills),
                'trade_volume': trade_volume,
                'recent_fills': [fill.to_dict() for fill in recent_fills[:10]]  # Return last 10 fills for display
            }
            
        except Exception as e:
            logger.error(f"Failed to get recent PnL: {e}")
            return {'realized_pnl': 0, 'trade_count': 0, 'trades': []}
    
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            if not self.client:
                return False
            # Try to get balance as a simple health check
            balance = self.get_balance()
            return balance is not None
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
