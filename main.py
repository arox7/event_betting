"""
Main application for the Kalshi market making bot.
"""
import logging
import signal
import sys
import time
import subprocess
import os
from datetime import datetime

from config import Config
from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener
from scheduler import MarketScheduler, MarketDataCollector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('market_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class MarketMakingBot:
    """Main application class for the market making bot."""
    
    def __init__(self):
        """Initialize the bot."""
        self.config = Config()
        self.kalshi_client = KalshiAPIClient(self.config)
        self.screener = MarketScreener(self.kalshi_client, self.config)
        self.scheduler = MarketScheduler(self.kalshi_client, self.screener)
        self.data_collector = MarketDataCollector()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Add data collector as callback
        self.scheduler.add_callback(self.data_collector.add_results)
        
        self.running = False
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.stop()
    
    def start(self):
        """Start the bot."""
        logger.info("Starting Kalshi Market Making Bot...")
        
        # Check API connection
        if not self.kalshi_client.health_check():
            logger.error("Failed to connect to Kalshi API. Please check your configuration.")
            return False
        
        logger.info("✅ Connected to Kalshi API")
        
        # Check authentication
        balance = self.kalshi_client.get_balance()
        if balance is not None:
            logger.info(f"✅ Authenticated - Account balance: ${balance:.2f}")
        else:
            logger.warning("⚠️ Not authenticated - running in read-only mode")
        
        # Start scheduler
        self.scheduler.start(interval_seconds=self.config.MARKET_UPDATE_INTERVAL)
        self.running = True
        
        logger.info("🚀 Bot started successfully")
        
        # Main loop
        self._main_loop()
        
        return True
    
    def stop(self):
        """Stop the bot."""
        if not self.running:
            return
        
        logger.info("Stopping bot...")
        self.running = False
        self.scheduler.stop()
        logger.info("Bot stopped")
    
    def _main_loop(self):
        """Main application loop."""
        last_stats_time = time.time()
        
        while self.running:
            try:
                # Print statistics every 60 seconds
                if time.time() - last_stats_time >= 60:
                    self._print_statistics()
                    last_stats_time = time.time()
                
                time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(5)  # Wait before retrying
    
    def _print_statistics(self):
        """Print current statistics."""
        stats = self.scheduler.get_statistics()
        summary = self.data_collector.get_summary_stats(hours=1)
        
        logger.info(f"📈 Status: {'Running' if stats['is_running'] else 'Stopped'} | Runs: {stats['total_runs']} | Success: {stats['success_rate']:.1%} | Avg opportunities: {summary['avg_opportunities_per_cycle']:.1f}")
    
    def run_dashboard(self):
        """Run the Streamlit dashboard."""
        
        logger.info("Starting dashboard...")
        
        # Set environment variables for Streamlit
        env = os.environ.copy()
        env['STREAMLIT_SERVER_PORT'] = str(self.config.DASHBOARD_PORT)
        env['STREAMLIT_SERVER_ADDRESS'] = self.config.DASHBOARD_HOST
        
        try:
            subprocess.run([
                sys.executable, '-m', 'streamlit', 'run', 'dashboard.py',
                '--server.port', str(self.config.DASHBOARD_PORT),
                '--server.address', self.config.DASHBOARD_HOST
            ], env=env)
        except KeyboardInterrupt:
            logger.info("Dashboard stopped")
        except Exception as e:
            logger.error(f"Error running dashboard: {e}")

def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Kalshi Market Making Bot')
    parser.add_argument('--mode', choices=['bot', 'dashboard'], default='bot',
                       help='Run mode: bot (screening) or dashboard (web interface)')
    parser.add_argument('--port', type=int, default=8501,
                       help='Dashboard port (default: 8501)')
    
    args = parser.parse_args()
    
    bot = MarketMakingBot()
    
    if args.mode == 'dashboard':
        bot.config.DASHBOARD_PORT = args.port
        bot.run_dashboard()
    else:
        try:
            bot.start()
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        finally:
            bot.stop()

if __name__ == "__main__":
    main()
