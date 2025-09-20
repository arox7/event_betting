"""
Kalshi API client using the official kalshi-python SDK.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import kalshi_python

from models import Market, Event
from config import Config


logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Kalshi API client using the official kalshi-python SDK."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.client = self._initialize_client()
        
    def _initialize_client(self):
        """Initialize the official Kalshi client."""
        if not (self.config.KALSHI_API_KEY_ID and self.config.KALSHI_PRIVATE_KEY_PATH):
            logger.warning("No API credentials provided - client will not work for authenticated endpoints")
            return None
            
        try:
            # Configure the official SDK
            configuration = kalshi_python.Configuration(
                host=self.config.KALSHI_DEMO_HOST if self.config.KALSHI_DEMO_MODE 
                     else self.config.KALSHI_API_HOST
            )
            
            # Read private key from file
            with open(self.config.KALSHI_PRIVATE_KEY_PATH, 'r') as f:
                private_key = f.read()
            
            configuration.api_key_id = self.config.KALSHI_API_KEY_ID
            configuration.private_key_pem = private_key
            
            return kalshi_python.KalshiClient(configuration)
            
        except Exception as e:
            logger.error(f"Failed to initialize Kalshi client: {e}")
            raise
    
    def _preprocess_market_data(self, data):
        """Recursively preprocess market data to handle known API inconsistencies."""
        if isinstance(data, dict):
            # Create a copy to avoid modifying the original
            cleaned = data.copy()
            
            # Handle status field - map non-standard values to valid enum values
            status = cleaned.get('status')
            valid_statuses = {'initialized', 'active', 'closed', 'settled', 'determined'}
            if status and status not in valid_statuses:
                logger.info(f"Converting non-standard status '{status}' to 'closed' for ticker: {cleaned.get('ticker', 'unknown')}")
                cleaned['status'] = 'closed'
            
            # Recursively clean nested structures
            for key, value in cleaned.items():
                cleaned[key] = self._preprocess_market_data(value)
            
            return cleaned
        elif isinstance(data, list):
            return [self._preprocess_market_data(item) for item in data]
        else:
            return data
    
    def get_markets(self, limit: int = 100, status: Optional[str] = None) -> List[Market]:
        """Fetch markets from Kalshi API."""
        if not self.client:
            logger.error("Client not initialized")
            return []
            
        try:
            response = self.client.get_markets(limit=limit, status=status)
            markets = []
            
            for market_data in response.markets or []:
                try:
                    # Convert the SDK response to our Market model
                    market_dict = market_data.to_dict()
                    
                    # Preprocess market data to handle known issues
                    cleaned_market_dict = self._preprocess_market_data(market_dict)
                    
                    markets.append(Market.model_validate(cleaned_market_dict, strict=False))
                except Exception as e:
                    ticker = getattr(market_data, 'ticker', 'unknown')
                    logger.warning(f"Skipping invalid market {ticker}: {e}")
                    continue
            
            return markets
            
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []
    
    def get_events(self, limit: int = 100, status: Optional[str] = None, max_events: Optional[int] = None) -> List[Event]:
        """Fetch events from Kalshi API using direct HTTP calls with nested markets."""
        if not self.client:
            logger.error("Client not initialized")
            return []
            
        try:
            import requests
            
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
                url = f"{self.client.api_client.configuration.host}/events"
                headers = self._get_auth_headers()
                
                response = requests.get(url, headers=headers, params=params)
                
                if response.status_code != 200:
                    logger.error(f"API call failed: {response.status_code} - {response.text}")
                    break
                
                data = response.json()
                
                # Process events from this batch
                for event_raw in data.get('events', []):
                    try:
                        # Preprocess to handle status validation issues
                        cleaned_event_dict = self._preprocess_market_data(event_raw)
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
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for direct API calls."""
        # The Kalshi SDK handles authentication, we can reuse its token
        headers = {}
        if hasattr(self.client.api_client.configuration, 'access_token'):
            headers['Authorization'] = f'Bearer {self.client.api_client.configuration.access_token}'
        return headers
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Market]:
        """Fetch a specific market by ticker."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_market(ticker=ticker)
            if response.market:
                market_dict = response.market.to_dict()
                return Market.model_validate(market_dict)
            return None
        except Exception as e:
            logger.error(f"Failed to fetch market {ticker}: {e}")
            return None
    
    def get_market_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch orderbook for a specific market."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_market_orderbook(ticker=ticker)
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
    
    def get_balance(self) -> Optional[float]:
        """Get account balance in dollars."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_balance()
            return response.balance / 100.0  # Convert cents to dollars
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def get_positions(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio positions."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_positions(limit=limit, cursor=cursor)
            return {
                'positions': [pos.to_dict() for pos in response.positions] if response.positions else [],
                'cursor': response.cursor
            }
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return None
    
    def get_settlements(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio settlements."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_settlements(limit=limit, cursor=cursor)
            return {
                'settlements': [settlement.to_dict() for settlement in response.settlements] if response.settlements else [],
                'cursor': response.cursor
            }
        except Exception as e:
            logger.error(f"Failed to get settlements: {e}")
            return None
    
    def get_fills(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get portfolio fills (trade history)."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            response = self.client.get_fills(limit=limit, cursor=cursor)
            return {
                'fills': response.fills or [],
                'cursor': response.cursor
            }
        except Exception as e:
            logger.error(f"Failed to get fills: {e}")
            return None
    
    def get_portfolio_summary(self) -> Optional[Dict[str, Any]]:
        """Get comprehensive portfolio summary including positions and PnL."""
        try:
            # Get cash balance
            cash_balance = self.get_balance()
            if cash_balance is None:
                return None
            
            # Get positions
            positions_data = self.get_positions(limit=200)
            if positions_data is None:
                return None
            
            positions = positions_data.get('positions', [])
            
            # Calculate position values
            total_position_value = 0
            total_unrealized_pnl = 0
            position_count = len(positions)
            
            for position in positions:
                # Position value = quantity * current_price
                quantity = position.get('position', 0)
                # For Kalshi, positions are in cents, convert to dollars
                position_value_cents = abs(quantity)  # Absolute value for total exposure
                position_value = position_value_cents / 100.0
                total_position_value += position_value
                
                # Unrealized PnL (if available in the response)
                unrealized_pnl_cents = position.get('unrealized_pnl', 0)
                total_unrealized_pnl += unrealized_pnl_cents / 100.0
            
            total_balance = cash_balance + total_position_value
            
            return {
                'cash_balance': cash_balance,
                'total_position_value': total_position_value,
                'total_balance': total_balance,
                'unrealized_pnl': total_unrealized_pnl,
                'position_count': position_count,
                'positions': positions
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return None
    
    def get_recent_pnl(self, hours: int = 24) -> Optional[Dict[str, Any]]:
        """Get realized P&L from recent trading activity."""
        try:
            from datetime import datetime, timezone, timedelta
            
            # Get recent fills to calculate realized PnL
            fills_data = self.get_fills(limit=200)
            if fills_data is None:
                return {'realized_pnl': 0, 'trade_count': 0, 'trades': []}
            
            fills = fills_data.get('fills', [])
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            recent_fills = []
            total_realized_pnl = 0
            
            for fill in fills:
                # fill is already a Fill object from the SDK, not a dict
                if not fill.created_time:
                    continue
                    
                try:
                    if fill.created_time >= cutoff_time:
                        recent_fills.append(fill)
                        
                        # Extract fill details - these are already the correct types
                        ticker = fill.ticker
                        side = fill.side  # 'yes' or 'no'
                        count = fill.count or 0
                        price = fill.price or 0  # in cents
                        
                        if not ticker or count == 0:
                            continue
                        
                        # Calculate trade value in dollars
                        trade_value = (count * price) / 100.0
                        
                        # Calculate realized P&L from trading activity
                        # Buying costs money (negative P&L), selling generates revenue (positive P&L)
                        if side == 'yes':
                            # Bought Yes shares - this is a cost
                            total_realized_pnl -= trade_value
                        else:
                            # Selling positions or buying No shares - treat as revenue
                            total_realized_pnl += trade_value
                            
                except Exception as e:
                    logger.warning(f"Failed to parse fill: {e}")
                    continue
            
            # Calculate some basic stats
            trade_volume = sum(((fill.count or 0) * (fill.price or 0)) / 100.0 for fill in recent_fills)
            
            return {
                'realized_pnl': total_realized_pnl,
                'trade_count': len(recent_fills),
                'trade_volume': trade_volume,
                'recent_fills': [fill.to_dict() for fill in recent_fills[:10]]  # Return last 10 fills for display
            }
            
        except Exception as e:
            logger.error(f"Failed to get recent PnL: {e}")
            return {'realized_pnl': 0, 'trade_count': 0, 'trades': []}
    
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            if not self.client:
                return False
            # Try to get balance as a simple health check
            balance = self.get_balance()
            return balance is not None
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
