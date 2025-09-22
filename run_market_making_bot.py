#!/usr/bin/env python3
"""
Kalshi Market Making Bot Runner

This script provides a command-line interface to run the Kalshi Market Making Bot
with proper error handling, logging, and configuration management.

Usage:
    python run_market_making_bot.py [options]

Examples:
    # Run with default configuration
    python run_market_making_bot.py
    
    # Run with custom config file
    python run_market_making_bot.py --config my_config.yaml
    
    # Run in conservative mode
    python run_market_making_bot.py --mode conservative
    
    # Run with dry-run (no actual trades)
    python run_market_making_bot.py --dry-run
    
    # Run with debug logging
    python run_market_making_bot.py --log-level DEBUG
"""

import asyncio
import argparse
import logging
import signal
import sys
import os
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, setup_logging
from bot_config import BotConfigManager, TradingMode, MarketMakingConfig
from market_making_bot import KalshiMarketMakingBot

# Global bot instance for signal handling
bot_instance: Optional[KalshiMarketMakingBot] = None

def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    def signal_handler(signum, frame):
        """Handle shutdown signals."""
        global bot_instance
        if bot_instance:
            logger = logging.getLogger(__name__)
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            asyncio.create_task(bot_instance.stop())
        else:
            sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def validate_environment():
    """Validate that the environment is properly configured."""
    required_vars = [
        'KALSHI_API_KEY_ID',
        'KALSHI_PRIVATE_KEY_PATH'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease set the following environment variables:")
        for var in missing_vars:
            print(f"  export {var}=<value>")
        print("\nSee SETUP.md for detailed instructions.")
        sys.exit(1)
    
    # Check if private key file exists
    private_key_path = os.getenv('KALSHI_PRIVATE_KEY_PATH')
    if private_key_path and not os.path.exists(private_key_path):
        print(f"Error: Private key file not found: {private_key_path}")
        sys.exit(1)

def create_argument_parser() -> argparse.ArgumentParser:
    """Create command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Kalshi Market Making Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                           # Run with default configuration
  %(prog)s --config my_config.yaml   # Run with custom config file
  %(prog)s --mode conservative       # Run in conservative mode
  %(prog)s --dry-run                 # Run without placing actual orders
  %(prog)s --log-level DEBUG         # Run with debug logging
        """
    )
    
    # Configuration options
    parser.add_argument(
        '--config', '-c',
        type=str,
        help='Path to configuration file (YAML or JSON)'
    )
    
    parser.add_argument(
        '--mode', '-m',
        choices=['conservative', 'moderate', 'aggressive'],
        help='Trading mode preset'
    )
    
    # Bot behavior options
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run in dry-run mode (no actual orders will be placed)'
    )
    
    parser.add_argument(
        '--enabled',
        action='store_true',
        default=True,
        help='Enable the bot (default: True)'
    )
    
    parser.add_argument(
        '--disabled',
        action='store_true',
        help='Disable the bot (override --enabled)'
    )
    
    # Logging options
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )
    
    parser.add_argument(
        '--log-file',
        type=str,
        help='Log file path (default: logs/market_making_bot.log)'
    )
    
    # Risk management options
    parser.add_argument(
        '--max-daily-loss',
        type=float,
        help='Maximum daily loss in dollars'
    )
    
    parser.add_argument(
        '--max-position-per-market',
        type=int,
        help='Maximum position per market'
    )
    
    parser.add_argument(
        '--max-total-exposure',
        type=int,
        help='Maximum total exposure across all markets'
    )
    
    # Market selection options
    parser.add_argument(
        '--min-volume',
        type=int,
        help='Minimum daily volume for market selection'
    )
    
    parser.add_argument(
        '--max-spread-cents',
        type=int,
        help='Maximum spread in cents for market selection'
    )
    
    # Utility options
    parser.add_argument(
        '--validate-config',
        action='store_true',
        help='Validate configuration and exit'
    )
    
    parser.add_argument(
        '--show-config',
        action='store_true',
        help='Show current configuration and exit'
    )
    
    parser.add_argument(
        '--version',
        action='version',
        version='Kalshi Market Making Bot 1.0.0'
    )
    
    return parser

def apply_command_line_overrides(config: MarketMakingConfig, args) -> MarketMakingConfig:
    """Apply command line argument overrides to configuration."""
    # Bot behavior
    if args.disabled:
        config.enabled = False
    elif args.enabled:
        config.enabled = True
    
    if args.dry_run:
        config.enabled = False  # Disable bot in dry-run mode
        logging.getLogger(__name__).info("DRY-RUN MODE: Bot is disabled, no orders will be placed")
    
    # Logging
    config.log_level = args.log_level
    
    # Risk management
    if args.max_daily_loss is not None:
        config.risk_limits.max_daily_loss = args.max_daily_loss
    
    if args.max_position_per_market is not None:
        config.risk_limits.max_position_per_market = args.max_position_per_market
    
    if args.max_total_exposure is not None:
        config.risk_limits.max_total_exposure = args.max_total_exposure
    
    # Market selection
    if args.min_volume is not None:
        config.market_selection.min_volume = args.min_volume
    
    if args.max_spread_cents is not None:
        config.market_selection.max_spread_cents = args.max_spread_cents
    
    return config

async def run_bot(config: MarketMakingConfig, args):
    """Run the market making bot."""
    global bot_instance
    
    logger = logging.getLogger(__name__)
    
    try:
        # Create Kalshi API configuration
        kalshi_config = Config()
        
        # Create and start the bot
        logger.info("Initializing Market Making Bot...")
        bot_instance = KalshiMarketMakingBot(kalshi_config, config)
        
        if not config.enabled:
            logger.info("Bot is disabled, exiting...")
            return
        
        logger.info("Starting Market Making Bot...")
        logger.info(f"Trading mode: {config.trading_mode.value}")
        logger.info(f"Market side: {config.market_side.value}")
        logger.info(f"Max daily loss: ${config.risk_limits.max_daily_loss}")
        logger.info(f"Max position per market: {config.risk_limits.max_position_per_market}")
        logger.info(f"Max total exposure: {config.risk_limits.max_total_exposure}")
        
        if args.dry_run:
            logger.info("DRY-RUN MODE: No actual orders will be placed")
        
        # Start the bot
        await bot_instance.start()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, stopping bot...")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)
        raise
    finally:
        if bot_instance:
            logger.info("Stopping bot...")
            await bot_instance.stop()
            bot_instance = None

def main():
    """Main function."""
    # Parse command line arguments
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Validate environment
    validate_environment()
    
    # Setup logging
    log_file = args.log_file or f"logs/market_making_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    setup_logging(
        level=getattr(logging, args.log_level),
        include_filename=True
    )
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Kalshi Market Making Bot Starting")
    logger.info("=" * 60)
    logger.info(f"Log level: {args.log_level}")
    logger.info(f"Log file: {log_file}")
    logger.info(f"Start time: {datetime.now(timezone.utc)}")
    
    try:
        # Load configuration
        config_manager = BotConfigManager(args.config)
        
        if args.mode:
            # Use preset configuration
            trading_mode = TradingMode(args.mode)
            config = config_manager.get_preset_config(trading_mode)
            logger.info(f"Using preset configuration: {trading_mode.value}")
        else:
            # Load from file or environment
            config = config_manager.load_config()
            logger.info("Loaded configuration from file/environment")
        
        # Apply command line overrides
        config = apply_command_line_overrides(config, args)
        
        # Handle utility commands
        if args.validate_config:
            logger.info("Configuration validation successful")
            print("✅ Configuration is valid")
            return
        
        if args.show_config:
            import json
            print("Current configuration:")
            print(json.dumps(config.to_dict(), indent=2, default=str))
            return
        
        # Setup signal handlers
        setup_signal_handlers()
        
        # Run the bot
        asyncio.run(run_bot(config, args))
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"❌ Fatal error: {e}")
        sys.exit(1)
    
    logger.info("Market Making Bot stopped successfully")

if __name__ == "__main__":
    main()
