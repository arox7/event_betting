"""
Simple Kalshi Market Dashboard
"""
import streamlit as st
import logging

# This MUST be the first Streamlit call
st.set_page_config(
    page_title="Kalshi Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

from config import Config, setup_logging
from kalshi import KalshiAPIClient
from screening import MarketScreener, GeminiScreener

from screener import ScreenerPage
from portfolio import PortfolioPage

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
    
    def get_accurate_portfolio_value(self):
        """Get accurate total portfolio value using the same calculation as the main portfolio page."""
        if not st.session_state.portfolio_data:
            return None
        
        try:
            portfolio_data = st.session_state.portfolio_data
            cash_balance = portfolio_data['cash_balance']
            
            # Get accurate market value from unrealized P&L data
            try:
                all_unrealized_pnl = self.kalshi_client.get_all_unrealized_pnl()
                if all_unrealized_pnl:
                    accurate_market_value = all_unrealized_pnl.get('total_market_value', 0)
                    return cash_balance + accurate_market_value
                else:
                    # Fallback to cached value
                    return portfolio_data['total_portfolio_value']
            except Exception:
                # Fallback to cached value
                return portfolio_data['total_portfolio_value']
        except Exception:
            return None
    
    def run(self):
        """Run the dashboard."""
        
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
            elif page == "💼 Portfolio":
                st.session_state.current_page = 'Portfolio'
            
            st.divider()
            
            # Add some dashboard info
            st.markdown("### 📊 Quick Stats")
            if st.session_state.current_page == 'Portfolio' and st.session_state.portfolio_data:
                try:
                    # Show quick portfolio stats in sidebar with accurate calculations
                    portfolio_data = st.session_state.portfolio_data
                    cash_balance = portfolio_data['cash_balance']
                    accurate_total_portfolio = self.get_accurate_portfolio_value()
                    
                    st.metric("Cash Balance", f"${cash_balance:.2f}")
                    st.metric("Total Portfolio", f"${accurate_total_portfolio:.2f}")
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
