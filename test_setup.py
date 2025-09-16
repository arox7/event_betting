"""
Test script to verify the setup and API connection.
"""
import logging
from config import Config
from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_setup():
    """Test the basic setup and API connection."""
    logger.info("Testing Kalshi Market Making Bot setup...")
    
    try:
        # Initialize components
        config = Config()
        kalshi_client = KalshiAPIClient(config)
        screener = MarketScreener(kalshi_client, config)
        
        # Test API connection
        logger.info("Testing API connection...")
        if kalshi_client.health_check():
            logger.info("✅ API connection successful")
        else:
            logger.error("❌ API connection failed")
            return False
        
        # Test authentication
        balance = kalshi_client.get_balance()
        if balance is not None:
            logger.info(f"✅ Authentication successful - Balance: ${balance:.2f}")
        else:
            logger.warning("⚠️ Not authenticated - running in read-only mode")
        
        # Test market fetching
        logger.info("Testing market fetching...")
        markets = kalshi_client.get_markets(limit=5, status="open")
        
        if markets:
            logger.info(f"✅ Successfully fetched {len(markets)} markets")
            
            # Test screening
            logger.info("Testing market screening...")
            results = screener.screen_markets(markets)
            
            if results:
                logger.info(f"✅ Successfully screened {len(results)} markets")
                profitable = len([r for r in results if r.is_profitable])
                logger.info(f"📊 Found {profitable} profitable opportunities")
                
                # Show top opportunity
                if profitable > 0:
                    top_opportunity = max(results, key=lambda x: x.score)
                    logger.info(f"🎯 Top opportunity: {top_opportunity.market.ticker} (Score: {top_opportunity.score:.2f})")
            else:
                logger.warning("⚠️ No screening results")
        else:
            logger.warning("⚠️ No markets found")
        
        logger.info("✅ Setup test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Setup test failed: {e}")
        return False

if __name__ == "__main__":
    test_setup()
