"""
Consolidated Kalshi API client with authentication and market data functionality.
"""
import base64
import datetime
import logging
import requests
from typing import List, Optional, Dict, Any
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from models import Market, Event, Position
from config import Config

logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Consolidated Kalshi API client with authentication and market data functionality."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.base_url = (self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                        else self.config.KALSHI_API_HOST).rstrip('/')
        self.authenticator = self._initialize_authenticator()
        
        # Simple cache for market data to avoid repeated API calls
        self._market_cache = {}
        self._cache_timestamp = None
        self._cache_duration = 300  # 5 minutes cache
        
    def _initialize_authenticator(self):
        """Initialize authenticator if credentials are provided."""
        if not (self.config.KALSHI_API_KEY_ID and self.config.KALSHI_PRIVATE_KEY_PATH):
            logger.warning("No API credentials provided - client will not work for authenticated endpoints")
            return None
            
        try:
            return KalshiAuthenticator(self.config.KALSHI_API_KEY_ID, 
                                     self.config.KALSHI_PRIVATE_KEY_PATH)
        except Exception as e:
            logger.error(f"Failed to initialize authenticator: {e}")
            raise
    
    def _make_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """Make an authenticated request to the Kalshi API."""
        if not self.authenticator:
            raise RuntimeError("No authenticator available - check API credentials")
            
        url = f"{self.base_url}{endpoint}"
        headers = self.authenticator.create_headers(method, endpoint)
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=headers)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=headers, json=data)
            elif method.upper() == 'PUT':
                response = requests.put(url, headers=headers, json=data)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API request failed: {response.status_code} - {response.text}")
                response.raise_for_status()
                
        except Exception as e:
            logger.error(f"Failed to make request to {endpoint}: {e}")
            raise
    
    def get_markets(self, limit: int = 100, status: Optional[str] = None) -> List[Market]:
        """Fetch markets from Kalshi API."""
        try:
            endpoint = f"/trade-api/v2/markets?limit={limit}"
            if status:
                endpoint += f"&status={status}"
            
            response_data = self._make_request("GET", endpoint)
            markets = []
            
            for market_data in response_data.get('markets', []):
                try:
                    markets.append(Market.model_validate(market_data))
                except Exception as e:
                    logger.warning(f"Failed to create market {market_data.get('ticker', 'unknown')}: {e}")
                    continue
            
            return markets
            
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []
    
    def get_events(self, limit: int = 100, status: Optional[str] = None, max_events: Optional[int] = None) -> List[Event]:
        """Fetch events from Kalshi API using pagination."""
        try:
            all_events = []
            cursor = None
            page_count = 0
            
            while True:
                page_count += 1
                
                endpoint = f"/trade-api/v2/events?limit={limit}&with_nested_markets=true"
                if status:
                    endpoint += f"&status={status}"
                if cursor:
                    endpoint += f"&cursor={cursor}"
                
                response_data = self._make_request("GET", endpoint)
                events_data = response_data.get('events', [])
                cursor = response_data.get('cursor')
                
                if not events_data:
                    break
                
                for event_data in events_data:
                    try:
                        all_events.append(Event.model_validate(event_data))
                    except Exception as e:
                        logger.warning(f"Failed to create event {event_data.get('event_ticker', 'unknown')}: {e}")
                        continue
                
                if not cursor or (max_events and len(all_events) >= max_events):
                    break
            
            return all_events
            
        except Exception as e:
            logger.error(f"Failed to fetch events: {e}")
            return []
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Market]:
        """Fetch a specific market by ticker."""
        try:
            endpoint = f"/trade-api/v2/markets/{ticker}"
            response_data = self._make_request("GET", endpoint)
            market_data = response_data.get('market', {})
            
            if market_data:
                return Market.model_validate(market_data)
            else:
                return None
        except Exception as e:
            logger.error(f"Failed to fetch market {ticker}: {e}")
            return None
    
    def get_market_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch orderbook for a specific market."""
        try:
            endpoint = f"/trade-api/v2/markets/{ticker}/orderbook"
            response_data = self._make_request("GET", endpoint)
            orderbook = response_data.get('orderbook', {})
            return {
                'yes_bid': orderbook.get('yes_bid'),
                'yes_ask': orderbook.get('yes_ask'),
                'no_bid': orderbook.get('no_bid'),
                'no_ask': orderbook.get('no_ask'),
                'timestamp': datetime.datetime.now(datetime.timezone.utc)
            }
        except Exception as e:
            logger.error(f"Failed to fetch orderbook for {ticker}: {e}")
            return None
    
    def get_balance(self) -> Optional[float]:
        """Get account balance in dollars."""
        try:
            endpoint = "/trade-api/v2/portfolio/balance"
            response_data = self._make_request("GET", endpoint)
            balance_cents = response_data.get('balance', 0)
            return balance_cents / 100.0
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def get_positions(self) -> List[Position]:
        """Get all portfolio positions."""
        try:
            endpoint = "/trade-api/v2/portfolio/positions"
            response_data = self._make_request("GET", endpoint)
            
            # The API returns both market_positions and event_positions
            market_positions = response_data.get('market_positions', [])
            event_positions = response_data.get('event_positions', [])
            
            logger.info(f"Retrieved {len(market_positions)} market positions and {len(event_positions)} event positions")
            if market_positions:
                logger.debug(f"Sample market position keys: {list(market_positions[0].keys())}")
            if event_positions:
                logger.debug(f"Sample event position keys: {list(event_positions[0].keys())}")
            
            # Get market titles and statuses for better display (only if we have market positions)
            market_titles = {}
            market_statuses = {}
            if market_positions:
                market_titles = self._get_market_titles_cache()
                market_statuses = self._get_market_status_cache()
            
            positions = []
            
            # Process market positions
            for pos_data in market_positions:
                try:
                    ticker = pos_data.get('ticker', '')
                    market_title = market_titles.get(ticker, '')
                    
                    # Get market status from the cache or API data
                    market_status = market_statuses.get(ticker, 'active')  # Default to active
                    if 'market_status' in pos_data:
                        market_status = pos_data.get('market_status', market_status)
                    
                    # Calculate unrealized P&L if we have the data
                    unrealized_pnl = None
                    if 'unrealized_pnl_dollars' in pos_data:
                        unrealized_pnl = float(pos_data.get('unrealized_pnl_dollars', 0.0))
                    # Note: Removed expensive individual market lookup for unrealized P&L calculation
                    
                    position = Position(
                        ticker=ticker,
                        position=pos_data.get('position', 0),
                        market_status=market_status,
                        total_cost=float(pos_data.get('total_cost_dollars', 0.0)) if pos_data.get('total_cost_dollars') is not None else None,
                        total_value=float(pos_data.get('market_exposure_dollars', 0.0)) if pos_data.get('market_exposure_dollars') is not None else None,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=float(pos_data.get('realized_pnl_dollars', 0.0)) if pos_data.get('realized_pnl_dollars') is not None else None,
                        market_title=market_title,
                        event_title=None,
                        market_exposure=float(pos_data.get('market_exposure_dollars', 0.0)) if pos_data.get('market_exposure_dollars') is not None else None,
                        total_traded=float(pos_data.get('total_traded_dollars', 0.0)) if pos_data.get('total_traded_dollars') is not None else None,
                        fees_paid=float(pos_data.get('fees_paid_dollars', 0.0)) if pos_data.get('fees_paid_dollars') is not None else None
                    )
                    positions.append(position)
                except Exception as e:
                    logger.warning(f"Failed to parse market position {pos_data.get('ticker', 'unknown')}: {e}")
                    continue
            
            # Process event positions
            for pos_data in event_positions:
                try:
                    event_ticker = pos_data.get('event_ticker', '')
                    
                    # Calculate unrealized P&L if we have the data
                    unrealized_pnl = None
                    if 'unrealized_pnl_dollars' in pos_data:
                        unrealized_pnl = float(pos_data.get('unrealized_pnl_dollars', 0.0))
                    
                    position = Position(
                        ticker=event_ticker,
                        position=0,  # Event positions don't have individual position size
                        market_status='active',  # Event positions are typically active
                        total_cost=float(pos_data.get('total_cost_dollars', 0.0)) if pos_data.get('total_cost_dollars') is not None else None,
                        total_value=float(pos_data.get('event_exposure_dollars', 0.0)) if pos_data.get('event_exposure_dollars') is not None else None,
                        unrealized_pnl=unrealized_pnl,
                        realized_pnl=float(pos_data.get('realized_pnl_dollars', 0.0)) if pos_data.get('realized_pnl_dollars') is not None else None,
                        market_title=None,
                        event_title=self._get_event_title(event_ticker),
                        market_exposure=float(pos_data.get('event_exposure_dollars', 0.0)) if pos_data.get('event_exposure_dollars') is not None else None,
                        total_traded=None,
                        fees_paid=float(pos_data.get('fees_paid_dollars', 0.0)) if pos_data.get('fees_paid_dollars') is not None else None
                    )
                    positions.append(position)
                except Exception as e:
                    logger.warning(f"Failed to parse event position {pos_data.get('event_ticker', 'unknown')}: {e}")
                    continue
            
            return positions
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []
    
    def _get_market_titles_cache(self) -> Dict[str, str]:
        """Get market titles for better display (cached)."""
        try:
            # Check if we have cached data
            if self._is_cache_valid():
                return self._market_cache.get('titles', {})
            
            # Get fewer markets to build a title cache (use smaller limit for speed)
            markets = self.get_markets(limit=50)  # Reduced from 100 to 50 for speed
            titles = {}
            for market in markets:
                if hasattr(market, 'ticker') and hasattr(market, 'title'):
                    titles[market.ticker] = market.title
            
            # Cache the results
            self._market_cache['titles'] = titles
            self._cache_timestamp = datetime.datetime.now()
            
            return titles
        except Exception as e:
            logger.warning(f"Failed to get market titles: {e}")
            return {}
    
    def _get_market_status_cache(self) -> Dict[str, str]:
        """Get market statuses for better display (cached)."""
        try:
            # Check if we have cached data
            if self._is_cache_valid():
                return self._market_cache.get('statuses', {})
            
            # Get fewer markets to build a status cache (use smaller limit for speed)
            markets = self.get_markets(limit=50)  # Reduced from 100 to 50 for speed
            statuses = {}
            for market in markets:
                if hasattr(market, 'ticker') and hasattr(market, 'status'):
                    statuses[market.ticker] = market.status
            
            # Cache the results
            self._market_cache['statuses'] = statuses
            self._cache_timestamp = datetime.datetime.now()
            
            return statuses
        except Exception as e:
            logger.warning(f"Failed to get market statuses: {e}")
            return {}
    
    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid."""
        if not self._cache_timestamp:
            return False
        
        time_diff = datetime.datetime.now() - self._cache_timestamp
        return time_diff.total_seconds() < self._cache_duration
    
    def _calculate_unrealized_pnl(self, ticker: str, position: int) -> Optional[float]:
        """Calculate unrealized P&L for a position."""
        try:
            if position == 0:
                return 0.0
            
            # Get current market data
            market = self.get_market_by_ticker(ticker)
            if not market:
                return None
            
            # Calculate current value based on mid price
            if hasattr(market, 'mid_price') and market.mid_price:
                current_value = position * market.mid_price
                # This is a simplified calculation - in reality you'd need to know the entry price
                # For now, we'll return None to indicate we can't calculate it accurately
                return None
            
            return None
        except Exception as e:
            logger.warning(f"Failed to calculate unrealized P&L for {ticker}: {e}")
            return None
    
    def _get_event_title(self, event_ticker: str) -> str:
        """Get event title for better display."""
        try:
            # Get events to build a title cache (use smaller limit)
            events = self.get_events(limit=100)  # Get up to 100 events
            for event in events:
                if hasattr(event, 'event_ticker') and hasattr(event, 'title'):
                    if event.event_ticker == event_ticker:
                        return event.title
            return ""
        except Exception as e:
            logger.warning(f"Failed to get event title for {event_ticker}: {e}")
            return ""
    
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            if not self.authenticator:
                return False
            self.get_markets(limit=1)
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


class KalshiAuthenticator:
    """Handles Kalshi API authentication using RSA-PSS signatures."""
    
    def __init__(self, api_key_id: str, private_key_path: str):
        self.api_key_id = api_key_id
        self.private_key = self._load_private_key(private_key_path)
    
    def _load_private_key(self, file_path: str) -> rsa.RSAPrivateKey:
        """Load private key from file."""
        try:
            with open(file_path, "rb") as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                    backend=default_backend()
                )
            return private_key
        except Exception as e:
            logger.error(f"Failed to load private key from {file_path}: {e}")
            raise
    
    def _sign_pss_text(self, text: str) -> str:
        """Sign text with private key using RSA-PSS."""
        try:
            message = text.encode('utf-8')
            signature = self.private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256()
            )
            return base64.b64encode(signature).decode('utf-8')
        except InvalidSignature as e:
            raise ValueError("RSA sign PSS failed") from e
    
    def _generate_timestamp(self) -> str:
        """Generate current timestamp in milliseconds."""
        return str(int(datetime.datetime.now().timestamp() * 1000))
    
    def create_headers(self, method: str, path: str) -> dict:
        """Create authentication headers for Kalshi API request."""
        timestamp_str = self._generate_timestamp()
        msg_string = timestamp_str + method + path
        signature = self._sign_pss_text(msg_string)
        
        return {
            'KALSHI-ACCESS-KEY': self.api_key_id,
            'KALSHI-ACCESS-SIGNATURE': signature,
            'KALSHI-ACCESS-TIMESTAMP': timestamp_str,
            'Content-Type': 'application/json'
        }