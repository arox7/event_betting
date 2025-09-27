"""
Markets ETL - Extract, Transform, Load for Kalshi market data.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from kalshi.http_client import KalshiHTTPClient
from kalshi.market_functions import get_market_by_ticker, get_markets
from database.models import TradesDatabase
from config import Config, setup_logging

logger = logging.getLogger(__name__)

class MarketsETL:
    """ETL class for fetching and storing Kalshi market data."""
    
    def __init__(self):
        """Initialize ETL with configuration and database."""
        self.config = Config()
        self.kalshi_client = KalshiHTTPClient(self.config)
        self.db = TradesDatabase()
    
    def fetch_and_store_market(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch a single market by ticker and store it in the database.
        
        Args:
            ticker: Market ticker to fetch
            
        Returns:
            Dict with success status and market data
        """
        try:
            logger.info(f"Fetching market data for ticker: {ticker}")
            
            # Fetch market from Kalshi API
            market = get_market_by_ticker(self.kalshi_client, ticker)
            
            if not market:
                return {
                    'success': False,
                    'error': f'Market not found for ticker: {ticker}',
                    'ticker': ticker
                }
            
            # Extract required fields
            market_data = {
                'ticker': getattr(market, 'ticker', ''),
                'event_ticker': getattr(market, 'event_ticker', ''),
                'subtitle': getattr(market, 'subtitle', ''),
                'status': getattr(market, 'status', ''),
                'category': getattr(market, 'category', ''),
                'title': getattr(market, 'title', '')
            }
            
            # Store in database
            success = self.db.insert_market(market_data)
            
            if success:
                logger.info(f"Successfully stored market: {ticker}")
                return {
                    'success': True,
                    'ticker': ticker,
                    'market_data': market_data
                }
            else:
                return {
                    'success': False,
                    'error': f'Failed to store market data for ticker: {ticker}',
                    'ticker': ticker
                }
                
        except Exception as e:
            logger.error(f"Error fetching market {ticker}: {e}")
            return {
                'success': False,
                'error': str(e),
                'ticker': ticker
            }
    
    def fetch_and_store_multiple_markets(self, tickers: List[str]) -> Dict[str, Any]:
        """
        Fetch multiple markets by ticker and store them in the database.
        
        Args:
            tickers: List of market tickers to fetch
            
        Returns:
            Dict with success status and results for each ticker
        """
        results = {
            'success': True,
            'total_tickers': len(tickers),
            'successful': 0,
            'failed': 0,
            'results': []
        }
        
        for ticker in tickers:
            result = self.fetch_and_store_market(ticker)
            results['results'].append(result)
            
            if result['success']:
                results['successful'] += 1
            else:
                results['failed'] += 1
        
        # Overall success if at least one market was fetched successfully
        results['success'] = results['successful'] > 0
        
        logger.info(f"Markets ETL completed: {results['successful']} successful, {results['failed']} failed")
        return results
    
    def fetch_and_store_all_markets(self, limit: int = None) -> Dict[str, Any]:
        """
        Fetch all available markets and store them in the database using pagination.
        
        Args:
            limit: Maximum number of markets to fetch (None = all)
            
        Returns:
            Dict with success status and results
        """
        try:
            logger.info(f"Fetching all markets with pagination (limit: {limit if limit else 'unlimited'})")
            
            # Use direct HTTP API to bypass SDK validation issues
            import requests
            import time
            from kalshi.shared_utils import get_base_api_url
            
            url = f"{get_base_api_url(self.kalshi_client)}/markets"
            
            all_markets = []
            cursor = None
            page_count = 0
            total_fetched = 0
            
            while True:
                page_count += 1
                logger.info(f"Fetching page {page_count} (cursor: {cursor[:20] if cursor else 'None'}...)")
                
                # Build request parameters
                params = {'limit': 1000}  # Max per page
                if cursor:
                    params['cursor'] = cursor
                
                # Retry logic for API calls
                max_retries = 3
                retry_delay = 2.0
                response = None
                
                for attempt in range(max_retries):
                    try:
                        response = requests.get(url, params=params, timeout=30)
                        
                        if response.status_code == 200:
                            break
                        elif response.status_code in [502, 503, 504]:  # Server errors
                            if attempt < max_retries - 1:
                                logger.warning(f"API call failed with {response.status_code}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                                time.sleep(retry_delay)
                                retry_delay *= 2  # Exponential backoff
                                continue
                            else:
                                return {
                                    'success': False,
                                    'error': f'API call failed after {max_retries} attempts: {response.status_code} - {response.text}',
                                    'markets_fetched': total_fetched,
                                    'markets_stored': 0
                                }
                        else:
                            return {
                                'success': False,
                                'error': f'API call failed: {response.status_code} - {response.text}',
                                'markets_fetched': total_fetched,
                                'markets_stored': 0
                            }
                    except requests.exceptions.RequestException as e:
                        if attempt < max_retries - 1:
                            logger.warning(f"Request failed: {e}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                        else:
                            return {
                                'success': False,
                                'error': f'Request failed after {max_retries} attempts: {e}',
                                'markets_fetched': total_fetched,
                                'markets_stored': 0
                            }
                
                # Parse response
                data = response.json()
                markets_data = data.get('markets', [])
                cursor = data.get('cursor')
                
                if not markets_data:
                    logger.info("No more markets in response, pagination complete")
                    break
                
                all_markets.extend(markets_data)
                total_fetched += len(markets_data)
                
                logger.info(f"Page {page_count}: {len(markets_data)} markets (total so far: {total_fetched})")
                
                # Check if we've reached the limit
                if limit and total_fetched >= limit:
                    logger.info(f"Reached limit of {limit} markets")
                    all_markets = all_markets[:limit]
                    total_fetched = limit
                    break
                
                # Check if there's no more data
                if not cursor:
                    logger.info("No cursor returned, pagination complete")
                    break
                
                # Small delay between requests to be respectful
                time.sleep(0.1)
            
            if not all_markets:
                return {
                    'success': False,
                    'error': 'No markets returned from API',
                    'markets_fetched': 0,
                    'markets_stored': 0
                }
            
            # Store each market in database
            stored_count = 0
            for market_dict in all_markets:
                market_data = {
                    'ticker': market_dict.get('ticker', ''),
                    'event_ticker': market_dict.get('event_ticker', ''),
                    'subtitle': market_dict.get('subtitle', ''),
                    'status': market_dict.get('status', ''),
                    'category': market_dict.get('category', ''),
                    'title': market_dict.get('title', '')
                }
                
                if self.db.insert_market(market_data):
                    stored_count += 1
            
            logger.info(f"Successfully stored {stored_count} out of {total_fetched} markets across {page_count} pages")
            
            return {
                'success': True,
                'markets_fetched': total_fetched,
                'markets_stored': stored_count,
                'pages_fetched': page_count
            }
            
        except Exception as e:
            logger.error(f"Error fetching all markets: {e}")
            return {
                'success': False,
                'error': str(e),
                'markets_fetched': 0,
                'markets_stored': 0
            }

def main():
    """Main function for testing the markets ETL."""
    setup_logging()
    
    etl = MarketsETL()
    
    # Test with a single market
    print("Testing single market fetch...")
    result = etl.fetch_and_store_market('KXTIME-25-AI')
    print(f"Result: {result}")
    
    # Test with multiple markets
    print("\nTesting multiple markets fetch...")
    tickers = ['KXTIME-25-AI', 'KXNCAAFGAME-25SEP27USUVAN-VAN']
    result = etl.fetch_and_store_multiple_markets(tickers)
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
