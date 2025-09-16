"""
Kalshi API authentication module following the official documentation.
Based on: https://docs.kalshi.com/getting_started/api_keys
"""
import base64
import datetime
import logging
from typing import Optional
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature
import requests

logger = logging.getLogger(__name__)

class KalshiAuthenticator:
    """Handles Kalshi API authentication using RSA-PSS signatures."""
    
    def __init__(self, api_key_id: str, private_key_path: str):
        """
        Initialize the authenticator.
        
        Args:
            api_key_id: The API key ID from Kalshi
            private_key_path: Path to the private key file
        """
        self.api_key_id = api_key_id
        self.private_key = self._load_private_key(private_key_path)
    
    def _load_private_key(self, file_path: str) -> rsa.RSAPrivateKey:
        """
        Load private key from file.
        
        Args:
            file_path: Path to the private key file
            
        Returns:
            RSA private key object
        """
        try:
            with open(file_path, "rb") as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,  # or provide a password if your key is encrypted
                    backend=default_backend()
                )
            logger.info(f"Successfully loaded private key from {file_path}")
            return private_key
        except Exception as e:
            logger.error(f"Failed to load private key from {file_path}: {e}")
            raise
    
    def _sign_pss_text(self, text: str) -> str:
        """
        Sign text with private key using RSA-PSS.
        
        Args:
            text: Text to sign
            
        Returns:
            Base64 encoded signature
        """
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
        current_time = datetime.datetime.now()
        timestamp = current_time.timestamp()
        return str(int(timestamp * 1000))
    
    def create_headers(self, method: str, path: str) -> dict:
        """
        Create authentication headers for Kalshi API request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            
        Returns:
            Dictionary of headers including authentication
        """
        timestamp_str = self._generate_timestamp()
        
        # Create message string: timestamp + method + path
        msg_string = timestamp_str + method + path
        
        # Sign the message
        signature = self._sign_pss_text(msg_string)
        
        # Create headers
        headers = {
            'KALSHI-ACCESS-KEY': self.api_key_id,
            'KALSHI-ACCESS-SIGNATURE': signature,
            'KALSHI-ACCESS-TIMESTAMP': timestamp_str,
            'Content-Type': 'application/json'
        }
        
        return headers
    
    def make_authenticated_request(self, method: str, url: str, path: str, 
                                 data: Optional[dict] = None) -> requests.Response:
        """
        Make an authenticated request to Kalshi API.
        
        Args:
            method: HTTP method
            url: Full URL
            path: API endpoint path
            data: Request data (for POST/PUT requests)
            
        Returns:
            Response object
        """
        headers = self.create_headers(method, path)
        
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
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to make authenticated request: {e}")
            raise

class KalshiAPIClientManual:
    """Manual Kalshi API client using proper authentication."""
    
    def __init__(self, api_key_id: str, private_key_path: str, base_url: str):
        """
        Initialize the API client.
        
        Args:
            api_key_id: API key ID
            private_key_path: Path to private key file
            base_url: Base URL for API (demo or production)
        """
        self.base_url = base_url.rstrip('/')
        self.authenticator = KalshiAuthenticator(api_key_id, private_key_path)
    
    def _make_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """
        Make a request to the Kalshi API.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            
        Returns:
            JSON response data
        """
        url = f"{self.base_url}{endpoint}"
        
        response = self.authenticator.make_authenticated_request(method, url, endpoint, data)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API request failed: {response.status_code} - {response.text}")
            response.raise_for_status()
    
    def get_markets(self, limit: int = 100, status: Optional[str] = None) -> dict:
        """
        Get markets from Kalshi API.
        
        Args:
            limit: Maximum number of markets to fetch
            status: Filter by market status
            
        Returns:
            Markets response data
        """
        endpoint = f"/trade-api/v2/markets?limit={limit}"
        if status:
            endpoint += f"&status={status}"
        
        return self._make_request("GET", endpoint)
    
    def get_events(self, limit: int = 100, status: Optional[str] = None, cursor: Optional[str] = None) -> dict:
        """
        Get events from Kalshi API.
        
        Args:
            limit: Maximum number of events to fetch
            status: Filter by event status
            cursor: Cursor for pagination
        Returns:
            Events response data
        """
        endpoint = f"/trade-api/v2/events?limit={limit}&with_nested_markets=true"
        if status:
            endpoint += f"&status={status}"
        if cursor:
            endpoint += f"&cursor={cursor}"
        return self._make_request("GET", endpoint)
    
    def get_market(self, ticker: str) -> dict:
        """
        Get a specific market by ticker.
        
        Args:
            ticker: Market ticker symbol
            
        Returns:
            Market data
        """
        endpoint = f"/trade-api/v2/markets/{ticker}"
        return self._make_request("GET", endpoint)
    
    def get_market_orderbook(self, ticker: str) -> dict:
        """
        Get orderbook for a specific market.
        
        Args:
            ticker: Market ticker symbol
            
        Returns:
            Orderbook data
        """
        endpoint = f"/trade-api/v2/markets/{ticker}/orderbook"
        return self._make_request("GET", endpoint)
    
    def get_balance(self) -> dict:
        """
        Get account balance.
        
        Returns:
            Balance data
        """
        endpoint = "/trade-api/v2/portfolio/balance"
        return self._make_request("GET", endpoint)
    
    def health_check(self) -> bool:
        """
        Check if the API is accessible.
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            # Try to get markets with a small limit
            self.get_markets(limit=1)
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
