"""
Market Making Bot Package

This package contains tools for building market making strategies on Kalshi prediction markets.
It provides real-time WebSocket listeners for market data streams including orderbook updates,
market tickers, public trades, and user fills.
"""

__version__ = "1.0.0"
__author__ = "Event Betting Team"

from .mm_ws_listener import MarketMakingListener, run_market_making_listener

__all__ = [
    "MarketMakingListener",
    "run_market_making_listener"
]
