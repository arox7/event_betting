"""
Simple Kalshi Market Dashboard
"""
import streamlit as st
import logging

from config import Config, setup_logging
from kalshi import KalshiAPIClient
from screening import MarketScreener, GeminiScreener

from screener import ScreenerPage
from portfolio import PortfolioPage

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

class SimpleDashboard:
    """Simple two-page dashboard for Kalshi markets."""
    
    def __init__(self):
        """Initialize the dashboard."""
        self.config = Config()
        self.kalshi_client = KalshiAPIClient(self.config)
        self.screener = MarketScreener(self.kalshi_client, self.config)
        self.gemini_screener = GeminiScreener(self.config)
        
        # Initialize pages
        self.screener_page = ScreenerPage(
            self.kalshi_client, 
            self.screener, 
            self.gemini_screener,
            self.config
        )
        self.portfolio_page = PortfolioPage(
            self.kalshi_client
        )
        
        # Initialize session state
        self._init_session_state()
    
    def _init_session_state(self):
        """Initialize session state variables efficiently."""
        if not hasattr(st.session_state, '_initialized'):
            defaults = {
                'current_page': 'Screener',
                'last_update': None,
                'screening_results': [],
                'portfolio_data': None
            }
            
            for key, default_value in defaults.items():
                st.session_state[key] = default_value
            
            st.session_state._initialized = True
    
    
    def run(self):
        """Run the dashboard."""
        st.set_page_config(
            page_title="Kalshi Dashboard",
            page_icon="📈",
            layout="wide",
            initial_sidebar_state="expanded"
        )
        
        # Sidebar navigation
        with st.sidebar:
            st.title("📈 Kalshi Dashboard")
            st.divider()
            
            # Page selector
            st.subheader("📋 Navigation")
            page = st.radio(
                "Select Page:",
                ["🎯 Screener", "💼 Portfolio"],
                index=0 if st.session_state.current_page == 'Screener' else 1,
                key="page_selector"
            )
            
            # Update session state based on selection
            if page == "🎯 Screener":
                st.session_state.current_page = 'Screener'
                st.rerun()
            elif page == "💼 Portfolio":
                st.session_state.current_page = 'Portfolio'
                st.rerun()
            
            st.divider()
            
            # Add some dashboard info
            st.markdown("### 📊 Quick Stats")
            if st.session_state.current_page == 'Portfolio' and st.session_state.portfolio_data:
                try:
                    # Show quick portfolio stats in sidebar
                    portfolio_data = st.session_state.portfolio_data
                    st.metric("Cash Balance", f"${portfolio_data['cash_balance']:.2f}")
                    st.metric("Total Portfolio", f"${portfolio_data['total_portfolio_value']:.2f}")
                    st.metric("Active Positions", portfolio_data['total_positions'])
                except Exception:
                    st.info("Portfolio data loading...")
            else:
                st.info("Select Portfolio page to see stats")
        
        # Main content area
        st.title("📈 Kalshi Market Dashboard")
        
        # Current page indicator
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
