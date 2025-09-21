"""
Simple Kalshi Market Dashboard
"""
import streamlit as st
import logging
from datetime import datetime

from config import Config
from kalshi_client import KalshiAPIClient
from kalshi_websocket import WebSocketManager
from market_screener import MarketScreener
from gemini_screener import GeminiScreener

from .screener import ScreenerPage
from .portfolio import PortfolioPage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
)
logger = logging.getLogger(__name__)

class SimpleDashboard:
    """Simple two-page dashboard for Kalshi markets."""
    
    def __init__(self):
        """Initialize the dashboard."""
        self.config = Config()
        self.kalshi_client = KalshiAPIClient(self.config)
        self.screener = MarketScreener(self.kalshi_client, self.config)
        self.gemini_screener = GeminiScreener(self.config)
        self.ws_manager = WebSocketManager(self.config)
        
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
        """Initialize session state variables."""
        defaults = {
            'current_page': 'Screener',
            'websocket_connected': False,
            'last_update': None,
            'screening_results': [],
            'portfolio_data': None
        }
        
        for key, default_value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = default_value
    
    def run(self):
        """Run the dashboard."""
        st.set_page_config(
            page_title="Kalshi Dashboard",
            page_icon="📈",
            layout="wide",
            initial_sidebar_state="collapsed"
        )
        
        # Header
        st.title("📈 Kalshi Market Dashboard")
        
        # Page navigation
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            if st.button("🎯 Screener", key="nav_screener", width='stretch'):
                st.session_state.current_page = 'Screener'
                st.rerun()
        
        with col3:
            if st.button("💼 Portfolio", key="nav_portfolio", width='stretch'):
                st.session_state.current_page = 'Portfolio'
                st.rerun()
        
        # Current page indicator
        with col2:
            st.markdown(f"**Current Page:** {st.session_state.current_page}")
        
        st.divider()
        
        # Render current page
        if st.session_state.current_page == 'Screener':
            self.screener_page.render()
        elif st.session_state.current_page == 'Portfolio':
            self.portfolio_page.render()
        
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
