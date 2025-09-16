"""
Streamlit dashboard for Kalshi market making bot.
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import time
import logging

from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener
from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MarketDashboard:
    """Streamlit dashboard for market making bot."""
    
    def __init__(self):
        """Initialize the dashboard."""
        self.config = Config()
        self.kalshi_client = KalshiAPIClient(self.config)
        self.screener = MarketScreener(self.kalshi_client, self.config)
        
        # Initialize session state
        if 'screening_results' not in st.session_state:
            st.session_state.screening_results = []
        if 'last_update' not in st.session_state:
            st.session_state.last_update = None
        if 'auto_refresh' not in st.session_state:
            st.session_state.auto_refresh = False
    
    def run(self):
        """Run the dashboard."""
        st.set_page_config(
            page_title="Kalshi Market Making Bot",
            page_icon="📊",
            layout="wide",
            initial_sidebar_state="expanded"
        )
        
        st.title("📊 Kalshi Market Making Bot")
        st.markdown("Real-time market screening for profitable trading opportunities")
        
        # Sidebar controls
        self._render_sidebar()
        
        # Main content
        if st.session_state.auto_refresh:
            self._auto_refresh_content()
        else:
            self._render_main_content()
    
    def _render_sidebar(self):
        """Render sidebar controls."""
        st.sidebar.header("🎛️ Controls")
        
        # API Status
        st.sidebar.subheader("API Status")
        if self.kalshi_client.health_check():
            st.sidebar.success("✅ Kalshi API Connected")
        else:
            st.sidebar.error("❌ Kalshi API Disconnected")
        
        # Balance (if authenticated)
        balance = self.kalshi_client.get_balance()
        if balance is not None:
            st.sidebar.metric("Account Balance", f"${balance:.2f}")
        
        # Refresh controls
        st.sidebar.subheader("Refresh Controls")
        if st.sidebar.button("🔄 Refresh Markets", type="primary"):
            self._refresh_markets()
        
        st.session_state.auto_refresh = st.sidebar.checkbox(
            "Auto Refresh (5s)", 
            value=st.session_state.auto_refresh
        )
        
        st.sidebar.caption("💡 Tip: Click 'Refresh Markets' after changing criteria to see updated results")
        
        # Screening criteria
        st.sidebar.subheader("Screening Criteria")
        min_volume = st.sidebar.number_input(
            "Min Volume", 
            min_value=0, 
            value=self.config.MIN_VOLUME,
            step=100
        )
        max_spread = st.sidebar.slider(
            "Max Spread %", 
            min_value=0.0, 
            max_value=0.2, 
            value=self.config.MAX_SPREAD_PERCENTAGE,
            format="%.3f"
        )
        max_days = st.sidebar.number_input(
            "Max Days to Expiry", 
            min_value=1, 
            value=self.config.MAX_TIME_TO_EXPIRY_DAYS
        )
        min_liquidity = st.sidebar.number_input(
            "Min Liquidity", 
            min_value=0, 
            value=self.config.MIN_LIQUIDITY,
            step=100
        )
        min_open_interest = st.sidebar.number_input(
            "Min Open Interest", 
            min_value=0, 
            value=100,
            step=50
        )
        
        # Update criteria if changed
        if (min_volume != self.config.MIN_VOLUME or 
            max_spread != self.config.MAX_SPREAD_PERCENTAGE or 
            max_days != self.config.MAX_TIME_TO_EXPIRY_DAYS or
            min_liquidity != self.config.MIN_LIQUIDITY or
            min_open_interest != 100):
            # Update config
            self.config.MIN_VOLUME = min_volume
            self.config.MAX_SPREAD_PERCENTAGE = max_spread
            self.config.MAX_TIME_TO_EXPIRY_DAYS = max_days
            self.config.MIN_LIQUIDITY = min_liquidity
            
            # Update screener criteria
            from models import ScreeningCriteria
            new_criteria = ScreeningCriteria(
                min_volume=min_volume,
                max_spread_percentage=max_spread,
                min_liquidity=min_liquidity,
                max_time_to_expiry_days=max_days,
                min_open_interest=min_open_interest,
                categories=None  # No category filtering
            )
            self.screener.update_criteria(new_criteria)
            
            # Show message that criteria were updated
            st.sidebar.success("✅ Screening criteria updated!")
            
            # Force refresh of markets with new criteria
            st.sidebar.info("🔄 Refreshing markets with new criteria...")
            self._refresh_markets()
    
    def _render_main_content(self):
        """Render main dashboard content."""
        # Summary metrics
        self._render_summary_metrics()
        
        # Market opportunities table
        self._render_opportunities_table()
        
        # Charts
        col1, col2 = st.columns(2)
        with col1:
            self._render_score_distribution()
        with col2:
            self._render_category_breakdown()
        
        # Market details
        self._render_market_details()
    
    def _auto_refresh_content(self):
        """Auto-refresh content every 5 seconds."""
        placeholder = st.empty()
        
        with placeholder.container():
            self._render_main_content()
        
        time.sleep(5)
        self._refresh_markets()
        st.rerun()
    
    def _render_summary_metrics(self):
        """Render summary metrics."""
        st.subheader("📈 Summary")
        
        if not st.session_state.screening_results:
            st.info("No screening results available. Click 'Refresh Markets' to get started.")
            return
        
        summary = self.screener.get_screening_summary(st.session_state.screening_results)
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Markets", summary['total_markets'])
        
        with col2:
            st.metric("Profitable Opportunities", summary['profitable_markets'])
        
        with col3:
            st.metric("Success Rate", f"{summary['profitability_rate']:.1%}")
        
        with col4:
            st.metric("Avg Score", f"{summary['avg_score']:.2f}")
        
        # Last update time
        if st.session_state.last_update:
            st.caption(f"Last updated: {st.session_state.last_update.strftime('%H:%M:%S')}")
    
    def _render_opportunities_table(self):
        """Render opportunities table."""
        st.subheader("🎯 Top Opportunities")
        
        if not st.session_state.screening_results:
            return
        
        # Get top opportunities
        top_opportunities = self.screener.get_top_opportunities(
            st.session_state.screening_results, 
            limit=20
        )
        
        if not top_opportunities:
            st.warning("No profitable opportunities found with current criteria.")
            return
        
        # Create DataFrame
        data = []
        for result in top_opportunities:
            market = result.market
            event = result.event
            
            # Create event title
            event_title = event.title[:30] + "..." if event and len(event.title) > 30 else (event.title if event else "N/A")
            
            # Use series ticker (event ticker) for the URL
            series_ticker = event.series_ticker
            kalshi_url = f"https://kalshi.com/markets/{series_ticker}"
            
            data.append({
                'Event': event_title,
                'Series Ticker': series_ticker,
                'Market Ticker': market.ticker,
                'Market Title': market.title[:40] + "..." if len(market.title) > 40 else market.title,
                'Category': event.category,
                'Score': f"{result.score:.2f}",
                'Volume': market.volume,
                'Spread %': f"{market.spread_percentage:.1%}" if market.spread_percentage else "N/A",
                'Mid Price': f"{market.mid_price:.2f}" if market.mid_price else "N/A",
                'Days to Expiry': market.days_to_expiry,
                'Kalshi Link': kalshi_url,
                'Reasons': "; ".join(result.reasons[:2])  # Show first 2 reasons
            })
        
        df = pd.DataFrame(data)
        
        # Display table with clickable links
        st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            disabled=True,
            column_config={
                "Kalshi Link": st.column_config.LinkColumn(
                    "Kalshi Link",
                    help="Click to view market on Kalshi",
                    display_text="🔗 View"
                )
            }
        )
    
    def _render_score_distribution(self):
        """Render score distribution chart."""
        st.subheader("📊 Score Distribution")
        
        if not st.session_state.screening_results:
            st.info("No data available")
            return
        
        scores = [r.score for r in st.session_state.screening_results]
        
        fig = px.histogram(
            x=scores,
            nbins=20,
            title="Distribution of Market Scores",
            labels={'x': 'Score', 'y': 'Count'}
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    def _render_category_breakdown(self):
        """Render category breakdown chart."""
        st.subheader("📂 Category Breakdown")
        
        if not st.session_state.screening_results:
            st.info("No data available")
            return
        
        # Count by category
        category_counts = {}
        for result in st.session_state.screening_results:
            category = result.event.category
            if category not in category_counts:
                category_counts[category] = 0
            category_counts[category] += 1
        
        if not category_counts:
            st.info("No data available")
            return
        
        fig = px.pie(
            values=list(category_counts.values()),
            names=list(category_counts.keys()),
            title="Markets by Category"
        )
        
        st.plotly_chart(fig, use_container_width=True)
    
    def _render_market_details(self):
        """Render detailed market information."""
        st.subheader("🔍 Market Details")
        
        if not st.session_state.screening_results:
            return
        
        # Create a selectbox for market selection
        market_options = {}
        for r in st.session_state.screening_results:
            event_info = f" ({r.event.title[:20]}...)" if r.event else ""
            key = f"{r.market.ticker} - {r.market.title[:25]}...{event_info}"
            market_options[key] = r
        
        selected_market_key = st.selectbox(
            "Select a market for detailed view:",
            options=list(market_options.keys())
        )
        
        if selected_market_key:
            result = market_options[selected_market_key]
            market = result.market
            event = result.event
            
            # Event information
            if event:
                st.markdown("### 📅 Event Information")
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**Event:** {event.title}")
                    st.markdown(f"**Event Ticker:** {event.ticker}")
                with col2:
                    st.markdown(f"**Total Markets:** {len(event.markets)}")
                    st.markdown(f"**Open Markets:** {len(event.open_markets)}")
                    st.markdown(f"**Total Volume:** {event.total_volume:,}")
                st.markdown("---")
            
            # Market information
            st.markdown("### 📊 Market Information")
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown(f"**Ticker:** {market.ticker}")
                st.markdown(f"**Title:** {market.title}")
                st.markdown(f"**Category:** {event.category}")
                st.markdown(f"**Status:** {market.status.value.title()}")
                st.markdown(f"**Volume:** {market.volume:,}")
                st.markdown(f"**Open Interest:** {market.open_interest:,}")
                
                # Kalshi market link
                kalshi_url = f"https://kalshi.com/markets/{market.ticker}"
                st.markdown(f"**🔗 [View on Kalshi]({kalshi_url})**")
            
            with col2:
                st.markdown(f"**Yes Bid/Ask:** {market.yes_bid:.2f} / {market.yes_ask:.2f}")
                st.markdown(f"**No Bid/Ask:** {market.no_bid:.2f} / {market.no_ask:.2f}")
                st.markdown(f"**Mid Price:** {market.mid_price:.2f}" if market.mid_price else "**Mid Price:** N/A")
                st.markdown(f"**Spread:** {market.spread_percentage:.1%}" if market.spread_percentage else "**Spread:** N/A")
                st.markdown(f"**Days to Expiry:** {market.days_to_expiry}")
                st.markdown(f"**Expiry Date:** {market.expiry_date.strftime('%Y-%m-%d %H:%M')}")
            
            # Screening reasons
            st.markdown("**Screening Analysis:**")
            for reason in result.reasons:
                st.markdown(f"• {reason}")
            
            # Price chart (simplified)
            if market.yes_bid and market.yes_ask:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=['Yes Bid', 'Yes Ask'],
                    y=[market.yes_bid, market.yes_ask],
                    marker_color=['green', 'red']
                ))
                fig.update_layout(
                    title=f"Price Levels - {market.ticker}",
                    yaxis_title="Price",
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True)
    
    def _refresh_markets(self):
        """Refresh event and market data."""
        try:
            with st.spinner("Fetching events..."):
                # Fetch events
                events = self.kalshi_client.get_events(limit=200, status="open", max_events=self.config.MAX_EVENTS)
                
                if not events:
                    st.error("No events found")
                    return
                
                # Screen events and their markets
                with st.spinner("Screening events and markets..."):
                    results = self.screener.screen_events(events)
                
                # Update session state
                st.session_state.screening_results = results
                from datetime import timezone
                st.session_state.last_update = datetime.now(timezone.utc)
                
                # Show debugging info
                total_markets = sum(len(event.markets) for event in events)
                open_markets = sum(len(event.open_markets) for event in events)
                closed_markets = total_markets - open_markets
                
                st.success(f"Refreshed {len(events)} events ({total_markets} markets), found {len([r for r in results if r.is_profitable])} opportunities")
                st.info(f"📊 Market breakdown: {open_markets} open, {closed_markets} closed")
                
                if open_markets == 0:
                    st.warning("⚠️ No open markets found! This might be because:")
                    st.markdown("- You're in demo mode with limited markets")
                    st.markdown("- Markets are closed outside trading hours")
                    st.markdown("- API filtering issue")
                
        except Exception as e:
            st.error(f"Failed to refresh events: {e}")
            logger.error(f"Failed to refresh events: {e}")

def main():
    """Main function to run the dashboard."""
    dashboard = MarketDashboard()
    dashboard.run()

if __name__ == "__main__":
    main()
