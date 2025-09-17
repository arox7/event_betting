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

from models import Market, Event
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