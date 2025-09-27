"""
Kalshi Market Functions - Market, event, and orderbook operations.
"""
import logging
import time
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
from pprint import pprint

import requests

from .http_client import KalshiHTTPClient
from .shared_utils import (
    create_sdk_client, preprocess_market_data, preprocess_event_data, 
    is_market_valid, fetch_event_by_ticker, get_base_api_url
)
from .models import Market, Event

logger = logging.getLogger(__name__)

def get_markets(client: KalshiHTTPClient, limit: int = 100, status: Optional[str] = None) -> List[Market]:
    """Fetch markets from Kalshi API using the official SDK."""
    try:
        # Use shared SDK client factory
        sdk_client = create_sdk_client(client)
        
        response = sdk_client.get_markets(limit=limit, status=status)
        markets = []
        
        for market_data in response.markets or []:
            try:
                # Convert the SDK response to our Market model
                market_dict = market_data.to_dict()
                
                # Handle status mapping for non-standard statuses
                if market_dict.get("status") == "finalized":
                    market_dict["status"] = "settled"
                
                # Preprocess market data to handle known issues
                cleaned_market_dict = preprocess_market_data(market_dict)
                
                markets.append(Market.model_validate(cleaned_market_dict, strict=False))
            except Exception as e:
                ticker = getattr(market_data, 'ticker', 'unknown')
                logger.warning(f"Skipping invalid market {ticker}: {e}")
                continue
        
        return markets
        
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return []

def get_market_by_ticker(client: KalshiHTTPClient, ticker: str) -> Optional[Market]:
    """Fetch a specific market by ticker using direct HTTP requests."""
    # Check cache first
    cached_market = client.get_cached('market', ticker)
    if cached_market is not None:
        return cached_market
        
    try:
        # Make direct API call
        url = f"{get_base_api_url(client)}/markets/{ticker}"
        response = requests.get(url)
        
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
            client.set_cache('market', market, ticker)
            return market
        return None
    except Exception as e:
        logger.error(f"Failed to fetch market {ticker}: {e}")
        return None

def get_markets_by_tickers(client: KalshiHTTPClient, tickers: List[str]) -> Dict[str, Market]:
    """Fetch multiple markets by tickers using the batch API with tickers parameter."""
    if not tickers:
        return {}
    
    # Check cache first for all tickers
    cached_markets = {}
    uncached_tickers = []
    
    for ticker in tickers:
        cached_market = client.get_cached('market', ticker)
        if cached_market is not None:
            cached_markets[ticker] = cached_market
        else:
            uncached_tickers.append(ticker)
    
    # If all markets are cached, return them
    if not uncached_tickers:
        return cached_markets
    
    # Fetch uncached markets using batch API with tickers parameter
    fetched_markets = {}
    try:
        # Use the batch API with tickers parameter - much more efficient!
        # The API supports up to 1000 markets per request, so we can batch them
        tickers_param = ",".join(uncached_tickers)
        
        # Make batch request using the tickers parameter
        params = {
            'tickers': tickers_param,
            'limit': 1000  # Maximum allowed by API
        }
        
        response = client.make_public_request("/markets", params)
        
        if response.status_code == 200:
            data = response.json()
            markets_data = data.get('markets', [])
            
            # Process each market in the response
            for market_dict in markets_data:
                try:
                    ticker = market_dict.get('ticker')
                    if ticker and ticker in uncached_tickers:
                        # Handle status mapping
                        if market_dict.get("status") == "finalized":
                            market_dict["status"] = "settled"
                        
                        market = Market.model_validate(market_dict, strict=False)
                        fetched_markets[ticker] = market
                        
                        # Cache the result
                        client.set_cache('market', market, ticker)
                        
                except Exception as e:
                    logger.warning(f"Error processing market data for {ticker}: {e}")
                    continue
                    
        else:
            logger.error(f"Batch market fetch failed: {response.status_code} - {response.text}")
            # Fallback to individual requests if batch fails
            for ticker in uncached_tickers:
                market = get_market_by_ticker(client, ticker)
                if market:
                    fetched_markets[ticker] = market
                    time.sleep(0.2)  # Delay between fallback requests
                    
    except Exception as e:
        logger.error(f"Error in batch market fetching: {e}")
        # Fallback to individual requests
        for ticker in uncached_tickers:
            market = get_market_by_ticker(client, ticker)
            if market:
                fetched_markets[ticker] = market
                time.sleep(0.2)  # Delay between fallback requests
    
    # Combine cached and fetched markets
    all_markets = {**cached_markets, **fetched_markets}
    
    return all_markets

def get_market_orderbook(client: KalshiHTTPClient, ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch orderbook for a specific market."""
    try:
        # Use shared SDK client factory
        sdk_client = create_sdk_client(client)
        
        response = sdk_client.get_market_orderbook(ticker=ticker)
        if response.orderbook:
            orderbook = response.orderbook
            return {
                'yes_bid': orderbook.yes_bid,
                'yes_ask': orderbook.yes_ask,
                'no_bid': orderbook.no_bid,
                'no_ask': orderbook.no_ask,
                'timestamp': datetime.now(timezone(timedelta(hours=-5)))  # Eastern Time
            }
        return None
    except Exception as e:
        logger.error(f"Failed to fetch orderbook for {ticker}: {e}")
        return None


def get_events_by_tickers(client: KalshiHTTPClient, event_tickers: List[str]) -> Dict[str, Event]:
    """Fetch multiple events by tickers in batch using concurrent requests."""
    if not event_tickers:
        return {}
    
    # Check cache first for all tickers
    cached_events = {}
    uncached_tickers = []
    
    for ticker in event_tickers:
        cached_event = client.get_cached('event', ticker)
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
            return fetch_event_by_ticker(client, event_ticker)
        except Exception as e:
            logger.warning(f"Error fetching event {event_ticker}: {e}")
            return None
    
    # Use sequential requests to avoid rate limiting issues
    # Process events one by one with delays between requests
    for event_ticker in uncached_tickers:
        try:
            event = fetch_single_event(event_ticker)
            if event:
                fetched_events[event_ticker] = event
                # Cache the result
                client.set_cache('event', event, event_ticker)
                # Add delay between requests to avoid rate limiting
                time.sleep(0.1)  # Reduced delay since events are less frequent
        except Exception as e:
            logger.warning(f"Error fetching event {event_ticker}: {e}")
            continue
    
    # Combine cached and fetched events
    all_events = {**cached_events, **fetched_events}
    
    return all_events

def get_events(client: KalshiHTTPClient, limit: int = 100, status: Optional[str] = None, max_events: Optional[int] = None) -> List[Event]:
    """Fetch events from Kalshi API using direct HTTP calls with nested markets."""
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
            url = f"{get_base_api_url(client)}/events"
            response = requests.get(url, params=params)
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                break
            
            data = response.json()
            
            # Process events from this batch
            for event_raw in data.get('events', []):
                try:
                    # Preprocess to handle status validation issues
                    cleaned_event_dict = preprocess_event_data(event_raw, status)
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

def get_trades_with_pagination(
    client: KalshiHTTPClient,
    limit: int = 1000,
    min_ts: Optional[int] = None,
    max_ts: Optional[int] = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    max_trades: int = 500000
) -> List[Dict[str, Any]]:
    """
    Fetch all trades from Kalshi API with proper pagination and retry handling.
    
    Args:
        client: KalshiHTTPClient instance
        limit: Number of trades per request (max 1000)
        min_ts: Minimum timestamp (Unix timestamp in seconds)
        max_ts: Maximum timestamp (Unix timestamp in seconds)
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries (exponential backoff)
        max_trades: Maximum total number of trades to fetch (default: 500,000)
    
    Returns:
        List of all trades (up to max_trades limit)
    """
    all_trades = []
    cursor = None
    retry_count = 0
    
    logger.info(f"Starting trades fetch with limit={limit}, min_ts={min_ts}, max_ts={max_ts}, max_trades={max_trades}")
    
    while len(all_trades) < max_trades:
        # Build request parameters
        remaining_trades = max_trades - len(all_trades)
        request_limit = min(limit, 1000, remaining_trades)  # Kalshi API max limit is 1000
        
        params = {
            'limit': request_limit
        }
        
        if cursor:
            params['cursor'] = cursor
        if min_ts is not None:
            params['min_ts'] = min_ts
        if max_ts is not None:
            params['max_ts'] = max_ts
        
        # Make request with retry logic
        success = False
        current_retry = 0
        
        while current_retry <= max_retries and not success:
            try:
                logger.info(f"Fetching trades page (cursor: {cursor[:20] if cursor else 'None'}...)")
                
                response = client.make_public_request('/markets/trades', params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'trades' in data:
                        trades = data['trades']
                        all_trades.extend(trades)
                        
                        logger.info(f"Fetched {len(trades)} trades (total: {len(all_trades)})")
                        
                        # Check if we've reached the max limit
                        if len(all_trades) >= max_trades:
                            logger.info(f"Reached maximum trade limit of {max_trades}")
                            break
                        
                        # Check for next page
                        cursor = data.get('cursor')
                        if not cursor:
                            logger.info("No more pages - pagination complete")
                            break
                        
                        success = True
                        retry_count = 0  # Reset retry count on success
                        
                    else:
                        logger.warning(f"Unexpected response format: {data}")
                        success = True  # Don't retry on format issues
                        break
                else:
                    logger.warning(f"HTTP {response.status_code}: {response.text}")
                    success = False
                    
            except Exception as e:
                current_retry += 1
                retry_count += 1
                
                if current_retry <= max_retries:
                    wait_time = retry_delay * (2 ** (current_retry - 1))  # Exponential backoff
                    logger.warning(f"Request failed (attempt {current_retry}/{max_retries + 1}): {e}")
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Max retries exceeded. Last error: {e}")
                    raise e
        
        # If we couldn't get a successful response after all retries, break
        if not success:
            break
    
    logger.info(f"Trades fetch complete. Total trades: {len(all_trades)}")
    return all_trades
