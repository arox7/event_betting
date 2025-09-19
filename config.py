"""
Configuration settings for the Kalshi market making bot.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Configuration class for the application."""
    
    # Kalshi API Configuration
    KALSHI_API_KEY_ID = os.getenv('KALSHI_API_KEY_ID', '')
    KALSHI_PRIVATE_KEY_PATH = os.getenv('KALSHI_PRIVATE_KEY_PATH', '')
    KALSHI_API_HOST = os.getenv('KALSHI_API_HOST', 'https://api.elections.kalshi.com/')
    KALSHI_DEMO_MODE = os.getenv('KALSHI_DEMO_MODE', 'false').lower() == 'true'
    
    # Gemini API Configuration
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
    
    # Demo environment URL
    KALSHI_DEMO_HOST = 'https://demo-api.kalshi.co/trade-api/v2'
    
    # Dashboard Configuration
    DASHBOARD_PORT = int(os.getenv('DASHBOARD_PORT', 8501))
    DASHBOARD_HOST = os.getenv('DASHBOARD_HOST', 'localhost')
    
    # Market Screening Parameters (for dashboard filtering)
    MIN_VOLUME = 1000  # Minimum volume for consideration
    MIN_VOLUME_24H = 500  # Minimum 24h volume
    MAX_SPREAD_PERCENTAGE = 0.20  # Maximum spread percentage (20%)
    MAX_SPREAD_CENTS = 20  # Maximum spread in cents
    MIN_SPREAD_CENTS = 1  # Minimum spread in cents
    MIN_LIQUIDITY = 500  # Minimum liquidity requirement
    MAX_TIME_TO_CLOSE_DAYS = 365  # Maximum days until market close
    MIN_OPEN_INTEREST = 100  # Minimum open interest
    
    # Dashboard settings
    DASHBOARD_REFRESH_INTERVAL = 5  # seconds
    MAX_EVENTS = 4000  # Maximum number of events to fetch
