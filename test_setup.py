"""
Test script to verify the setup and API connection.

Note: If you see Streamlit warnings about missing ScriptRunContext, these can be safely ignored.
These warnings occur because some modules import Streamlit components, but we're running
outside of the Streamlit context. The core functionality is still tested properly.
"""
import logging
from config import Config, setup_logging
from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
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
        
        # Test new portfolio metrics functionality
        logger.info("📊 Testing portfolio metrics...")
        portfolio_metrics = kalshi_client.get_portfolio_metrics()
        
        if portfolio_metrics:
            cash_balance = portfolio_metrics.get('cash_balance', 0)
            total_market_value = portfolio_metrics.get('total_market_value', 0)
            total_positions = portfolio_metrics.get('total_positions', 0)
            winning_positions = portfolio_metrics.get('winning_positions', 0)
            losing_positions = portfolio_metrics.get('losing_positions', 0)
            win_rate = portfolio_metrics.get('win_rate', 0)
            
            logger.info(f"✅ Portfolio metrics loaded successfully:")
            logger.info(f"   💰 Cash Balance: ${cash_balance:.2f}")
            logger.info(f"   📊 Market Value: ${total_market_value:.2f}")
            logger.info(f"   📈 Total Positions: {total_positions}")
            logger.info(f"   🏆 Win Rate: {win_rate:.1f}% ({winning_positions} winners, {losing_positions} losers)")
            
            # Test consistency between old and new methods
            old_summary = kalshi_client.get_portfolio_summary()
            if old_summary:
                old_position_value = old_summary.get('total_position_value', 0)
                new_position_value = total_market_value
                
                if abs(old_position_value - new_position_value) < 0.01:  # Allow for small rounding differences
                    logger.info("✅ Portfolio value calculations are consistent between methods")
                else:
                    logger.warning(f"⚠️ Portfolio value mismatch: old={old_position_value:.2f}, new={new_position_value:.2f}")
        else:
            logger.warning("⚠️ Could not load portfolio metrics")
        
        # Test enriched positions
        logger.info("🔍 Testing enriched positions...")
        enriched_positions = kalshi_client.get_enriched_positions()
        
        if enriched_positions:
            logger.info(f"✅ Loaded {len(enriched_positions)} enriched positions")
            
            # Show sample position details
            if enriched_positions:
                sample_pos = enriched_positions[0]
                ticker = sample_pos.get('ticker', 'Unknown')
                market_value = abs(sample_pos.get('market_value', 0)) / 100.0
                quantity = sample_pos.get('quantity', 0)
                has_market_data = sample_pos.get('market') is not None
                has_event_data = sample_pos.get('event') is not None
                
                logger.info(f"   📋 Sample position: {ticker}")
                logger.info(f"      💵 Market Value: ${market_value:.2f}")
                logger.info(f"      📊 Quantity: {quantity}")
                logger.info(f"      🏪 Market Data: {'✅' if has_market_data else '❌'}")
                logger.info(f"      📅 Event Data: {'✅' if has_event_data else '❌'}")
        else:
            logger.info("ℹ️ No enriched positions found (this is normal if you have no positions)")
        
    except Exception as e:
        logger.error(f"❌ Setup test failed: {e}")
        return False

if __name__ == "__main__":
    test_setup()