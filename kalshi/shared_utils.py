"""
Kalshi Shared Utilities - Common functions used across multiple modules.
"""
import logging
from typing import Dict, Any, Optional, List
import kalshi_python
import requests

from .http_client import KalshiHTTPClient
from .constants import VALID_MARKET_STATUSES, REQUEST_TIMEOUT
from .models import Event

logger = logging.getLogger(__name__)

def create_sdk_client(client: KalshiHTTPClient) -> kalshi_python.KalshiClient:
    """Create a configured Kalshi SDK client."""
    configuration = kalshi_python.Configuration(
        host=client.config.KALSHI_DEMO_HOST if client.config.KALSHI_DEMO_MODE 
             else client.config.KALSHI_API_HOST
    )
    
    # Read private key from file
    with open(client.config.KALSHI_PRIVATE_KEY_PATH, 'r') as f:
        private_key = f.read()
    
    configuration.api_key_id = client.config.KALSHI_API_KEY_ID
    configuration.private_key_pem = private_key
    
    return kalshi_python.KalshiClient(configuration)

def preprocess_market_data(data):
    """Recursively preprocess market data to handle known API inconsistencies."""
    if isinstance(data, dict):
        # Create a copy to avoid modifying the original
        cleaned = data.copy()
        
        # Handle status field - map non-standard values to valid enum values
        status = cleaned.get('status')
        if status and status not in VALID_MARKET_STATUSES:
            if status == 'finalized':
                logger.info(f"Converting status 'finalized' to 'settled' for ticker: {cleaned.get('ticker', 'unknown')}")
                cleaned['status'] = 'settled'
            else:
                logger.info(f"Converting non-standard status '{status}' to 'closed' for ticker: {cleaned.get('ticker', 'unknown')}")
                cleaned['status'] = 'closed'
        elif status == 'finalized':
            logger.info(f"Status 'finalized' found for ticker: {cleaned.get('ticker', 'unknown')} - should be in VALID_MARKET_STATUSES")
        
        # Recursively clean nested structures
        for key, value in cleaned.items():
            cleaned[key] = preprocess_market_data(value)
        
        return cleaned
    elif isinstance(data, list):
        return [preprocess_market_data(item) for item in data]
    else:
        return data

def preprocess_event_data(data, status: Optional[str] = None):
    """Recursively preprocess event data to handle known API inconsistencies."""
    markets = data.get('markets', [])
    cleaned = data.copy()
    markets_to_keep = []
    for market in markets:
        processed_market = is_market_valid(market, status)
        if processed_market:
            markets_to_keep.append(market)
    cleaned['markets'] = markets_to_keep
    return cleaned

def is_market_valid(data, status: Optional[str] = None):
    """Check if market data is valid based on status."""
    market_status = data.get('status')
    if market_status not in VALID_MARKET_STATUSES:
        return False

    if status is not None:
        if status == 'open' and market_status not in ["active", "open"]:
            return False
        elif status != 'open' and market_status != status:
            return False
    
    return True

def fetch_event_by_ticker(client: KalshiHTTPClient, event_ticker: str) -> Optional[Event]:
    """Fetch a specific event by ticker using direct HTTP requests."""
    try:
        # Make direct API call to get event
        url = f"{client.config.KALSHI_DEMO_HOST if client.config.KALSHI_DEMO_MODE else client.config.KALSHI_API_HOST}/events/{event_ticker}"
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 200:
            data = response.json()
            if 'event' in data and data['event']:
                event_dict = data['event']
                # Preprocess to handle known issues
                cleaned_event_dict = preprocess_event_data(event_dict)
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

def get_base_api_url(client: KalshiHTTPClient) -> str:
    """Get the base API URL for the client."""
    return client.config.KALSHI_DEMO_HOST if client.config.KALSHI_DEMO_MODE else client.config.KALSHI_API_HOST
