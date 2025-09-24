#!/usr/bin/env python3
"""
Example usage of the Market Making Bot

This script demonstrates how to use the MarketMakingListener class programmatically
instead of using the command line interface.
"""

import asyncio
import logging
from mm_ws_listener import MarketMakingListener

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def example_basic_usage():
    """Example of basic usage - public data only."""
    ticker = "KXEPSTEINLIST-26-HKIS"  # Replace with a valid ticker
    
    logger.info(f"Starting basic market making listener for {ticker}")
    
    listener = MarketMakingListener(ticker, with_private=False)
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await listener.stop()


async def example_with_private_data():
    """Example with private data (fills, positions)."""
    ticker = "KXEPSTEINLIST-26-HKIS"  # Replace with a valid ticker
    
    logger.info(f"Starting market making listener with private data for {ticker}")
    
    listener = MarketMakingListener(ticker, with_private=True)
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await listener.stop()


async def example_custom_handlers():
    """Example with custom event handlers."""
    ticker = "KXEPSTEINLIST-26-HKIS"  # Replace with a valid ticker
    
    logger.info(f"Starting market making listener with custom handlers for {ticker}")
    
    listener = MarketMakingListener(ticker, with_private=True)
    
    # Add custom handlers (these would override the default ones)
    # listener.ws_client._register_callback("orderbook_delta", custom_orderbook_handler)
    # listener.ws_client._register_callback("trade", custom_trade_handler)
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await listener.stop()


def custom_orderbook_handler(data):
    """Example custom orderbook handler."""
    logger.info(f"Custom orderbook handler received: {data}")


def custom_trade_handler(data):
    """Example custom trade handler."""
    logger.info(f"Custom trade handler received: {data}")


if __name__ == "__main__":
    # Run the basic example
    asyncio.run(example_basic_usage())
    
    # Uncomment to run other examples:
    # asyncio.run(example_with_private_data())
    # asyncio.run(example_custom_handlers())
