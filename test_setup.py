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
        
        # Show environment info
        env_mode = "DEMO" if config.KALSHI_DEMO_MODE else "PRODUCTION"
        logger.info(f"🌐 Using {env_mode} environment")
        
        kalshi_client = KalshiAPIClient(config)
        screener = MarketScreener(kalshi_client, config)
        
        # Test API connection
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
            logger.warning("⚠️ Authentication failed - check your API credentials")
        
        # Test market fetching and screening
        markets = kalshi_client.get_markets(limit=5, status="open")
        
        if markets:
            results = screener.screen_markets(markets)
            
            if results:
                passing = len([r for r in results if r.score > 0])
                logger.info(f"✅ Screened {len(results)} markets - {passing} opportunities found")
                
                # Show top opportunity
                if passing > 0:
                    top_opportunity = max(results, key=lambda x: x.score)
                    logger.info(f"🎯 Top: {top_opportunity.market.ticker} (Score: {top_opportunity.score:.2f})")
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
