"""
Constants for the Kalshi Dashboard
"""
from typing import Dict, Any, List

# Default screening criteria
DEFAULT_SCREENING_CRITERIA: Dict[str, Any] = {
    'min_volume': 1000,
    'min_volume_24h': 500,
    'max_spread_percentage': 5.0,
    'max_spread_cents': 10,
    'min_spread_cents': 1,
    'min_liquidity_dollars': 1000,
    'max_time_to_close_days': 30,
    'min_open_interest': 500,
    'categories': []
}

# Session state defaults
SESSION_DEFAULTS: Dict[str, Any] = {
    'current_page': 'Screener',
    'last_update': None,
    'screening_results': [],
    'portfolio_data': None,
    'screening_mode': 'rule_based',
    'screening_criteria': DEFAULT_SCREENING_CRITERIA,
    'portfolio_last_update': None,
    'ai_query_used': None,
    'ai_screening_code': None,
    'ai_criteria_explanation': None
}

# AI Quick search examples
AI_QUICK_EXAMPLES: List[Dict[str, str]] = [
    {
        'label': '⚡ High Volume',
        'query': 'show me markets with volume > 5000 and tight spreads'
    },
    {
        'label': '💰 Closing Soon',
        'query': 'find markets closing in the next hour'
    },
    {
        'label': '🎯 Undervalued',
        'query': 'show me markets with good volume but wide spreads'
    },
    {
        'label': '🔥 Hot Markets',
        'query': 'find the most active markets with tight spreads'
    }
]

# Filter configurations
FILTER_CONFIGS: Dict[str, Dict[str, Any]] = {
    'min_volume_24h': {
        'label': 'Min Volume (24h)',
        'min_value': 0,
        'step': 100,
        'help': 'Minimum volume in the last 24 hours'
    },
    'min_liquidity_dollars': {
        'label': 'Min Liquidity ($)',
        'min_value': 0,
        'step': 100,
        'help': 'Minimum liquidity requirement'
    },
    'max_spread_percentage': {
        'label': 'Max Spread %',
        'min_value': 0.0,
        'max_value': 100.0,
        'step': 0.5,
        'help': 'Maximum spread percentage'
    },
    'min_open_interest': {
        'label': 'Min Open Interest',
        'min_value': 0,
        'step': 50,
        'help': 'Minimum open interest requirement'
    },
    'max_spread_cents': {
        'label': 'Max Spread (cents)',
        'min_value': 0,
        'step': 1,
        'help': 'Maximum spread in cents'
    },
    'max_time_to_close_days': {
        'label': 'Max Days to Close',
        'min_value': 0,
        'max_value': 365,
        'step': 1,
        'help': 'Maximum days until market closes'
    }
}

# UI Configuration
UI_CONFIG: Dict[str, Any] = {
    'page_title': 'Kalshi Dashboard',
    'page_icon': '📈',
    'layout': 'wide',
    'initial_sidebar_state': 'collapsed',
    'ai_query_height': 80,
    'table_hide_index': True
}

# Messages
MESSAGES: Dict[str, str] = {
    'loading_markets': 'Loading market data...',
    'loading_initial': 'Loading initial market data...',
    'generating_ai': 'Generating AI screening function...',
    'analyzing_criteria': 'Analyzing AI criteria...',
    'running_ai': 'Running AI screening...',
    'loading_portfolio': 'Loading portfolio data...',
    'no_markets': 'No market data available',
    'no_positions': 'No positions found in your portfolio',
    'gemini_not_configured': 'Gemini API not configured. Please set GEMINI_API_KEY in your .env file.',
    'portfolio_error': 'Unable to load portfolio data. Please check your API credentials.',
    'ai_generation_failed': 'Failed to generate screening function from your query',
    'screening_error': 'Screening error',
    'ai_screening_error': 'AI screening error',
    'portfolio_load_error': 'Error loading portfolio data',
    'dashboard_error': 'Dashboard error'
}

# Column configurations for tables
TABLE_COLUMN_CONFIGS: Dict[str, Dict[str, Any]] = {
    'screener_results': {
        'Volume (24h)': {
            'help': 'Number of contracts traded in last 24 hours'
        },
        'Open Interest': {
            'help': 'Number of outstanding contracts'
        }
    },
    'portfolio_positions': {
        'Market Value': {
            'format': '$%.2f'
        },
        'Realized P&L': {
            'format': '$%.2f'
        },
        'Unrealized P&L': {
            'format': '$%.2f'
        },
        'Total P&L': {
            'format': '$%.2f'
        },
        'Avg Price': {
            'format': '$%.2f'
        }
    }
}
