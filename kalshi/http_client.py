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
    
    # Single shared instance
    _instance = None
    
    def __new__(cls, config: Config):
        """Return the single shared instance."""
        if cls._instance is None:
            logger.debug(f"[AUTH DEBUG] Creating new singleton HTTP client instance")
            cls._instance = super().__new__(cls)
        else:
            logger.debug(f"[AUTH DEBUG] Returning existing singleton HTTP client instance {id(cls._instance)}")
        return cls._instance
    
    def __init__(self, config: Config):
        """Initialize the HTTP client (only once)."""
        if hasattr(self, '_initialized'):
            logger.debug(f"[AUTH DEBUG] HTTP client already initialized, skipping (instance {id(self)})")
            return
            
        logger.debug(f"[AUTH DEBUG] Initializing new HTTP client instance {id(self)}")
        self.config = config
        self._private_key = None
        self._load_private_key()
        
        # Simple in-memory cache with TTL
        self._cache = {}
        self._cache_ttl = CACHE_TTL
        
        # Request frequency tracking
        self._request_count = 0
        self._last_request_time = None
        
        # Order placement throttling
        self._last_order_time = None
        self._min_order_interval = 1.0  # Minimum 1 second between order placements
        
        # DEBUG: Session state tracking
        self._session_start_time = time.time()
        self._successful_requests = 0
        self._failed_requests = 0
        self._last_successful_request_time = None
        self._last_failed_request_time = None
        
        self._initialized = True
        
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
        
        # DEBUG: Log signature creation details
        logger.debug(f"[AUTH DEBUG] Signature message: {message.decode('utf-8')}")
        logger.debug(f"[AUTH DEBUG] Message length: {len(message)} bytes")
        
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        encoded_signature = base64.b64encode(signature).decode('utf-8')
        
        # DEBUG: Log signature details
        logger.debug(f"[AUTH DEBUG] Signature length: {len(encoded_signature)} chars")
        logger.debug(f"[AUTH DEBUG] Signature preview: {encoded_signature[:20]}...")
        
        return encoded_signature
    
    def make_authenticated_request(self, method: str, path: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None) -> requests.Response:
        """Make an authenticated request to the Kalshi API using raw HTTP."""
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            raise Exception("API credentials not properly configured")
        
        return self._make_authenticated_request_internal(method, path, params, json_data)
    
    def _make_authenticated_request_internal(self, method: str, path: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None, retry_count: int = 0) -> requests.Response:
        """Internal method to make authenticated request (called with lock)."""
        
        # Track request frequency
        import time
        current_time = time.time()
        self._request_count += 1
        self._last_request_time = current_time
        
        # DEBUG: Log request details for 409 diagnosis
        logger.debug(f"[AUTH DEBUG] Request #{self._request_count}: {method} {path}")
        logger.debug(f"[AUTH DEBUG] Retry count: {retry_count}")
        logger.debug(f"[AUTH DEBUG] Instance ID: {id(self)}")
        if json_data:
            logger.debug(f"[AUTH DEBUG] Payload keys: {list(json_data.keys())}")
            if 'client_order_id' in json_data:
                logger.debug(f"[AUTH DEBUG] Client order ID: {json_data['client_order_id']}")
            if 'order_group_id' in json_data:
                logger.debug(f"[AUTH DEBUG] Order group ID: {json_data['order_group_id']}")
        logger.debug(f"[AUTH DEBUG] Time since last request: {current_time - self._last_request_time if self._last_request_time else 'N/A'}")
        
        # DEBUG: Check for potential session conflicts
        if hasattr(self, '_concurrent_requests'):
            logger.debug(f"[AUTH DEBUG] Concurrent requests in flight: {self._concurrent_requests}")
        else:
            self._concurrent_requests = 0
        self._concurrent_requests += 1
        
        # Determine base URL
        base_url = (self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                   else self.config.KALSHI_API_HOST)
        
        # Order placement throttling - special handling for order requests
        if path == "/portfolio/orders":
            if self._last_order_time:
                time_since_last_order = time.time() - self._last_order_time
                if time_since_last_order < self._min_order_interval:
                    sleep_time = self._min_order_interval - time_since_last_order
                    logger.info(f"[AUTH] Order throttling: sleeping {sleep_time:.2f}s")
                    time.sleep(sleep_time)
            self._last_order_time = time.time()
        
        # Create timestamp for this request
        timestamp = str(int(datetime.now().timestamp() * 1000))
        
        # For signature, we need the full API path
        signature_path = f"/trade-api/v2{path}"
        signature = self._create_signature(timestamp, method, signature_path)
        
        # DEBUG: Log authentication details
        logger.debug(f"[AUTH DEBUG] Timestamp: {timestamp}")
        logger.debug(f"[AUTH DEBUG] Signature path: {signature_path}")
        logger.debug(f"[AUTH DEBUG] API Key ID: {self.config.KALSHI_API_KEY_ID[:8]}...")
        logger.debug(f"[AUTH DEBUG] Private key loaded: {self._private_key is not None}")
        logger.debug(f"[AUTH DEBUG] Demo mode: {self.config.KALSHI_DEMO_MODE}")
        logger.debug(f"[AUTH DEBUG] Base URL: {base_url}")
        
        # Set up headers
        headers = {
            'KALSHI-ACCESS-KEY': self.config.KALSHI_API_KEY_ID,
            'KALSHI-ACCESS-SIGNATURE': signature,
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
        
        # Make request - base URL already includes /trade-api/v2
        url = base_url.rstrip('/') + path
        logger.debug(f"[AUTH DEBUG] Making request to: {url}")
        
        response = requests.request(method, url, headers=headers, params=params, json=json_data)
        
        # DEBUG: Decrement concurrent requests counter
        self._concurrent_requests -= 1
        
        # DEBUG: Log response details and update session state
        logger.debug(f"[AUTH DEBUG] Response status: {response.status_code}")
        logger.debug(f"[AUTH DEBUG] Response headers: {dict(response.headers)}")
        if response.status_code != 200:
            logger.debug(f"[AUTH DEBUG] Response body: {response.text[:500]}")  # First 500 chars
        
        # DEBUG: Check for specific 409 error patterns
        if response.status_code == 409:
            logger.debug(f"[AUTH DEBUG] 409 ERROR DETAILS:")
            logger.debug(f"[AUTH DEBUG] - Full response text: {response.text}")
            logger.debug(f"[AUTH DEBUG] - Request method: {method}")
            logger.debug(f"[AUTH DEBUG] - Request path: {path}")
            logger.debug(f"[AUTH DEBUG] - Timestamp used: {timestamp}")
            logger.debug(f"[AUTH DEBUG] - Instance ID: {id(self)}")
            logger.debug(f"[AUTH DEBUG] - Session duration: {current_time - self._session_start_time:.2f}s")
            logger.debug(f"[AUTH DEBUG] - Total requests so far: {self._request_count}")
            logger.debug(f"[AUTH DEBUG] - Success/failure ratio: {self._successful_requests}/{self._failed_requests}")
        
        # Update session state tracking
        if 200 <= response.status_code < 300:
            self._successful_requests += 1
            self._last_successful_request_time = current_time
            logger.debug(f"[AUTH DEBUG] Session stats: {self._successful_requests} successful, {self._failed_requests} failed")
        else:
            self._failed_requests += 1
            self._last_failed_request_time = current_time
            logger.debug(f"[AUTH DEBUG] Session stats: {self._successful_requests} successful, {self._failed_requests} failed")
            
        # Log timing patterns that might trigger 409s
        if self._last_successful_request_time and self._last_failed_request_time:
            time_between_success_and_failure = self._last_failed_request_time - self._last_successful_request_time
            logger.debug(f"[AUTH DEBUG] Time between last success and current request: {time_between_success_and_failure:.2f}s")
        
        # Handle 409 "user already exists" - reset session and retry once
        if response.status_code == 409:
            logger.error(f"[AUTH] 409 error: {response.text}")
            
            # Prevent infinite retry loops
            if retry_count > 0:
                logger.error(f"[AUTH] Session reset failed - giving up after {retry_count} retries")
                return response
                
            logger.error(f"[AUTH] Session conflict detected - resetting session and retrying")
            
            # DEBUG: Log session state before reset
            session_duration = current_time - self._session_start_time
            logger.debug(f"[AUTH DEBUG] Session duration before reset: {session_duration:.2f}s")
            logger.debug(f"[AUTH DEBUG] Total requests in this session: {self._request_count}")
            logger.debug(f"[AUTH DEBUG] Success rate: {self._successful_requests}/{self._request_count}")
            
            # Reset the singleton instance to clear any stale session state
            KalshiHTTPClient.reset_instance()
            logger.debug(f"[AUTH DEBUG] Singleton instance reset complete")
            
            # Create a new instance and retry the request
            new_client = KalshiHTTPClient(self.config)
            logger.info(f"[AUTH] Retrying request after session reset")
            logger.debug(f"[AUTH DEBUG] New client instance created for retry")
            return new_client._make_authenticated_request_internal(method, path, params, json_data, retry_count + 1)
        
        return response
    
    def make_public_request(self, path: str, params: Optional[Dict] = None) -> requests.Response:
        """Make a public (unauthenticated) request to the Kalshi API."""
        # Determine base URL
        base_url = (self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                   else self.config.KALSHI_API_HOST)
        
        # Make request
        url = base_url.rstrip('/') + path
        return requests.get(url, params=params)
    
    def get_request_stats(self) -> Dict[str, Any]:
        """Get request frequency statistics."""
        import time
        current_time = time.time()
        
        if self._last_request_time:
            time_since_last = current_time - self._last_request_time
            avg_requests_per_second = self._request_count / (current_time - (self._last_request_time - time_since_last)) if self._last_request_time > 0 else 0
        else:
            time_since_last = 0
            avg_requests_per_second = 0
        
        return {
            'total_requests': self._request_count,
            'time_since_last_request': time_since_last,
            'avg_requests_per_second': avg_requests_per_second
        }
    
    
    @classmethod
    def reset_instance(cls):
        """Reset the single instance (useful for session issues)."""
        if cls._instance:
            logger.debug(f"[AUTH DEBUG] Resetting HTTP client instance {id(cls._instance)}")
            cls._instance = None
        else:
            logger.debug(f"[AUTH DEBUG] No instance to reset")
        logger.info("[AUTH] HTTP client instance reset")
    
    
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            # Try to get balance as a simple health check
            response = self.make_authenticated_request("GET", "/portfolio/balance")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
