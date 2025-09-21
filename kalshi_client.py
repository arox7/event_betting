"""
Kalshi API client using the official kalshi-python SDK.
"""
import logging
import base64
import time
import concurrent.futures
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from pprint import pprint

import kalshi_python
import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from config import Config, setup_logging
from models import Market, Event, MarketPosition


# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
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
            'market': 600,      # 10 minutes for market data (less volatile)
            'balance': 60,      # 1 minute for balance (more volatile)
            'positions': 120,   # 2 minutes for positions (moderate volatility)
            'events': 1800,     # 30 minutes for events (very stable)
            'enriched_positions': 300  # 5 minutes for enriched positions
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
            return data
        return None
    
    def _set_cache(self, cache_type: str, data: Any, identifier: str = ""):
        """Set cached data."""
        cache_key = self._get_cache_key(cache_type, identifier)
        self._cache[cache_key] = (time.time(), data)
    
    def clear_cache(self, cache_type: Optional[str] = None):
        """Clear cache entries. If cache_type is None, clear all cache."""
        if cache_type is None:
            self._cache.clear()
        else:
            keys_to_remove = [key for key in self._cache.keys() if key.startswith(f"{cache_type}:")]
            for key in keys_to_remove:
                del self._cache[key]
    
    def invalidate_positions_cache(self):
        """Invalidate positions-related cache when positions change."""
        self.clear_cache('positions')
        self.clear_cache('enriched_positions')
    
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
            event_ticker = market.event_ticker
            if not event_ticker:
                logger.warning(f"Market {market.ticker} has no event_ticker")
                return None
            
            # Check cache first
            cached_event = self._get_cached('event', event_ticker)
            if cached_event is not None:
                return cached_event
            
            # Fetch event data from API
            event = self._fetch_event_by_ticker(event_ticker)
            if event:
                # Cache the event
                self._set_cache('event', event, event_ticker)
                return event
            else:
                logger.error(f"Failed to fetch event {event_ticker} from API")
                return None
            
        except Exception as e:
            logger.error(f"Failed to get event for market {market.ticker}: {e}")
            return None
    
    def _fetch_event_by_ticker(self, event_ticker: str) -> Optional[Event]:
        """Fetch a specific event by ticker using direct HTTP requests."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            # Make direct API call to get event
            url = f"{self.client.api_client.configuration.host}/events/{event_ticker}"
            headers = self._get_auth_headers()
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if 'event' in data and data['event']:
                    event_dict = data['event']
                    # Preprocess to handle known issues
                    cleaned_event_dict = self._preprocess_event_data(event_dict)
                    if cleaned_event_dict:
                        event = Event.model_validate(cleaned_event_dict, strict=False)
                        return event
                    else:
                        logger.error(f"Preprocessing returned empty event data for {event_ticker}")
                        return None
                else:
                    logger.error(f"No event data in API response for {event_ticker}: {data}")
                    return None
            elif response.status_code == 404:
                logger.warning(f"Event {event_ticker} not found")
                return None
            else:
                logger.warning(f"Failed to fetch event {event_ticker}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching event {event_ticker}: {e}")
            return None
    
    def get_events_by_tickers(self, event_tickers: List[str]) -> Dict[str, Event]:
        """Fetch multiple events by tickers in batch using concurrent requests."""
        if not event_tickers:
            return {}
        
        if not self.client:
            logger.error("Client not initialized")
            return {}
        
        # Check cache first for all tickers
        cached_events = {}
        uncached_tickers = []
        
        for ticker in event_tickers:
            cached_event = self._get_cached('event', ticker)
            if cached_event is not None:
                cached_events[ticker] = cached_event
            else:
                uncached_tickers.append(ticker)
        
        # If all events are cached, return them
        if not uncached_tickers:
            return cached_events
        
        # Fetch uncached events using concurrent requests
        fetched_events = {}
        
        def fetch_single_event(event_ticker):
            try:
                return self._fetch_event_by_ticker(event_ticker)
            except Exception as e:
                logger.warning(f"Error fetching event {event_ticker}: {e}")
                return None
        
        # Use ThreadPoolExecutor for concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_ticker = {executor.submit(fetch_single_event, ticker): ticker for ticker in uncached_tickers}
            
            for future in concurrent.futures.as_completed(future_to_ticker):
                event_ticker = future_to_ticker[future]
                event = future.result()
                if event:
                    fetched_events[event_ticker] = event
                    # Cache the result
                    self._set_cache('event', event, event_ticker)
        
        # Combine cached and fetched events
        all_events = {**cached_events, **fetched_events}
        
        return all_events
    
    def _preprocess_event_data(self, data, status: Optional[str] = None):
        """Recursively preprocess event data to handle known API inconsistencies."""
        markets = data.get('markets', [])
        cleaned = data.copy()
        markets_to_keep = []
        for market in markets:
            processed_market = self._is_market_valid(market, status)
            if processed_market:
                markets_to_keep.append(market)
        cleaned['markets'] = markets_to_keep
        return cleaned

    def _is_market_valid(self, data, status: Optional[str] = None):
        market_status = data.get('status')
        valid_statuses = {'initialized', 'active', 'closed', 'settled', 'determined'}
        if market_status not in valid_statuses:
            return False

        if status is not None:
            if status == 'open' and market_status not in ["active", "open"]:
                return False
            elif status != 'open' and market_status != status:
                return False
        
        return True

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
                        cleaned_event_dict = self._preprocess_event_data(event_raw, status)
                        if len(cleaned_event_dict) == 0:
                            continue
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
        """Fetch a specific market by ticker using direct HTTP requests."""
        # Check cache first
        cached_market = self._get_cached('market', ticker)
        if cached_market is not None:
            return cached_market
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            # Make direct API call
            url = f"{self.client.api_client.configuration.host}/markets/{ticker}"
            headers = self._get_auth_headers()
            
            response = requests.get(url, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            
            if 'market' in data and data['market']:
                market_dict = data['market']
                # Do this to fit the model schema validation
                if market_dict.get("status") == "finalized":
                    market_dict["status"] = "settled"
                market = Market.model_validate(market_dict, strict=False)
                # Cache the result
                self._set_cache('market', market, ticker)
                return market
            return None
        except Exception as e:
            logger.error(f"Failed to fetch market {ticker}: {e}")
            return None
    
    def get_markets_by_tickers(self, tickers: List[str]) -> Dict[str, Market]:
        """Fetch multiple markets by tickers in batch using direct HTTP requests."""
        if not tickers:
            return {}
        
        if not self.client:
            logger.error("Client not initialized")
            return {}
        
        # Check cache first for all tickers
        cached_markets = {}
        uncached_tickers = []
        
        for ticker in tickers:
            cached_market = self._get_cached('market', ticker)
            if cached_market is not None:
                cached_markets[ticker] = cached_market
            else:
                uncached_tickers.append(ticker)
        
        # If all markets are cached, return them
        if not uncached_tickers:
            return cached_markets
        
        # Fetch uncached markets using batch API call
        fetched_markets = {}
        try:
            # Use the markets endpoint with multiple tickers
            # Note: Kalshi API may not support batch fetching, so we'll use concurrent requests
            
            def fetch_single_market(ticker):
                try:
                    url = f"{self.client.api_client.configuration.host}/markets/{ticker}"
                    headers = self._get_auth_headers()
                    
                    response = requests.get(url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        if 'market' in data and data['market']:
                            market_dict = data['market']
                            if market_dict.get("status") == "finalized":
                                market_dict["status"] = "settled"
                            market = Market.model_validate(market_dict, strict=False)
                            # Cache the result
                            self._set_cache('market', market, ticker)
                            return ticker, market
                    else:
                        logger.warning(f"Failed to fetch market {ticker}: {response.status_code}")
                        return ticker, None
                except Exception as e:
                    logger.warning(f"Error fetching market {ticker}: {e}")
                    return ticker, None
            
            # Use ThreadPoolExecutor for concurrent requests (much faster than sequential)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_ticker = {executor.submit(fetch_single_market, ticker): ticker for ticker in uncached_tickers}
                
                for future in concurrent.futures.as_completed(future_to_ticker):
                    ticker, market = future.result()
                    if market:
                        fetched_markets[ticker] = market
                        
        except Exception as e:
            logger.error(f"Error in batch market fetching: {e}")
            # Fallback to sequential fetching
            for ticker in uncached_tickers:
                market = self.get_market_by_ticker(ticker)
                if market:
                    fetched_markets[ticker] = market
        
        # Combine cached and fetched markets
        all_markets = {**cached_markets, **fetched_markets}
        
        return all_markets
    
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
    
    def get_balance_dollars(self) -> Optional[float]:
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
            balance_cents = data['balance']
            balance_dollars = balance_cents / 100.0  # Convert cents to dollars
            
            # Cache the result
            self._set_cache('balance', balance_dollars)
            return balance_dollars
            
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def get_balance(self) -> Optional[float]:
        """Backward compatibility alias for get_balance_dollars()."""
        return self.get_balance_dollars()
    
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
                    'settlement_status': 'all',
                    'count_filter': "position,total_traded,resting_order_count"
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
            self._set_cache('positions', result)
            return result
            
        except Exception as e:
            logger.error(f"Failed to get all positions: {e}")
            return None
    
    def get_settled_positions(self) -> Optional[Dict[str, Any]]:
        """Get settled portfolio positions using raw HTTP requests with pagination."""
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            logger.error("API credentials not properly configured")
            return None
            
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
                response = self._make_authenticated_request("GET", path, params)
                
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
    
    def enrich_positions(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich a list of positions with market and event data.
        
        Args:
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
        
        cached_enriched = self._get_cached('enriched_positions', cache_key)
        if cached_enriched is not None:
            return cached_enriched
        
        try:
            if not position_tickers:
                return []
            
            # Use batch fetching instead of sequential API calls
            markets_dict = self.get_markets_by_tickers(position_tickers)
            
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
                events_dict = self.get_events_by_tickers(event_tickers)
                
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
            self._set_cache('enriched_positions', enriched_positions, cache_key)
            
            return enriched_positions
            
        except Exception as e:
            logger.error(f"Failed to enrich positions: {e}")
            return []
    
    def get_enriched_positions(self, include_closed: bool = True) -> Optional[List[Dict[str, Any]]]:
        """Get positions enriched with market and event data.
        
        Args:
            include_closed: If True, include both open and closed positions. If False, only open positions.
            
        Returns:
            List of enriched positions, or None if unable to fetch positions
        """
        try:
            # Get positions
            positions_data = self.get_all_positions()
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
            
            # Use the new enrich_positions method
            return self.enrich_positions(relevant_positions)
            
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
    
    def filter_market_positions_by_date(self, market_positions: List[Dict[str, Any]], start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
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

    def get_portfolio_metrics(self) -> Optional[Dict[str, Any]]:
        """Get comprehensive portfolio metrics including cash, positions, and P&L."""
        try:
            # Get cash balance (in dollars)
            cash_balance_dollars = self.get_balance_dollars()
            if cash_balance_dollars is None:
                return None
            
            # Get all positions data (includes both active and market positions)
            all_positions_data = self.get_all_positions()
            if all_positions_data is None:
                return None
            
            # Get enriched positions for detailed calculations (active positions only)
            enriched_positions = self.get_enriched_positions(include_closed=False)
            if enriched_positions is None:
                enriched_positions = []
            
            # Extract market positions (all positions including closed ones)
            all_market_positions = all_positions_data['all_market_positions']
            
            # Calculate metrics from enriched positions (active positions only)
            total_active_positions = len(enriched_positions)
            
            # Market value calculations (convert from cents to dollars)
            total_market_value_dollars = sum(abs(pos['market_value']) for pos in enriched_positions) / 100.0
            total_unrealized_pnl_dollars = sum(pos['unrealized_pnl'] for pos in enriched_positions) / 100.0
            
            # Calculate realized P&L from all market positions, accounting for fees
            total_realized_pnl_cents = 0
            total_fees_paid_cents = 0
            
            for pos in all_market_positions:
                realized_pnl_cents = pos['realized_pnl']  # Already in cents
                fees_paid_cents = pos['fees_paid']  # Already in cents
                total_realized_pnl_cents += realized_pnl_cents
                total_fees_paid_cents += fees_paid_cents
            
            # Net realized P&L after fees (convert to dollars)
            total_realized_pnl_dollars = (total_realized_pnl_cents - total_fees_paid_cents) / 100.0
            total_fees_paid_dollars = total_fees_paid_cents / 100.0
            
            # Calculate portfolio totals
            total_portfolio_value_dollars = cash_balance_dollars + total_market_value_dollars
            
            # Calculate win/loss metrics from active positions
            winning_positions = len([pos for pos in enriched_positions if pos['unrealized_pnl'] > 0])
            losing_positions = len([pos for pos in enriched_positions if pos['unrealized_pnl'] < 0])
            win_rate = (winning_positions / total_active_positions) * 100 if total_active_positions > 0 else 0
            portfolio_return = (total_unrealized_pnl_dollars / total_market_value_dollars) * 100 if total_market_value_dollars > 0 else 0
            
            # Calculate closed positions from all data (client-side filtering will handle date ranges)
            closed_positions = [pos for pos in all_market_positions if pos['position'] == 0 and pos['total_traded'] > 0]
            
            # Don't enrich closed positions by default - only when specifically requested for display
            # This keeps the portfolio metrics calculation fast
            enriched_closed_positions = []
            
            return {
                'cash_balance': cash_balance_dollars,  # In dollars
                'total_market_value': total_market_value_dollars,  # In dollars
                'total_portfolio_value': total_portfolio_value_dollars,  # In dollars
                'total_unrealized_pnl': total_unrealized_pnl_dollars,  # In dollars
                'total_realized_pnl': total_realized_pnl_dollars,  # In dollars (after fees)
                'total_fees_paid': total_fees_paid_dollars,  # In dollars
                'total_positions': total_active_positions,
                'winning_positions': winning_positions,
                'losing_positions': losing_positions,
                'win_rate': win_rate,
                'portfolio_return': portfolio_return,
                'enriched_positions': enriched_positions,  # Active positions only
                'enriched_closed_positions': enriched_closed_positions,  # Closed positions with market/event data
                'market_positions': all_market_positions,  # All market positions (unfiltered)
                'closed_positions': closed_positions,  # All closed positions (raw data)
                'total_closed_positions': len(closed_positions)
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio metrics: {e}")
            return None

    
    def get_recent_pnl(self, hours: int = 24) -> Optional[Dict[str, Any]]:
        """Get realized P&L from recent trading activity."""
        try:
            
            # Get recent fills to calculate realized PnL
            fills_data = self.get_fills(limit=200)
            if fills_data is None:
                logger.error("Failed to get fills data for recent P&L calculation")
                return None
            
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
                        count = fill.count
                        price = fill.price  # in cents
                        
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
            trade_volume = sum((fill.count * fill.price) / 100.0 for fill in recent_fills)
            
            return {
                'realized_pnl': total_realized_pnl,
                'trade_count': len(recent_fills),
                'trade_volume': trade_volume,
                'recent_fills': [fill.to_dict() for fill in recent_fills[:10]]  # Return last 10 fills for display
            }
            
        except Exception as e:
            logger.error(f"Failed to get recent PnL: {e}")
            return None
    
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            if not self.client:
                return False
            # Try to get balance as a simple health check
            balance = self.get_balance_dollars()
            return balance is not None
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
