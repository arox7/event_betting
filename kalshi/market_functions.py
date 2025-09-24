"""
Kalshi Market Functions - Market, event, and orderbook operations.
"""
import logging
import time
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from pprint import pprint

import requests

from .http_client import KalshiHTTPClient
from .shared_utils import (
    create_sdk_client, preprocess_market_data, preprocess_event_data, 
    is_market_valid, fetch_event_by_ticker, get_base_api_url
)
from .models import Market, Event

logger = logging.getLogger(__name__)

def get_markets(client: KalshiHTTPClient, limit: int = 100, status: Optional[str] = None, cursor: Optional[str] = None) -> tuple[List[Market], Optional[str]]:
    """Fetch markets from Kalshi API using existing HTTP client methods."""
    try:
        # Build request parameters
        params = {'limit': limit}
        if status:
            params['status'] = status
        if cursor:
            params['cursor'] = cursor
        
        # Use existing HTTP client method (reuse existing code)
        response = client.make_public_request('/markets', params=params)
        
        if response.status_code != 200:
            logger.error(f"API call failed: {response.status_code} - {response.text}")
            return []
        
        data = response.json()
        markets = []
        
        # Process markets from response
        for market_dict in data.get('markets', []):
            try:
                # Preprocess market data to handle known issues
                cleaned_market_dict = preprocess_market_data(market_dict)
                if len(cleaned_market_dict) == 0:
                    continue
                    
                market = Market.model_validate(cleaned_market_dict, strict=False)
                markets.append(market)
            except Exception as e:
                ticker = market_dict.get('ticker', 'unknown')
                logger.warning(f"Skipping invalid market {ticker}: {e}")
                continue
        
        # Extract cursor for next page
        next_cursor = data.get('cursor')
        return markets, next_cursor
        
    except Exception as e:
        logger.error(f"Error fetching markets: {e}")
        return [], None

def get_all_markets(client: KalshiHTTPClient, status: Optional[str] = None, max_markets: Optional[int] = None) -> List[Market]:
    """Get all markets using pagination, following the same pattern as portfolio functions."""
    try:
        all_markets = []
        cursor = None
        request_count = 0
        max_requests = 20  # Safety limit
        
        while request_count < max_requests:
            # Get markets for this page
            markets, next_cursor = get_markets(client, limit=200, status=status, cursor=cursor)
            
            if not markets:
                logger.info(f"No more markets found on page {request_count + 1}")
                break
            
            all_markets.extend(markets)
            logger.info(f"Retrieved {len(markets)} markets on page {request_count + 1}, total so far: {len(all_markets)}")
            
            # Check if we've reached our limit
            if max_markets and len(all_markets) >= max_markets:
                logger.info(f"Reached max markets limit: {max_markets}")
                break
            
            # Check if there are more pages
            cursor = next_cursor
            if not cursor:
                logger.info("No more pages available")
                break
                
            request_count += 1
            
            # Small delay to be respectful to the API
            if cursor:  # Only delay if there are more pages
                time.sleep(0.1)
        
        logger.info(f"Total markets retrieved: {len(all_markets)}")
        return all_markets[:max_markets] if max_markets else all_markets
        
    except Exception as e:
        logger.error(f"Error fetching all markets: {e}")
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
                'timestamp': datetime.now(timezone.utc)
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
