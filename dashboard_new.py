"""
Simple Kalshi Market Dashboard
"""
import streamlit as st

# This MUST be the first Streamlit call
st.set_page_config(
    page_title="Kalshi Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

import logging
from datetime import datetime

from config import Config, setup_logging
from kalshi_client import KalshiAPIClient
from kalshi_websocket import WebSocketManager
from market_screener import MarketScreener
from gemini_screener import GeminiScreener

from dashboard.screener import ScreenerPage
from dashboard.portfolio import PortfolioPage

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

# Note: Removed caching due to hashability issues with KalshiAPIClient objects
# Streamlit's @st.cache_resource cannot hash complex objects like KalshiAPIClient

class SimpleDashboard:
    """Simple two-page dashboard for Kalshi markets."""
    
    def __init__(self):
        """Initialize the dashboard."""
        self.config = Config()
        
        # Initialize resources directly (caching removed due to hashability issues)
        self.kalshi_client = KalshiAPIClient(self.config)
        self.ws_manager = WebSocketManager(self.config)
        self.screener = MarketScreener(self.kalshi_client, self.config)
        self.gemini_screener = GeminiScreener(self.config)
        
        # Initialize pages
        self.screener_page = ScreenerPage(
            self.kalshi_client, 
            self.screener, 
            self.gemini_screener,
            self.ws_manager,
            self.config
        )
        self.portfolio_page = PortfolioPage(
            self.kalshi_client,
            self.ws_manager
        )
        
        # Initialize session state
        self._init_session_state()
    
    def _init_session_state(self):
        """Initialize session state variables efficiently."""
        if not hasattr(st.session_state, '_initialized'):
            defaults = {
                'current_page': 'Screener',
                'websocket_connected': False,
                'last_update': None,
                'screening_results': [],
                'portfolio_data': None
            }
            
            for key, default_value in defaults.items():
                st.session_state[key] = default_value
            
            st.session_state._initialized = True
    
    def run(self):
        """Run the dashboard."""
        # Header
        st.title("📈 Kalshi Market Dashboard")
        
        # Page navigation
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            if st.button("🎯 Screener", key="nav_screener"):
                st.session_state.current_page = 'Screener'
        
        with col3:
            if st.button("💼 Portfolio", key="nav_portfolio"):
                st.session_state.current_page = 'Portfolio'
        
        # Current page indicator
        with col2:
            st.markdown(f"**Current Page:** {st.session_state.current_page}")
        
        st.divider()
        
        # Render current page with error boundaries
        try:
            if st.session_state.current_page == 'Screener':
                self.screener_page.render()
            elif st.session_state.current_page == 'Portfolio':
                self.portfolio_page.render()
            else:
                st.error(f"Unknown page: {st.session_state.current_page}")
        except Exception as e:
            st.error(f"Error rendering {st.session_state.current_page} page: {e}")
            logger.error(f"Page render error: {e}")
            st.info("Please try refreshing the page or contact support if the issue persists.")
        
        # Footer
        st.divider()
        col1, col2, col3 = st.columns(3)
        with col2:
            if st.session_state.last_update:
                st.caption(f"Last updated: {st.session_state.last_update.strftime('%H:%M:%S UTC')}")
            else:
                st.caption("Dashboard ready")

def main():
    """Main function to run the dashboard."""
    try:
        dashboard = SimpleDashboard()
        dashboard.run()
    except Exception as e:
        st.error(f"Dashboard error: {e}")
        logger.error(f"Dashboard error: {e}")

if __name__ == "__main__":
    main()
