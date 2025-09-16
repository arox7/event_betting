"""
Kalshi API client wrapper for market data queries.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from models import Market, MarketStatus, Event
from config import Config
from kalshi_auth import KalshiAPIClientManual

logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Wrapper for Kalshi API client with market data functionality."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.client = self._initialize_client()
        
    def _initialize_client(self) -> KalshiAPIClientManual:
        """Initialize and configure the Kalshi client."""
        try:
            # Check if authentication credentials are provided
            if self.config.KALSHI_API_KEY_ID and self.config.KALSHI_PRIVATE_KEY_PATH:
                try:
                    # Choose base URL based on demo mode
                    base_url = self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE else self.config.KALSHI_API_HOST
                    
                    # Initialize with authentication
                    client = KalshiAPIClientManual(
                        api_key_id=self.config.KALSHI_API_KEY_ID,
                        private_key_path=self.config.KALSHI_PRIVATE_KEY_PATH,
                        base_url=base_url
                    )
                    logger.info("Kalshi API client initialized with authentication")
                    return client
                except FileNotFoundError:
                    logger.warning(f"Private key file not found: {self.config.KALSHI_PRIVATE_KEY_PATH}")
                    raise
                except Exception as e:
                    logger.error(f"Failed to initialize authenticated client: {e}")
                    raise
            else:
                logger.warning("No API credentials provided - client will not work for authenticated endpoints")
                # Create a dummy client for testing (will fail on authenticated requests)
                return None
            
        except Exception as e:
            logger.error(f"Failed to initialize Kalshi client: {e}")
            raise
    
    def get_markets(self, limit: int = 100, status: Optional[str] = None) -> List[Market]:
        """
        Fetch markets from Kalshi API.
        
        Args:
            limit: Maximum number of markets to fetch
            status: Filter by market status (open, closed, suspended)
            
        Returns:
            List of Market objects
        """
        try:
            if not self.client:
                logger.error("No authenticated client available")
                return []
            
            # Get markets from Kalshi API
            response_data = self.client.get_markets(limit=limit, status=status)
            
            
            markets = []
            for market_data in response_data.get('markets', []):
                try:
                    market = self._convert_kalshi_market(market_data)
                    markets.append(market)
                except Exception as e:
                    logger.warning(f"Failed to convert market {market_data.get('ticker', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Successfully fetched {len(markets)} markets")
            return markets
            
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []
    
    def get_events(self, limit: int = 100, status: Optional[str] = None, max_events: Optional[int] = None) -> List[Event]:
        """
        Fetch events from Kalshi API using pagination to get all events.
        
        Args:
            limit: Maximum number of events per page (default 100)
            status: Filter by event status
            max_events: Maximum number of events to fetch
        Returns:
            List of Event objects with their markets
        """
        try:
            if not self.client:
                logger.error("No authenticated client available")
                return []
            
            all_events = []
            cursor = None
            page_count = 0
            
            while True:
                page_count += 1
                logger.info(f"Fetching events page {page_count}...")
                
                # Get events from Kalshi API with cursor for pagination
                if cursor:
                    response_data = self.client.get_events(limit=limit, status=status, cursor=cursor)
                else:
                    response_data = self.client.get_events(limit=limit, status=status)
                
                events_data = response_data.get('events', [])
                cursor = response_data.get('cursor')
                
                if not events_data:
                    logger.info("No more events data received from API")
                    break
                
                # Convert events in this page
                for event_data in events_data:
                    try:
                        event = self._convert_kalshi_event(event_data)
                        all_events.append(event)
                    except Exception as e:
                        logger.warning(f"Failed to convert event {event_data.get('event_ticker', 'unknown')}: {e}")
                        continue
                
                logger.info(f"Page {page_count}: Added {len(events_data)} events (total so far: {len(all_events)})")
                
                # If no cursor or we've reached the max number of events, stop
                if not cursor or len(all_events) >= max_events:
                    break
            
            total_markets = sum(len(event.markets) for event in all_events)
            logger.info(f"Successfully fetched {len(all_events)} events with {total_markets} total markets across {page_count} pages")
            return all_events
            
        except Exception as e:
            logger.error(f"Failed to fetch events: {e}")
            return []
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Market]:
        """
        Fetch a specific market by ticker.
        
        Args:
            ticker: Market ticker symbol
            
        Returns:
            Market object or None if not found
        """
        try:
            if not self.client:
                logger.error("No authenticated client available")
                return None
            
            response_data = self.client.get_market(ticker=ticker)
            return self._convert_kalshi_market(response_data.get('market', {}))
        except Exception as e:
            logger.error(f"Failed to fetch market {ticker}: {e}")
            return None
    
    def get_market_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch orderbook for a specific market.
        
        Args:
            ticker: Market ticker symbol
            
        Returns:
            Orderbook data or None if not found
        """
        try:
            if not self.client:
                logger.error("No authenticated client available")
                return None
            
            response_data = self.client.get_market_orderbook(ticker=ticker)
            orderbook = response_data.get('orderbook', {})
            from datetime import timezone
            return {
                'yes_bid': orderbook.get('yes_bid'),
                'yes_ask': orderbook.get('yes_ask'),
                'no_bid': orderbook.get('no_bid'),
                'no_ask': orderbook.get('no_ask'),
                'timestamp': datetime.now(timezone.utc)
            }
        except Exception as e:
            logger.error(f"Failed to fetch orderbook for {ticker}: {e}")
            return None
    
    def _convert_kalshi_market(self, market_data: dict) -> Market:
        """
        Convert Kalshi API market data to our Market model.
        
        Args:
            market_data: Market data from Kalshi API
            
        Returns:
            Market object
        """
        # Map status
        status_map = {
            'open': MarketStatus.OPEN,
            'closed': MarketStatus.CLOSED,
            'suspended': MarketStatus.SUSPENDED,
            'active': MarketStatus.OPEN
        }
        status = status_map.get(market_data.get('status', ''), MarketStatus.CLOSED)
        
        # Parse dates
        close_time = market_data.get('close_time', '')
        settle_time = market_data.get('settle_time', '')
        
        try:
            if close_time:
                # Handle both Z and +00:00 timezone formats
                if close_time.endswith('Z'):
                    close_time = close_time.replace('Z', '+00:00')
                expiry_date = datetime.fromisoformat(close_time)
            else:
                from datetime import timezone
                expiry_date = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to parse close_time '{close_time}': {e}")
            from datetime import timezone
            expiry_date = datetime.now(timezone.utc)
        
        try:
            if settle_time:
                # Handle both Z and +00:00 timezone formats
                if settle_time.endswith('Z'):
                    settle_time = settle_time.replace('Z', '+00:00')
                settlement_date = datetime.fromisoformat(settle_time)
            else:
                from datetime import timezone
                settlement_date = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to parse settle_time '{settle_time}': {e}")
            from datetime import timezone
            settlement_date = datetime.now(timezone.utc)
        
        return Market(
            ticker=market_data.get('ticker', ''),
            title=market_data.get('title', ''),
            description=market_data.get('description', ''),
            status=status,
            volume=market_data.get('volume', 0) or 0,
            open_interest=market_data.get('open_interest', 0) or 0,
            yes_bid=market_data.get('yes_bid'),
            yes_ask=market_data.get('yes_ask'),
            no_bid=market_data.get('no_bid'),
            no_ask=market_data.get('no_ask'),
            expiry_date=expiry_date,
            settlement_date=settlement_date,
            min_tick_size=market_data.get('min_tick_size', 0.01),
            max_order_size=market_data.get('max_order_size', 1000)
        )
    
    def _convert_kalshi_event(self, event_data: dict, markets: List[Market] = None) -> Event:
        """
        Convert Kalshi API event data to our Event model.
        
        Args:
            event_data: Event data from Kalshi API
            markets: Optional list of markets to associate with this event
            
        Returns:
            Event object
        """
        category = event_data.get('category', '')
        series_ticker = event_data.get('series_ticker', '')
        
        # Parse dates
        open_date_str = event_data.get('open_date', '')
        close_date_str = event_data.get('close_date', '')
        
        try:
            if open_date_str:
                if open_date_str.endswith('Z'):
                    open_date_str = open_date_str.replace('Z', '+00:00')
                open_date = datetime.fromisoformat(open_date_str)
            else:
                from datetime import timezone
                open_date = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to parse open_date '{open_date_str}': {e}")
            from datetime import timezone
            open_date = datetime.now(timezone.utc)
        
        try:
            if close_date_str:
                if close_date_str.endswith('Z'):
                    close_date_str = close_date_str.replace('Z', '+00:00')
                close_date = datetime.fromisoformat(close_date_str)
            else:
                from datetime import timezone
                close_date = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to parse close_date '{close_date_str}': {e}")
            from datetime import timezone
            close_date = datetime.now(timezone.utc)
        
        # Handle markets - either from parameter or from event data
        if markets is not None:
            # Use provided markets
            event_markets = markets
        else:
            # Convert markets from event data
            event_markets = []
            markets_data = event_data.get('markets', [])
            
            
            for market_data in markets_data:
                try:
                    market = self._convert_kalshi_market(market_data)
                    event_markets.append(market)
                except Exception as e:
                    logger.warning(f"Failed to convert market in event {event_data.get('event_ticker', 'unknown')}: {e}")
                    continue
        
        return Event(
            ticker=event_data.get('event_ticker', event_data.get('ticker', '')),
            title=event_data.get('title', ''),
            description=event_data.get('sub_title', event_data.get('description', '')),
            category=category,
            series_ticker=series_ticker,
            markets=event_markets,
            open_date=open_date,
            close_date=close_date
        )
    
    def get_balance(self) -> Optional[float]:
        """
        Get account balance.
        
        Returns:
            Account balance in dollars or None if not authenticated
        """
        try:
            if not self.client:
                logger.warning("Cannot get balance: not authenticated")
                return None
            
            response_data = self.client.get_balance()
            balance_cents = response_data.get('balance', 0)
            return balance_cents / 100.0  # Convert from cents to dollars
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def health_check(self) -> bool:
        """
        Check if the API client is working properly.
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            if not self.client:
                return False
            
            # Use the client's health check method
            return self.client.health_check()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

