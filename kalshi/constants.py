"""
Constants for the Kalshi API package.
"""

# Market status constants
VALID_MARKET_STATUSES = {'initialized', 'active', 'closed', 'settled', 'determined', 'finalized'}

# Cache TTL settings (in seconds)
CACHE_TTL = {
    'market': 600,      # 10 minutes for market data (less volatile)
    'balance': 60,      # 1 minute for balance (more volatile)
    'positions': 120,   # 2 minutes for positions (moderate volatility)
    'events': 1800,     # 30 minutes for events (very stable)
    'enriched_positions': 300  # 5 minutes for enriched positions
}

# Default cache TTL for unknown types
DEFAULT_CACHE_TTL = 300  # 5 minutes

# API limits
DEFAULT_MARKET_LIMIT = 100
DEFAULT_EVENT_LIMIT = 100
DEFAULT_POSITION_LIMIT = 200
DEFAULT_FILL_LIMIT = 100
DEFAULT_SETTLEMENT_LIMIT = 100

# Concurrent request limits
MAX_CONCURRENT_MARKET_REQUESTS = 10
MAX_CONCURRENT_EVENT_REQUESTS = 5

# Request timeouts
REQUEST_TIMEOUT = 10  # seconds

# WebSocket settings
WEBSOCKET_PING_INTERVAL = 20
WEBSOCKET_PING_TIMEOUT = 10
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 2  # seconds

# Portfolio calculation constants
CENTS_TO_DOLLARS = 100.0
