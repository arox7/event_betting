"""
Simple Kalshi Market Dashboard
"""
import streamlit as st
import logging
from datetime import datetime, timezone, timedelta
import os
import sys

# Add project root to path for database imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
from database.models import TradesDatabase

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
        self.trades_db = TradesDatabase()
        
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
                ["🎯 Screener", "💼 Portfolio", "📊 Site Analytics", "📈 Market Analytics", "🔍 Unusual Trades"],
                index=["🎯 Screener", "💼 Portfolio", "📊 Site Analytics", "📈 Market Analytics", "🔍 Unusual Trades"].index(
                    f"🎯 Screener" if st.session_state.current_page == 'Screener' else
                    f"💼 Portfolio" if st.session_state.current_page == 'Portfolio' else
                    f"📊 Site Analytics" if st.session_state.current_page == 'Site Analytics' else
                    f"📈 Market Analytics" if st.session_state.current_page == 'Market Analytics' else
                    f"🔍 Unusual Trades"
                ),
                key="page_selector"
            )
            
            # Update session state based on selection
            if page == "🎯 Screener":
                st.session_state.current_page = 'Screener'
            elif page == "💼 Portfolio":
                st.session_state.current_page = 'Portfolio'
            elif page == "📊 Site Analytics":
                st.session_state.current_page = 'Site Analytics'
            elif page == "📈 Market Analytics":
                st.session_state.current_page = 'Market Analytics'
            elif page == "🔍 Unusual Trades":
                st.session_state.current_page = 'Unusual Trades'
            
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
            elif st.session_state.current_page == 'Site Analytics':
                self._render_site_analytics_page()
            elif st.session_state.current_page == 'Market Analytics':
                self._render_market_analytics_page()
            elif st.session_state.current_page == 'Unusual Trades':
                self._render_unusual_trades_page()
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
                st.caption(f"Last updated: {st.session_state.last_update.strftime('%H:%M:%S ET')}")
            else:
                st.caption("Dashboard ready")
    
    def _render_site_analytics_page(self):
        """Render the Site Analytics page."""
        st.header("📊 Site Analytics")
        
        # Add refresh button
        if st.button("🔄 Refresh Data", key="refresh_site_analytics"):
            st.cache_data.clear()
            st.rerun()
        
        
        # Get data for the past 7 days (T7D)
        try:
            with st.spinner("Loading analytics data..."):
                # Get pre-computed aggregations for faster loading
                daily_aggregations = self._get_daily_aggregations()
                hourly_aggregations = self._get_hourly_aggregations()
                trades_data = self._get_trades_data()  # Still needed for metrics
            
            if not daily_aggregations and not hourly_aggregations:
                st.warning("No analytics data available. Please run the ETL job first.")
                return
            
            # Create two columns for the charts
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("📈 Daily Trade Volume (Past 7 Days)")
                self._render_daily_volume_chart(daily_aggregations)
            
            with col2:
                st.subheader("⏰ Hourly Average Trade Volume")
                self._render_hourly_volume_chart(hourly_aggregations)
            
            # Category breakdown section
            st.markdown("---")
            st.subheader("🏷️ Trade Volume by Category")
            
            # Get category aggregations
            category_aggregations = self._get_category_aggregations()
            self._render_category_breakdown_chart(category_aggregations)
            
            # Summary metrics
            st.markdown("---")
            self._render_analytics_metrics(trades_data)
            
        except Exception as e:
            st.error(f"Error loading analytics data: {e}")
            logger.error(f"Site analytics error: {e}")
    
    @st.cache_data(ttl=60)  # Cache for 1 minute (database is fast)
    def _get_trades_data(_self):
        """Fetch and process trades data for analytics from database."""
        try:
            # Get daily aggregations to calculate summary metrics (7 days for T7D)
            daily_aggs = _self.trades_db.get_daily_aggregations(days=7)
            
            # Calculate summary metrics from aggregations
            total_volume = sum(agg['total_volume'] for agg in daily_aggs)
            total_trades = sum(agg['total_trades'] for agg in daily_aggs)
            
            # Create a summary object that matches what the metrics expect
            summary_data = {
                'total_volume': total_volume,
                'total_trades': total_trades,
                'daily_aggregations': daily_aggs
            }
            
            logger.info(f"Retrieved summary data: {total_trades:,} trades, ${total_volume:,.0f} volume")
            return summary_data
            
        except Exception as e:
            logger.error(f"Error fetching trades data from database: {e}")
            return {'total_volume': 0, 'total_trades': 0, 'daily_aggregations': []}
    
    @st.cache_data(ttl=60)  # Cache for 1 minute
    def _get_daily_aggregations(_self):
        """Get pre-computed daily aggregations from database."""
        try:
            aggregations = _self.trades_db.get_daily_aggregations(days=7)
            logger.info(f"Retrieved {len(aggregations)} daily aggregations")
            return aggregations
        except Exception as e:
            logger.error(f"Error fetching daily aggregations: {e}")
            return []
    
    @st.cache_data(ttl=60)  # Cache for 1 minute
    def _get_hourly_aggregations(_self):
        """Get pre-computed hourly aggregations from database."""
        try:
            aggregations = _self.trades_db.get_hourly_aggregations(days=7)
            logger.info(f"Retrieved {len(aggregations)} hourly aggregations")
            return aggregations
        except Exception as e:
            logger.error(f"Error fetching hourly aggregations: {e}")
            return []
    
    @st.cache_data(ttl=60)  # Cache for 1 minute
    def _get_category_aggregations(_self):
        """Get pre-computed category aggregations from database."""
        try:
            aggregations = _self.trades_db.get_daily_category_aggregations(days=7)
            logger.info(f"Retrieved {len(aggregations)} category aggregations")
            return aggregations
        except Exception as e:
            logger.error(f"Error fetching category aggregations: {e}")
            return []
    
    def _render_daily_volume_chart(self, daily_aggregations):
        """Render daily trade volume chart using pre-computed aggregations."""
        try:
            if not daily_aggregations:
                st.info("No data available for daily volume chart")
                return
            
            # Get all available dates and sort them
            all_dates = sorted(list(set(agg['date'] for agg in daily_aggregations)))
            
            if not all_dates:
                st.info("No data available for daily volume chart")
                return
            
            # Get the last 7 days of data (including today if available)
            dates = all_dates[-7:]
            
            # Filter aggregations to only include the selected dates
            filtered_aggregations = [agg for agg in daily_aggregations if agg['date'] in dates]
            sorted_aggregations = sorted(filtered_aggregations, key=lambda x: x['date'])
            
            dates = [agg['date'] for agg in sorted_aggregations]
            volumes = [agg['total_volume'] for agg in sorted_aggregations]
            
            # Create chart
            if volumes:
                import plotly.express as px
                import plotly.graph_objects as go
                
                fig = go.Figure(data=go.Scatter(
                    x=dates,
                    y=volumes,
                    mode='lines+markers+text',
                    name='Daily Volume',
                    line=dict(color='#1f77b4', width=2),
                    marker=dict(size=6),
                    text=[f"${vol:,.0f}" for vol in volumes],
                    textposition='top center',
                    textfont=dict(size=11, color='black')
                ))
                
                fig.update_layout(
                    title="Daily Trade Volume",
                    xaxis_title="Date",
                    yaxis_title="Volume ($)",
                    height=500,
                    showlegend=False,
                    xaxis=dict(
                        tickformat="%Y-%m-%d",
                        tickmode='linear',
                        dtick=86400000  # 1 day in milliseconds
                    )
                )
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No data available for daily volume chart")
                
        except Exception as e:
            st.error(f"Error creating daily volume chart: {e}")
            logger.error(f"Daily volume chart error: {e}")
    
    def _render_category_breakdown_chart(self, category_aggregations):
        """Render category breakdown as stacked bar chart showing percentages."""
        try:
            if not category_aggregations:
                st.info("No category data available")
                return
            
            # Get all available dates and sort them
            all_dates = sorted(list(set(agg['date'] for agg in category_aggregations)))
            
            if not all_dates:
                st.info("No category data available")
                return
            
            # Get the last 7 days of data (including today if available)
            dates = all_dates[-7:]
            
            # Filter aggregations to only include the selected dates
            filtered_aggregations = [agg for agg in category_aggregations if agg['date'] in dates]
            
            if not dates:
                st.info("No category data available")
                return
            
            # Group data by date and calculate percentages
            import plotly.graph_objects as go
            import pandas as pd
            
            # Get all unique categories across all dates
            all_categories = set(agg['category'] for agg in filtered_aggregations)
            
            # Define colors for categories
            category_colors = {
                'Sports': '#FF6B6B',
                'Crypto': '#4ECDC4', 
                'Politics': '#45B7D1',
                'Entertainment': '#96CEB4',
                'Financials': '#FFEAA7',
                'Economics': '#DDA0DD',
                'Climate and Weather': '#98D8C8',
                'Elections': '#F7DC6F',
                'Science and Technology': '#BB8FCE',
                'Unknown': '#D5DBDB'
            }
            
            # Create stacked bar chart data
            fig = go.Figure()
            
            # For each category, create a bar trace
            for category in sorted(all_categories):
                percentages = []
                
                for date in dates:
                    # Get total volume for this date
                    date_total = sum(agg['total_volume'] for agg in filtered_aggregations if agg['date'] == date)
                    
                    # Get volume for this category on this date
                    category_volume = next((agg['total_volume'] for agg in filtered_aggregations 
                                          if agg['date'] == date and agg['category'] == category), 0)
                    
                    # Calculate percentage
                    percentage = (category_volume / date_total * 100) if date_total > 0 else 0
                    percentages.append(percentage)
                
                # Only add trace if category has any volume
                if any(p > 0 for p in percentages):
                    fig.add_trace(go.Bar(
                        name=category,
                        x=dates,
                        y=percentages,
                        marker_color=category_colors.get(category, '#D5DBDB'),
                        text=[f"{p:.1f}%" if p > 5 else "" for p in percentages],  # Only show text for >5%
                        textposition='inside',
                        textfont=dict(size=10, color='white')
                    ))
            
            fig.update_layout(
                title="Trade Volume by Category (%)",
                xaxis_title="Date",
                yaxis_title="Percentage (%)",
                height=500,
                barmode='stack',
                xaxis=dict(
                    tickformat="%Y-%m-%d",
                    tickmode='linear',
                    dtick=86400000  # 1 day in milliseconds
                ),
                yaxis=dict(
                    range=[0, 100],
                    ticksuffix="%"
                ),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                )
            )
            
            st.plotly_chart(fig, use_container_width=True)
            
        except Exception as e:
            st.error(f"Error creating category breakdown chart: {e}")
            logger.error(f"Error creating category breakdown chart: {e}")
    
    def _render_hourly_volume_chart(self, hourly_aggregations):
        """Render hourly average trade volume chart using pre-computed aggregations."""
        try:
            if not hourly_aggregations:
                st.info("No data available for hourly volume chart")
                return
            
            # Get all available dates and sort them
            all_dates = sorted(list(set(agg['date'] for agg in hourly_aggregations)))
            
            if not all_dates:
                st.info("No data available for hourly volume chart")
                return
            
            # Get the last 7 days of data (including today if available)
            dates = all_dates[-7:]
            
            # Filter aggregations to only include the selected dates
            filtered_aggregations = [agg for agg in hourly_aggregations if agg['date'] in dates]
            
            # Group by hour and calculate averages
            hourly_volumes = {hour: [] for hour in range(24)}
            
            for agg in filtered_aggregations:
                hour = agg['hour']
                total_volume = agg['total_volume']
                hourly_volumes[hour].append(total_volume)
            
            # Calculate average volume per hour
            avg_volumes = []
            hours = list(range(24))
            
            for hour in hours:
                if hourly_volumes[hour]:
                    avg_volumes.append(sum(hourly_volumes[hour]) / len(hourly_volumes[hour]))
                else:
                    avg_volumes.append(0)
            
            # Create chart
            if any(avg_volumes):
                import plotly.express as px
                import plotly.graph_objects as go
                
                fig = go.Figure(data=go.Bar(
                    x=hours,
                    y=avg_volumes,
                    name='Avg Hourly Volume',
                    marker=dict(color='#ff7f0e'),
                    text=[f"${vol:,.0f}" if vol > 0 else "" for vol in avg_volumes],
                    textposition='inside',
                    textfont=dict(size=9, color='black')
                ))
                
                fig.update_layout(
                    title="Average Hourly Trade Volume (Eastern Time)",
                    xaxis_title="Hour of Day (ET)",
                    yaxis_title="Average Hourly Volume ($)",
                    height=500,
                    showlegend=False,
                    xaxis=dict(tickmode='linear', dtick=1)
                )
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No data available for hourly volume chart")
                
        except Exception as e:
            st.error(f"Error creating hourly volume chart: {e}")
            logger.error(f"Hourly volume chart error: {e}")
    
    def _render_analytics_metrics(self, summary_data):
        """Render summary metrics for analytics."""
        try:
            # Get 7-day data for T7D calculations
            daily_aggs = summary_data.get('daily_aggregations', [])
            
            # Calculate 7-day metrics (last 7 days)
            t7d_daily_aggs = daily_aggs[-7:] if len(daily_aggs) >= 7 else daily_aggs
            
            # Calculate T7D metrics
            t7d_total_volume = sum(agg['total_volume'] for agg in t7d_daily_aggs)
            t7d_total_trades = sum(agg['total_trades'] for agg in t7d_daily_aggs)
            t7d_avg_daily_volume = t7d_total_volume / len(t7d_daily_aggs) if t7d_daily_aggs else 0
            t7d_avg_daily_trades = t7d_total_trades / len(t7d_daily_aggs) if t7d_daily_aggs else 0
            
            # Find peak hour from hourly aggregations
            hourly_aggs = self._get_hourly_aggregations()
            peak_hour = 0
            max_avg_volume = 0
            
            # Group by hour and find peak
            hourly_volumes = {hour: [] for hour in range(24)}
            for agg in hourly_aggs:
                hour = agg['hour']
                total_volume = agg['total_volume']
                hourly_volumes[hour].append(total_volume)
            
            for hour in range(24):
                if hourly_volumes[hour]:
                    avg_volume = sum(hourly_volumes[hour]) / len(hourly_volumes[hour])
                    if avg_volume > max_avg_volume:
                        max_avg_volume = avg_volume
                        peak_hour = hour
            
            # Display only the 3 requested metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Average Daily Volume (T7D)", f"${t7d_avg_daily_volume:,.0f}")
            with col2:
                st.metric("Average Daily Trades (T7D)", f"{t7d_avg_daily_trades:,.0f}")
            with col3:
                st.metric("Peak Trading Hour", f"{peak_hour:02d}:00")
            
            # Additional info
            st.markdown(f"**Data Period:** Past 7 days (T7D)")
            st.markdown(f"**Last Updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
        except Exception as e:
            st.error(f"Error calculating metrics: {e}")
            logger.error(f"Analytics metrics error: {e}")
    
    def _render_market_analytics_page(self):
        """Render the Market Analytics page."""
        st.header("📈 Market Analytics")
        
        # Get top traded markets data
        try:
            top_markets = self.trades_db.get_top_traded_markets(days=7, limit=5, kalshi_client=self.kalshi_client)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("Top Traded Markets ($)")
                for i, market in enumerate(top_markets['by_volume'], 1):
                    market_name = market.get('market_title', market['market_ticker'])
                    st.write(f"**{i}.** {market_name}")
                    st.write(f"   Volume: ${market['total_volume']:,.2f}")
                    st.write(f"   Trades: {market['total_trades']:,}")
                    st.write(f"   Avg Trade: ${market['avg_volume_per_trade']:.2f}")
                    st.write("")
            
            with col2:
                st.subheader("Top Traded Markets (# trades)")
                for i, market in enumerate(top_markets['by_trades'], 1):
                    market_name = market.get('market_title', market['market_ticker'])
                    st.write(f"**{i}.** {market_name}")
                    st.write(f"   Volume: ${market['total_volume']:,.2f}")
                    st.write(f"   Trades: {market['total_trades']:,}")
                    st.write(f"   Avg Trade: ${market['avg_volume_per_trade']:.2f}")
                    st.write("")
                    
        except Exception as e:
            st.error(f"Error loading top traded markets: {e}")
            logger.error(f"Error loading top traded markets: {e}")
            
        # Add more market analytics content here in the future
        st.markdown("---")
        st.info("More market analytics features coming soon!")
    
    def _render_unusual_trades_page(self):
        """Render the Unusual Trades page."""
        st.header("🔍 Unusual Trades")
        st.info("Unusual Trades page is coming soon! This will identify and analyze unusual trading activity.")
        
        # Placeholder content
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Unusual Volume", "Coming Soon")
        with col2:
            st.metric("Large Orders", "Coming Soon")
        with col3:
            st.metric("Anomalies", "Coming Soon")
        
        st.markdown("### 🚨 Planned Features")
        st.markdown("""
        - Unusual volume detection
        - Large order identification
        - Price anomaly alerts
        - Trading pattern analysis
        """)

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
