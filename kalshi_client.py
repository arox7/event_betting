"""
Kalshi API client using the official kalshi-python SDK.
"""
import logging
from multiprocessing import process
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import kalshi_python
import requests
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding

from config import Config
from models import Market, Event, MarketPosition
from pprint import pprint


logger = logging.getLogger(__name__)

class KalshiAPIClient:
    """Kalshi API client using the official kalshi-python SDK."""
    
    def __init__(self, config: Config):
        """Initialize the Kalshi API client."""
        self.config = config
        self.client = self._initialize_client()
        self._private_key = None
        self._load_private_key()
        
        
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
    
    def _make_authenticated_request(self, method: str, path: str, params: Optional[Dict] = None) -> requests.Response:
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
    
    def _get_event_from_market(self, market):
        """Get event data from market's event_ticker."""
        try:
            # For now, create a minimal but complete event from market data
            # In a full implementation, we'd fetch the actual event data
            event = Event(
                event_ticker=market.event_ticker,
                title=market.title,  # Market title contains the event context
                category=None,  # Category is optional
                markets=[],
                series_ticker=market.event_ticker,
                total_volume=getattr(market, 'volume', 0) or 0
            )
            return event
            
        except Exception as e:
            logger.error(f"Failed to create event from market data: {e}")
            return None
    
    def _preprocess_event_data(self, data, status: Optional[str] = None):
        """Recursively preprocess event data to handle known API inconsistencies."""
        markets = data.get('markets', [])
        cleaned = data.copy()
        markets_to_keep = []
        for market in markets:
            processed_market = self._is_market_valid(market, status)
            if processed_market:
                markets_to_keep.append(market)
        cleaned['markets'] = markets_to_keep
        return cleaned

    def _is_market_valid(self, data, status: Optional[str] = None):
        market_status = data.get('status')
        valid_statuses = {'initialized', 'active', 'closed', 'settled', 'determined'}
        if market_status not in valid_statuses:
            return False

        if status is not None:
            if status == 'open' and market_status not in ["active", "open"]:
                return False
            elif status != 'open' and market_status != status:
                return False
        
        return True
    
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
                    if self._is_market_valid(market_dict):
                        markets.append(Market.model_validate(market_dict, strict=False))
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
                        cleaned_event_dict = self._preprocess_event_data(event_raw, status)
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
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for direct API calls."""
        # The Kalshi SDK handles authentication, we can reuse its token
        headers = {}
        if hasattr(self.client.api_client.configuration, 'access_token'):
            headers['Authorization'] = f'Bearer {self.client.api_client.configuration.access_token}'
        return headers
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Market]:
        """Fetch a specific market by ticker using direct HTTP requests."""
        if not self.client:
            logger.error("Client not initialized")
            return None
            
        try:
            # Make direct API call
            url = f"{self.client.api_client.configuration.host}/markets/{ticker}"
            headers = self._get_auth_headers()
            
            response = requests.get(url, headers=headers)
            
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
                return market
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
    
    def get_balance_dollars(self) -> Optional[float]:
        """Get account balance in dollars using raw HTTP requests."""
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            logger.error("API credentials not properly configured")
            return None
            
        try:
            # Make authenticated request
            path = "/portfolio/balance"
            response = self._make_authenticated_request("GET", path)
            
            if response.status_code != 200:
                logger.error(f"API call failed: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            balance_cents = data['balance']
            balance_dollars = balance_cents / 100.0  # Convert cents to dollars
            
            return balance_dollars
            
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
    
    def get_balance(self) -> Optional[float]:
        """Backward compatibility alias for get_balance_dollars()."""
        return self.get_balance_dollars()
    
    def get_all_positions(self) -> Optional[Dict[str, Any]]:
        """Get all portfolio positions using raw HTTP requests with pagination."""
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            logger.error("API credentials not properly configured")
            return None
            
        try:
            all_market_positions = []
            all_event_positions = []
            cursor = None
            
            while True:
                # Build request parameters
                params = {
                    'limit': 200,
                    'settlement_status': 'all',
                    'count_filter': "position,total_traded,resting_order_count"
                }
                if cursor:
                    params['cursor'] = cursor
                
                # Make authenticated request
                path = "/portfolio/positions"
                response = self._make_authenticated_request("GET", path, params)
                
                if response.status_code != 200:
                    logger.error(f"API call failed: {response.status_code} - {response.text}")
                    return None
                
                data = response.json()
                market_positions = data.get('market_positions', [])
                event_positions = data.get('event_positions', [])
                
                # Add to our collections
                all_market_positions.extend(market_positions)
                all_event_positions.extend(event_positions)
                
                # Check if there are more pages
                cursor = data.get('cursor')
                if not cursor:
                    break
            
            # Filter client-side for positions with actual position != 0
            # The API count_up/count_down parameters might include resting orders
            active_positions = [pos for pos in all_market_positions if pos.get('position', 0) != 0]
            
            result = {
                'active_positions': active_positions,  # Only positions with actual holdings
                'all_market_positions': all_market_positions,  # All market positions from API
                'all_event_positions': all_event_positions,    # Event-level position data
                'cursor': None  # No cursor since we got everything
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get unsettled positions: {e}")
            return None
    
    def get_settled_positions(self) -> Optional[Dict[str, Any]]:
        """Get settled portfolio positions using raw HTTP requests with pagination."""
        if not self.config.KALSHI_API_KEY_ID or not self._private_key:
            logger.error("API credentials not properly configured")
            return None
            
        try:
            all_settlements = []
            cursor = None
            
            while True:
                # Build request parameters
                params = {
                    'limit': 200,
                }
                if cursor:
                    params['cursor'] = cursor
                
                # Make authenticated request
                path = "/portfolio/settlements"
                response = self._make_authenticated_request("GET", path, params)
                
                if response.status_code != 200:
                    logger.error(f"API call failed: {response.status_code} - {response.text}")
                    return None
                
                data = response.json()
                settlements = data.get('settlements', [])
                
                # Add to our collections
                all_settlements.extend(settlements)
                
                # Check if there are more pages
                cursor = data.get('cursor')
                if not cursor:
                    break
            
            result = {
                'all_settlements': all_settlements,  # All settlements from API
                'cursor': None  # No cursor since we got everything
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get settled positions: {e}")
            return None
    
    
    def get_enriched_positions(self) -> Optional[List[Dict[str, Any]]]:
        """Get positions enriched with market and event data."""
        try:
            # Get positions
            positions_data = self.get_all_positions()
            if not positions_data:
                return None
            
            positions = positions_data.get('all_market_positions', [])
            if not positions:
                return []
            
            # Extract unique tickers from positions that have actual holdings (position != 0)
            positions_with_holdings = [pos for pos in positions if pos.get('position', 0) != 0]
            position_tickers = set(pos.get('ticker') for pos in positions_with_holdings if pos.get('ticker'))
            
            if not position_tickers:
                logger.warning("No valid tickers found in positions")
                return []
            
            logger.info(f"Fetching market data for {len(position_tickers)} position tickers")
            
            # Create lookup dictionaries
            market_lookup = {}
            
            # Get market data for each ticker we have positions in
            for ticker in position_tickers:
                try:
                    market = self.get_market_by_ticker(ticker)
                    if market:
                        # Every market MUST have an event - get it from the event_ticker
                        if not hasattr(market, 'event_ticker') or not market.event_ticker:
                            logger.error(f"Market {ticker} missing event_ticker - this should not happen")
                            continue
                        
                        # Get the actual event data - we need to implement this properly
                        event = self._get_event_from_market(market)
                        if not event:
                            logger.error(f"Failed to get event data for market {ticker} - this should not happen")
                            continue
                        
                        market_lookup[ticker] = {
                            'market': market,
                            'event': event
                        }
                    else:
                        logger.warning(f"Could not find market data for ticker: {ticker}")
                except Exception as e:
                    logger.warning(f"Failed to get market data for {ticker}: {e}")
                    continue
            
            # Enrich positions with market data (only those with actual holdings)
            enriched_positions = []
            for position in positions_with_holdings:
                ticker = position.get('ticker')
                if not ticker:
                    continue
                
                market_info = market_lookup.get(ticker)
                if market_info:
                    enriched_position = {
                        'market': market_info['market'],
                        'event': market_info['event'],
                        'ticker': ticker,
                        'quantity': position['position'],
                        'position': position['position'],
                        'market_value': position['market_exposure'],  # Kalshi uses market_exposure
                        'total_cost': position['total_traded'],  # Use total_traded as cost basis
                        'unrealized_pnl': 0,  # Calculate this based on current market price vs cost
                        'realized_pnl': position['realized_pnl']
                    }
                    enriched_positions.append(enriched_position)
                else:
                    # Position without matching market (might be settled/closed)
                    enriched_position = {
                        'market': None,
                        'event': None,
                        'ticker': ticker,
                        'quantity': position['position'],
                        'position': position['position'],
                        'market_value': position['market_exposure'],  # Kalshi uses market_exposure
                        'total_cost': position['total_traded'],  # Use total_traded as cost basis
                        'realized_pnl': position['realized_pnl']
                    }
                    enriched_positions.append(enriched_position)
            
            return enriched_positions
            
        except Exception as e:
            logger.error(f"Failed to get enriched positions: {e}")
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
    
    def filter_market_positions_by_date(self, market_positions: List[Dict[str, Any]], start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Filter market positions by date range based on last_updated_ts."""
        if not start_date and not end_date:
            return market_positions
        
        filtered_positions = []
        for pos in market_positions:
            try:
                # Parse the timestamp
                last_updated_str = pos['last_updated_ts']
                if last_updated_str.endswith('Z'):
                    last_updated_str = last_updated_str[:-1] + '+00:00'
                
                pos_datetime = datetime.fromisoformat(last_updated_str)
                
                # Filter by date range (compare dates, not datetime objects)
                if start_date:
                    # Convert start_date to date if it's a datetime
                    start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
                    if pos_datetime.date() < start_date_only:
                        continue
                if end_date:
                    # Convert end_date to date if it's a datetime
                    end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
                    if pos_datetime.date() > end_date_only:
                        continue
                
                filtered_positions.append(pos)
            except Exception as e:
                logger.warning(f"Error parsing date for position {pos.get('ticker', 'Unknown')}: {e}")
                continue
        
        return filtered_positions

    def get_portfolio_metrics(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """Get comprehensive portfolio metrics including cash, positions, and P&L with optional date filtering."""
        try:
            # Get cash balance (in dollars)
            cash_balance_dollars = self.get_balance_dollars()
            if cash_balance_dollars is None:
                return None
            
            # Get all positions data (includes both active and market positions)
            all_positions_data = self.get_all_positions()
            if all_positions_data is None:
                return None
            
            # Get enriched positions for detailed calculations (active positions only)
            enriched_positions = self.get_enriched_positions()
            if enriched_positions is None:
                enriched_positions = []
            
            # Extract market positions (all positions including closed ones)
            all_market_positions = all_positions_data['all_market_positions']
            
            # Apply date filtering to market positions if dates are provided
            if start_date or end_date:
                filtered_market_positions = self.filter_market_positions_by_date(all_market_positions, start_date, end_date)
            else:
                filtered_market_positions = all_market_positions
            
            # Calculate metrics from enriched positions (active positions only)
            total_active_positions = len(enriched_positions)
            
            # Market value calculations (convert from cents to dollars)
            total_market_value_dollars = sum(abs(pos['market_value']) for pos in enriched_positions) / 100.0
            total_unrealized_pnl_dollars = sum(pos['unrealized_pnl'] for pos in enriched_positions) / 100.0
            
            # Calculate realized P&L from filtered market positions, accounting for fees
            total_realized_pnl_cents = 0
            total_fees_paid_cents = 0
            
            for pos in filtered_market_positions:
                realized_pnl_cents = pos['realized_pnl']  # Already in cents
                fees_paid_cents = pos['fees_paid']  # Already in cents
                total_realized_pnl_cents += realized_pnl_cents
                total_fees_paid_cents += fees_paid_cents
            
            # Net realized P&L after fees (convert to dollars)
            total_realized_pnl_dollars = (total_realized_pnl_cents - total_fees_paid_cents) / 100.0
            total_fees_paid_dollars = total_fees_paid_cents / 100.0
            
            # Calculate portfolio totals
            total_portfolio_value_dollars = cash_balance_dollars + total_market_value_dollars
            
            # Calculate win/loss metrics from active positions
            winning_positions = len([pos for pos in enriched_positions if pos['unrealized_pnl'] > 0])
            losing_positions = len([pos for pos in enriched_positions if pos['unrealized_pnl'] < 0])
            win_rate = (winning_positions / total_active_positions) * 100 if total_active_positions > 0 else 0
            portfolio_return = (total_unrealized_pnl_dollars / total_market_value_dollars) * 100 if total_market_value_dollars > 0 else 0
            
            # Calculate closed positions from filtered data
            closed_positions = [pos for pos in filtered_market_positions if pos['position'] == 0 and pos['total_traded'] > 0]
            
            return {
                'cash_balance': cash_balance_dollars,  # In dollars
                'total_market_value': total_market_value_dollars,  # In dollars
                'total_portfolio_value': total_portfolio_value_dollars,  # In dollars
                'total_unrealized_pnl': total_unrealized_pnl_dollars,  # In dollars
                'total_realized_pnl': total_realized_pnl_dollars,  # In dollars (after fees)
                'total_fees_paid': total_fees_paid_dollars,  # In dollars
                'total_positions': total_active_positions,
                'winning_positions': winning_positions,
                'losing_positions': losing_positions,
                'win_rate': win_rate,
                'portfolio_return': portfolio_return,
                'enriched_positions': enriched_positions,
                'market_positions': all_market_positions,  # All market positions (unfiltered)
                'filtered_market_positions': filtered_market_positions,  # Date-filtered market positions
                'closed_positions': closed_positions,  # Closed positions from filtered data
                'total_filtered_positions': len(filtered_market_positions),
                'total_closed_positions': len(closed_positions),
                'date_range_start': start_date,
                'date_range_end': end_date
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio metrics: {e}")
            return None
  
    def health_check(self) -> bool:
        """Check if the API client is working properly."""
        try:
            if not self.client:
                return False
            # Try to get balance as a simple health check
            balance = self.get_balance_dollars()
            return balance is not None
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
