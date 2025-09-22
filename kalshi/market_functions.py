"""
Kalshi Market Functions - Market, event, and orderbook operations.
"""
import logging
import concurrent.futures
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

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
    """Fetch multiple markets by tickers in batch using concurrent requests."""
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
    
    # Fetch uncached markets using concurrent requests
    fetched_markets = {}
    try:
        def fetch_single_market(ticker):
            try:
                url = f"{get_base_api_url(client)}/markets/{ticker}"
                response = requests.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    if 'market' in data and data['market']:
                        market_dict = data['market']
                        if market_dict.get("status") == "finalized":
                            market_dict["status"] = "settled"
                        market = Market.model_validate(market_dict, strict=False)
                        # Cache the result
                        client.set_cache('market', market, ticker)
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
            market = get_market_by_ticker(client, ticker)
            if market:
                fetched_markets[ticker] = market
    
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
    
    # Use ThreadPoolExecutor for concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_ticker = {executor.submit(fetch_single_event, ticker): ticker for ticker in uncached_tickers}
        
        for future in concurrent.futures.as_completed(future_to_ticker):
            event_ticker = future_to_ticker[future]
            event = future.result()
            if event:
                fetched_events[event_ticker] = event
                # Cache the result
                client.set_cache('event', event, event_ticker)
    
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
