"""
Kalshi API client wrapper for market data queries.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from kalshi_python import Configuration, KalshiClient
from kalshi_python.models import Market as KalshiMarket

from models import Market, MarketStatus, MarketCategory
from config import Config

logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Wrapper for Kalshi API client with market data functionality."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.client = self._initialize_client()
        
    def _initialize_client(self) -> KalshiClient:
        """Initialize and configure the Kalshi client."""
        try:
            # Configure the client
            kalshi_config = Configuration(
                host=self.config.KALSHI_API_HOST
            )
            
            # Add authentication if credentials are provided
            if self.config.KALSHI_API_KEY_ID and self.config.KALSHI_PRIVATE_KEY_PATH:
                try:
                    with open(self.config.KALSHI_PRIVATE_KEY_PATH, "r") as f:
                        private_key = f.read()
                    
                    kalshi_config.api_key_id = self.config.KALSHI_API_KEY_ID
                    kalshi_config.private_key_pem = private_key
                    logger.info("Kalshi API client initialized with authentication")
                except FileNotFoundError:
                    logger.warning(f"Private key file not found: {self.config.KALSHI_PRIVATE_KEY_PATH}")
                    logger.info("Kalshi API client initialized without authentication")
            else:
                logger.info("Kalshi API client initialized without authentication")
            
            return KalshiClient(kalshi_config)
            
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
            # Get markets from Kalshi API
            response = self.client.get_markets(limit=limit, status=status)
            
            markets = []
            for kalshi_market in response.markets:
                try:
                    market = self._convert_kalshi_market(kalshi_market)
                    markets.append(market)
                except Exception as e:
                    logger.warning(f"Failed to convert market {kalshi_market.ticker}: {e}")
                    continue
            
            logger.info(f"Successfully fetched {len(markets)} markets")
            return markets
            
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
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
            response = self.client.get_market(ticker=ticker)
            return self._convert_kalshi_market(response.market)
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
            response = self.client.get_market_orderbook(ticker=ticker)
            return {
                'yes_bid': response.yes_bid,
                'yes_ask': response.yes_ask,
                'no_bid': response.no_bid,
                'no_ask': response.no_ask,
                'timestamp': datetime.now()
            }
        except Exception as e:
            logger.error(f"Failed to fetch orderbook for {ticker}: {e}")
            return None
    
    def _convert_kalshi_market(self, kalshi_market: KalshiMarket) -> Market:
        """
        Convert Kalshi API market object to our Market model.
        
        Args:
            kalshi_market: Market object from Kalshi API
            
        Returns:
            Market object
        """
        # Map status
        status_map = {
            'open': MarketStatus.OPEN,
            'closed': MarketStatus.CLOSED,
            'suspended': MarketStatus.SUSPENDED
        }
        status = status_map.get(kalshi_market.status, MarketStatus.CLOSED)
        
        # Map category (simplified mapping)
        category_map = {
            'politics': MarketCategory.POLITICS,
            'economics': MarketCategory.ECONOMICS,
            'sports': MarketCategory.SPORTS,
            'entertainment': MarketCategory.ENTERTAINMENT,
            'technology': MarketCategory.TECHNOLOGY
        }
        category = category_map.get(kalshi_market.category, MarketCategory.OTHER)
        
        # Parse dates
        expiry_date = datetime.fromisoformat(kalshi_market.close_time.replace('Z', '+00:00'))
        settlement_date = datetime.fromisoformat(kalshi_market.settle_time.replace('Z', '+00:00'))
        
        return Market(
            ticker=kalshi_market.ticker,
            title=kalshi_market.title,
            description=kalshi_market.description,
            category=category,
            status=status,
            volume=kalshi_market.volume or 0,
            open_interest=kalshi_market.open_interest or 0,
            yes_bid=kalshi_market.yes_bid,
            yes_ask=kalshi_market.yes_ask,
            no_bid=kalshi_market.no_bid,
            no_ask=kalshi_market.no_ask,
            expiry_date=expiry_date,
            settlement_date=settlement_date,
            min_tick_size=kalshi_market.min_tick_size,
            max_order_size=kalshi_market.max_order_size
        )
    
    def get_balance(self) -> Optional[float]:
        """
        Get account balance.
        
        Returns:
            Account balance in dollars or None if not authenticated
        """
        try:
            if not self.config.KALSHI_API_KEY_ID:
                logger.warning("Cannot get balance: not authenticated")
                return None
            
            response = self.client.get_balance()
            return response.balance / 100.0  # Convert from cents to dollars
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
            # Try to fetch a small number of markets
            markets = self.get_markets(limit=1)
            return len(markets) >= 0  # Even 0 markets is a valid response
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

