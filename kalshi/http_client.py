"""
Kalshi HTTP Client - Base HTTP client with authentication and caching.
"""
import logging
import base64
import time
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from config import Config, setup_logging
from .constants import CACHE_TTL, DEFAULT_CACHE_TTL

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

class KalshiHTTPClient:
    """Base HTTP client for Kalshi API with authentication and caching."""
    
    def __init__(self, config: Config):
        """Initialize the HTTP client."""
        self.config = config
        self._private_key = None
        self._load_private_key()
        
        # Simple in-memory cache with TTL
        self._cache = {}
        self._cache_ttl = CACHE_TTL
        
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
        ttl = self._cache_ttl.get(cache_type, DEFAULT_CACHE_TTL)
        return (time.time() - cached_time) < ttl
    
    def get_cached(self, cache_type: str, identifier: str = ""):
        """Get cached data if valid."""
        cache_key = self._get_cache_key(cache_type, identifier)
        if self._is_cache_valid(cache_key, cache_type):
            _, data = self._cache[cache_key]
            return data
        return None
    
    def set_cache(self, cache_type: str, data: Any, identifier: str = ""):
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
    
    def make_authenticated_request(self, method: str, path: str, params: Optional[Dict] = None) -> requests.Response:
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
    
    def make_public_request(self, path: str, params: Optional[Dict] = None) -> requests.Response:
        """Make a public (unauthenticated) request to the Kalshi API."""
        # Determine base URL
        base_url = (self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                   else self.config.KALSHI_API_HOST)
        
        # Make request
        url = base_url.rstrip('/') + path
        return requests.get(url, params=params)
    
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            # Try to get balance as a simple health check
            response = self.make_authenticated_request("GET", "/portfolio/balance")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
