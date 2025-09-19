"""
Streamlit dashboard for Kalshi market making bot.
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timezone
from typing import List
import time
import logging

from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener
from config import Config
from models import utc_now, Position

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
        
        # Main content tabs
        tab1, tab2 = st.tabs(["📊 Markets", "💼 Positions"])
        
        with tab1:
            if st.session_state.auto_refresh:
                self._auto_refresh_content()
            else:
                self._render_main_content()
        
        with tab2:
            self._render_positions_tab()
    
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
        
        # Positions summary (if authenticated) - lazy loaded to avoid slow startup
        if st.sidebar.checkbox("📊 Load Positions Summary", value=False, help="Check to load positions data (may take a moment)"):
            with st.sidebar.spinner("Loading positions..."):
                positions = self.kalshi_client.get_positions()
                if positions:
                    # Calculate summary metrics
                    total_positions = len(positions)
                    active_positions = len([p for p in positions if p.position != 0])
                    total_pnl = sum([p.net_pnl or 0.0 for p in positions])
                    
                    st.sidebar.metric("Total Positions", total_positions)
                    st.sidebar.metric("Active Positions", active_positions)
                    st.sidebar.metric("Net P&L", f"${total_pnl:.2f}")
                    
                    # Show positions expander
                    with st.sidebar.expander("💼 Positions", expanded=False):
                        self._render_positions_summary(positions)
                else:
                    st.sidebar.info("No positions found")
        
        # Refresh controls
        st.sidebar.subheader("Refresh Controls")
        if st.sidebar.button("🔄 Refresh Markets", type="primary"):
            self._refresh_markets()
        
        st.session_state.auto_refresh = st.sidebar.checkbox(
            "Auto Refresh (5s)", 
            value=st.session_state.auto_refresh
        )
        
        st.sidebar.caption("💡 Tip: Click 'Refresh Markets' after changing criteria to see updated results")
        
        # Screening criteria with organized sections
        self._render_screening_criteria()
    
    def _render_screening_criteria(self):
        """Render comprehensive screening criteria with organized sections."""
        st.sidebar.subheader("🎯 Screening Criteria")
        
        # Initialize session state for criteria if not exists
        if 'screening_criteria' not in st.session_state:
            st.session_state.screening_criteria = {
                'min_volume': self.config.MIN_VOLUME,
                'min_volume_24h': self.config.MIN_VOLUME_24H,
                'max_spread_percentage': getattr(self.config, 'MAX_SPREAD_PERCENTAGE', None),
                'max_spread_cents': self.config.MAX_SPREAD_CENTS,
                'min_spread_cents': self.config.MIN_SPREAD_CENTS,
                'min_liquidity': self.config.MIN_LIQUIDITY,
                'max_time_to_expiry_days': self.config.MAX_TIME_TO_EXPIRY_DAYS,
                'min_open_interest': self.config.MIN_OPEN_INTEREST,
                'categories': []  # Start with empty list instead of None
            }
        
        # Volume & Liquidity Section
        with st.sidebar.expander("📊 Volume & Liquidity", expanded=True):
            min_volume = st.number_input(
                "Min Total Volume", 
                min_value=0, 
                value=st.session_state.screening_criteria['min_volume'],
                step=100,
                help="Minimum total volume across all time"
            )
            
            min_volume_24h = st.number_input(
                "Min 24h Volume", 
                min_value=0, 
                value=st.session_state.screening_criteria['min_volume_24h'],
                step=50,
                help="Minimum volume in the last 24 hours"
            )
            
            min_liquidity = st.number_input(
                "Min Liquidity ($)", 
                min_value=0, 
                value=st.session_state.screening_criteria['min_liquidity'],
                step=100,
                help="Minimum liquidity in dollars (volume + open interest)"
            )
            
            min_open_interest = st.number_input(
                "Min Open Interest", 
                min_value=0, 
                value=st.session_state.screening_criteria['min_open_interest'],
                step=50,
                help="Minimum open interest requirement"
            )
        
        # Spread Analysis Section
        with st.sidebar.expander("📈 Spread Analysis", expanded=True):
            st.markdown("**Percentage Spread**")
            max_spread_percentage = st.slider(
                "Max Spread %", 
                min_value=0.0, 
                max_value=0.5, 
                value=st.session_state.screening_criteria['max_spread_percentage'],
                step=0.001,
                format="%.3f",
                help="Maximum spread as percentage of price"
            )
            
            st.markdown("**Cents Spread**")
            col1, col2 = st.columns(2)
            with col1:
                min_spread_cents = st.number_input(
                    "Min Spread (¢)", 
                    min_value=0, 
                    value=st.session_state.screening_criteria['min_spread_cents'],
                    step=1,
                    help="Minimum spread in cents"
                )
            with col2:
                max_spread_cents = st.number_input(
                    "Max Spread (¢)", 
                    min_value=0, 
                    value=st.session_state.screening_criteria['max_spread_cents'],
                    step=1,
                    help="Maximum spread in cents"
                )
        
        # Time & Expiry Section
        with st.sidebar.expander("⏰ Time & Expiry", expanded=True):
            max_days = st.number_input(
                "Max Days to Expiry", 
                step=1,
                help="Maximum days until market expiry"
            )
        
        # Category Filtering Section
        with st.sidebar.expander("🏷️ Category Filtering", expanded=False):
            # Get available categories from current results
            available_categories = self._get_available_categories()
            
            if available_categories:
                # Get current selection from session state
                current_categories = st.session_state.screening_criteria.get('categories', [])
                
                # Create columns for buttons
                col1, col2 = st.columns(2)
                
                with col1:
                    if st.button("✅ Select All", help="Select all available categories", use_container_width=True):
                        st.session_state.screening_criteria['categories'] = available_categories.copy()
                        st.rerun()
                
                with col2:
                    if st.button("❌ Clear All", help="Deselect all categories", use_container_width=True):
                        st.session_state.screening_criteria['categories'] = []
                        st.rerun()
                
                # Multiselect for categories
                selected_categories = st.multiselect(
                    "Select Categories",
                    options=available_categories,
                    default=current_categories,
                    help="Select one or more categories to filter markets. Leave empty to include all categories."
                )
                
                # Show selection summary
                if selected_categories:
                    if len(selected_categories) == len(available_categories):
                        st.success(f"✅ All {len(available_categories)} categories selected")
                    else:
                        st.info(f"📋 {len(selected_categories)} of {len(available_categories)} categories selected")
                else:
                    st.warning("⚠️ No categories selected - will include all categories")
                
                # Convert to criteria format
                # If no categories selected, use None (no filtering)
                # If all categories selected, use None (no filtering needed)
                # Otherwise, use the selected categories list
                if not selected_categories:
                    selected_categories = None  # No filtering - show all categories
                elif len(selected_categories) == len(available_categories):
                    selected_categories = None  # All categories selected - no filtering needed
                # Otherwise, selected_categories remains as the list of selected categories
                    
            else:
                st.info("No categories available. Refresh markets to see categories.")
                selected_categories = None
        
        # Update session state with current values
        st.session_state.screening_criteria.update({
            'min_volume': min_volume,
            'min_volume_24h': min_volume_24h,
            'max_spread_percentage': max_spread_percentage,
            'max_spread_cents': max_spread_cents,
            'min_spread_cents': min_spread_cents,
            'min_liquidity': min_liquidity,
            'max_time_to_expiry_days': max_days,
            'min_open_interest': min_open_interest,
            'categories': selected_categories
        })
        
        # Apply criteria button
        if st.sidebar.button("🔄 Apply Criteria & Refresh", type="primary", use_container_width=True):
            self._apply_screening_criteria()
        
        # Export/Import criteria
        with st.sidebar.expander("💾 Save/Load Criteria", expanded=False):
            col1, col2 = st.columns(2)
            with col1:
                if st.button("📤 Export", help="Copy criteria to clipboard"):
                    self._export_criteria()
            with col2:
                if st.button("📥 Import", help="Load criteria from clipboard"):
                    self._import_criteria()
    
    def _get_available_categories(self):
        """Get available categories from current screening results."""
        if not st.session_state.screening_results:
            return []
        
        categories = set()
        for result in st.session_state.screening_results:
            if result.event and result.event.category:
                categories.add(result.event.category)
        
        return sorted(list(categories))
    
    def _apply_screening_criteria(self):
        """Apply the current screening criteria to the screener."""
        criteria = st.session_state.screening_criteria
        
        from models import ScreeningCriteria
        new_criteria = ScreeningCriteria(
            min_volume=criteria['min_volume'],
            min_volume_24h=criteria['min_volume_24h'],
            max_spread_percentage=criteria['max_spread_percentage'],
            max_spread_cents=criteria['max_spread_cents'],
            min_spread_cents=criteria['min_spread_cents'],
            min_liquidity=criteria['min_liquidity'],
            max_time_to_expiry_days=criteria['max_time_to_expiry_days'],
            min_open_interest=criteria['min_open_interest'],
            categories=criteria['categories']
        )
        
        self.screener.update_criteria(new_criteria)
        st.sidebar.success("✅ Criteria applied successfully!")
        
        # Refresh markets with new criteria
        self._refresh_markets()
    
    def _export_criteria(self):
        """Export current criteria to clipboard."""
        import json
        criteria = st.session_state.screening_criteria.copy()
        
        # Convert to JSON string
        criteria_json = json.dumps(criteria, indent=2)
        
        # Display in a text area for easy copying
        st.sidebar.text_area(
            "Copy these criteria:",
            value=criteria_json,
            height=200,
            help="Copy this JSON and save it to use later"
        )
        
        st.sidebar.success("✅ Criteria ready to copy!")
    
    def _import_criteria(self):
        """Import criteria from clipboard."""
        import json
        
        criteria_json = st.sidebar.text_area(
            "Paste criteria JSON:",
            height=200,
            help="Paste the JSON criteria you want to load"
        )
        
        if st.sidebar.button("Load Criteria"):
            try:
                criteria = json.loads(criteria_json)
                
                # Validate required fields
                required_fields = [
                    'min_volume', 'min_volume_24h', 'max_spread_percentage',
                    'max_spread_cents', 'min_spread_cents', 'min_liquidity',
                    'max_time_to_expiry_days', 'min_open_interest'
                ]
                
                if all(field in criteria for field in required_fields):
                    st.session_state.screening_criteria = criteria
                    st.sidebar.success("✅ Criteria loaded successfully!")
                    st.rerun()
                else:
                    st.sidebar.error("❌ Invalid criteria format. Missing required fields.")
                    
            except json.JSONDecodeError:
                st.sidebar.error("❌ Invalid JSON format.")
            except Exception as e:
                st.sidebar.error(f"❌ Error loading criteria: {e}")
    
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
        
        # Main metrics
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Markets", summary['total_markets'])
        
        with col2:
            st.metric("Filtered Markets", summary['passing_markets'])
        
        with col3:
            st.metric("Filter Rate", f"{summary['pass_rate']:.1%}")
        
        with col4:
            st.metric("Hidden Markets", summary['total_markets'] - summary['passing_markets'])
        
        # Current criteria summary
        if 'screening_criteria' in st.session_state:
            criteria = st.session_state.screening_criteria
            with st.expander("📋 Current Screening Criteria", expanded=False):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.markdown("**Volume & Liquidity**")
                    st.text(f"Min Volume: {criteria['min_volume']:,}")
                    st.text(f"Min 24h Volume: {criteria['min_volume_24h']:,}")
                    st.text(f"Min Liquidity: ${criteria['min_liquidity']:,}")
                    st.text(f"Min Open Interest: {criteria['min_open_interest']:,}")
                
                with col2:
                    st.markdown("**Spread Analysis**")
                    st.text(f"Max Spread %: {criteria['max_spread_percentage']:.1%}")
                    st.text(f"Min Spread: {criteria['min_spread_cents']}¢")
                    st.text(f"Max Spread: {criteria['max_spread_cents']}¢")
                
                with col3:
                    st.markdown("**Time & Expiry**")
                    st.text(f"Max Days: {criteria['max_time_to_expiry_days']}")
        
        # Last update time
        if st.session_state.last_update:
            st.caption(f"Last updated: {st.session_state.last_update.strftime('%H:%M:%S')}")
    
    def _render_opportunities_table(self):
        """Render opportunities table."""
        st.subheader("🎯 Top Opportunities")
        
        if not st.session_state.screening_results:
            return
        
        # Get top opportunities
        top_opportunities = st.session_state.screening_results
        
        if not top_opportunities:
            st.warning("No profitable opportunities found with current criteria.")
            return
        
        # Filter options
        col1, col2 = st.columns(2)
        with col1:
            show_only_filtered = st.checkbox("Show only filtered markets", value=True)
        with col2:
            search_term = st.text_input("🔍 Search events", placeholder="Search by event title...", help="Filter events by title")
        
        # Filter results
        if show_only_filtered:
            filtered_results = [r for r in top_opportunities if r.score == 1.0]
        else:
            filtered_results = top_opportunities
        
        # Apply search filter
        if search_term:
            search_lower = search_term.lower()
            filtered_results = [
                r for r in filtered_results 
                if r.event and search_lower in r.event.title.lower()
            ]
        
        if not filtered_results:
            st.info("No markets match the current filters.")
            return
        
        # Create DataFrame with enhanced data
        data = []
        for result in filtered_results:
            market = result.market
            event = result.event
            
            # Create event title
            event_title = event.title[:30] + "..." if event and len(event.title) > 30 else (event.title if event else "N/A")
            
            # Use series ticker (event ticker) for the URL
            series_ticker = event.series_ticker
            kalshi_url = f"https://kalshi.com/markets/{series_ticker}"
            
            # Calculate additional metrics
            spread_cents = market.spread_cents or 0
            volume_24h = market.volume_24h or 0
            
            data.append({
                'Event': event_title,
                'Series Ticker': series_ticker,
                'Market Ticker': market.ticker,
                'Market Title': market.title[:40] + "..." if len(market.title) > 40 else market.title,
                'Category': event.category if event else "N/A",
                'Score': f"{result.score:.2f}",
                'Status': "✅ Show" if result.score > 0 else "❌ Hidden",
                'Total Volume': f"{market.volume:,}" if market.volume else "0",
                '24h Volume': f"{volume_24h:,}" if volume_24h else "0",
                'Open Interest': f"{market.open_interest:,}" if market.open_interest else "0",
                'Spread %': f"{market.spread_percentage:.1%}" if market.spread_percentage else "N/A",
                'Spread (¢)': f"{spread_cents}¢" if spread_cents else "N/A",
                'Mid Price': f"{market.mid_price:.2f}" if market.mid_price else "N/A",
                'Days to Expiry': market.days_to_expiry,
                'Kalshi Link': kalshi_url,
                'Reasons': "; ".join(result.reasons[:2])  # Show first 2 reasons
            })
        
        df = pd.DataFrame(data)
        
        # Display table with enhanced configuration
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
                ),
                "Score": st.column_config.NumberColumn(
                    "Score",
                    help="Screening score (0-1)",
                    format="%.2f"
                ),
                "Status": st.column_config.TextColumn(
                    "Status",
                    help="Whether market passes screening criteria"
                ),
                "Total Volume": st.column_config.TextColumn(
                    "Total Volume",
                    help="Total volume across all time"
                ),
                "24h Volume": st.column_config.TextColumn(
                    "24h Volume", 
                    help="Volume in last 24 hours"
                ),
                "Open Interest": st.column_config.TextColumn(
                    "Open Interest",
                    help="Current open interest"
                ),
                "Spread %": st.column_config.TextColumn(
                    "Spread %",
                    help="Bid-ask spread as percentage"
                ),
                "Spread (¢)": st.column_config.TextColumn(
                    "Spread (¢)",
                    help="Bid-ask spread in cents"
                ),
                "Mid Price": st.column_config.TextColumn(
                    "Mid Price",
                    help="Midpoint between bid and ask"
                ),
                "Days to Expiry": st.column_config.NumberColumn(
                    "Days to Expiry",
                    help="Days until market expires"
                )
            }
        )
        
        # Add selection buttons for each market
        st.markdown("**Click a market below to view details:**")
        
        # Create columns for market selection buttons
        num_markets_to_show = min(10, len(filtered_results))  # Show up to 10 markets
        if num_markets_to_show > 0:
            cols = st.columns(min(3, num_markets_to_show))  # Up to 3 columns
            
            for i, result in enumerate(filtered_results[:num_markets_to_show]):
                col_index = i % 3
                with cols[col_index]:
                    market_label = f"{result.market.ticker[:10]}..."
                    if st.button(
                        market_label, 
                        key=f"select_market_{result.market.ticker}",
                        help=f"View details for {result.market.title[:30]}...",
                        use_container_width=True
                    ):
                        st.session_state.selected_market_ticker = result.market.ticker
                        st.rerun()
        
        # Summary stats for filtered results
        visible_count = len([r for r in filtered_results if r.score == 1.0])
        
        # Build caption with filter info
        caption_parts = [f"Showing {len(filtered_results)} markets ({visible_count} visible, {len(filtered_results) - visible_count} hidden)"]
        
        if search_term:
            caption_parts.append(f"filtered by '{search_term}'")
        
        if show_only_filtered:
            caption_parts.append("(visible only)")
        
        st.caption(" | ".join(caption_parts))
    
    def _render_score_distribution(self):
        """Render visible/hidden distribution chart."""
        st.subheader("📊 Visible vs Hidden Markets")
        
        if not st.session_state.screening_results:
            st.info("No data available")
            return
        
        visible = len([r for r in st.session_state.screening_results if r.score == 1.0])
        hidden = len([r for r in st.session_state.screening_results if r.score == 0.0])
        
        fig = px.pie(
            values=[visible, hidden],
            names=['Visible', 'Hidden'],
            title="Markets Visible vs Hidden by Filter",
            color_discrete_map={'Visible': 'green', 'Hidden': 'red'}
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
            if result.event and result.event.category:
                category = result.event.category
                if category not in category_counts:
                    category_counts[category] = 0
                category_counts[category] += 1
        
        if not category_counts:
            st.info("No category data available")
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
        
        # Find the default selection based on clicked row
        default_index = 0
        if hasattr(st.session_state, 'selected_market_ticker'):
            for i, (key, result) in enumerate(market_options.items()):
                if result.market.ticker == st.session_state.selected_market_ticker:
                    default_index = i
                    break
        
        selected_market_key = st.selectbox(
            "Select a market for detailed view:",
            options=list(market_options.keys()),
            index=default_index
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
                    st.markdown(f"**Event Ticker:** {event.event_ticker}")
                with col2:
                    st.markdown(f"**Total Markets:** {len(event.markets)}")
                    # Count open markets from the markets list
                    open_markets_count = len([m for m in event.markets if m.status == 'active'])
                    st.markdown(f"**Open Markets:** {open_markets_count}")
                    st.markdown(f"**Total Volume:** {event.total_volume:,}")
                st.markdown("---")
            
            # Market information
            st.markdown("### 📊 Market Information")
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown(f"**Ticker:** {market.ticker}")
                st.markdown(f"**Title:** {market.title}")
                st.markdown(f"**Category:** {event.category}")
                st.markdown(f"**Status:** {market.status}")
                st.markdown(f"**Volume:** {market.volume:,}")
                st.markdown(f"**Open Interest:** {market.open_interest:,}")
                
                # Kalshi market link
                kalshi_url = f"https://kalshi.com/markets/{market.ticker}"
                st.markdown(f"**🔗 [View on Kalshi]({kalshi_url})**")
            
            with col2:
                st.markdown(f"**Yes Bid/Ask:** {market.yes_bid:.2f} / {market.yes_ask:.2f}")
                st.markdown(f"**No Bid/Ask:** {market.no_bid:.2f} / {market.no_ask:.2f}")
                st.markdown(f"**Last Price:** {market.last_price:.2f}" if market.last_price else "**Last Price:** N/A")
                st.markdown(f"**Mid Price:** {market.mid_price:.2f}" if market.mid_price else "**Mid Price:** N/A")
                st.markdown(f"**Spread:** {market.spread_percentage:.1%}" if market.spread_percentage else "**Spread:** N/A")
                st.markdown(f"**Days to Expiry:** {market.days_to_expiry}")
                st.markdown(f"**Expiry Date:** {market.expiry_date.strftime('%Y-%m-%d %H:%M')}")
            
            # Screening reasons
            st.markdown("**Screening Analysis:**")
            for reason in result.reasons:
                st.markdown(f"• {reason}")
            
    
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
                st.session_state.last_update = utc_now()
                
                # Show debugging info
                total_markets = sum(len(event.markets) for event in events)
                # Count open markets from the markets list
                open_markets = sum(len([m for m in event.markets if m.status == 'active']) for event in events)
                closed_markets = total_markets - open_markets
                
                st.success(f"Refreshed {len(events)} events ({total_markets} markets), found {len([r for r in results if r.score > 0])} markets passing criteria")
                st.info(f"📊 Market breakdown: {open_markets} open, {closed_markets} closed")
                
                if open_markets == 0:
                    st.warning("⚠️ No open markets found! This might be because:")
                    st.markdown("- You're in demo mode with limited markets")
                    st.markdown("- Markets are closed outside trading hours")
                    st.markdown("- API filtering issue")
                
        except Exception as e:
            st.error(f"Failed to refresh events: {e}")
            logger.error(f"Failed to refresh events: {e}")
    
    def _render_positions_summary(self, positions: List[Position]):
        """Render positions summary in sidebar."""
        if not positions:
            st.info("No positions found")
            return
        
        # Show top positions by absolute value
        sorted_positions = sorted(positions, key=lambda p: abs(p.position), reverse=True)
        
        for position in sorted_positions[:5]:  # Show top 5
            pnl = position.net_pnl or 0.0
            pnl_color = "green" if pnl > 0 else "red" if pnl < 0 else "gray"
            
            st.markdown(f"**{position.ticker[:15]}...**")
            st.markdown(f"Position: {position.position} ({position.position_type})")
            st.markdown(f"P&L: :{pnl_color}[${pnl:.2f}]")
            st.markdown("---")
    
    def _render_positions_tab(self):
        """Render the positions tab."""
        st.header("💼 Portfolio Positions")
        
        # Get positions
        positions = self.kalshi_client.get_positions()
        
        if not positions:
            st.info("No positions found. You're authenticated, but you don't have any open positions in your portfolio.")
            return
        
        # Summary metrics
        total_positions = len(positions)
        active_positions = len([p for p in positions if p.position != 0])
        long_positions = len([p for p in positions if p.position > 0])
        short_positions = len([p for p in positions if p.position < 0])
        total_pnl = sum([p.net_pnl or 0.0 for p in positions])
        total_value = sum([p.total_value or 0.0 for p in positions])
        total_cost = sum([p.total_cost or 0.0 for p in positions])
        
        # Display metrics
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Positions", total_positions)
        with col2:
            st.metric("Active Positions", active_positions)
        with col3:
            st.metric("Long/Short", f"{long_positions}/{short_positions}")
        with col4:
            pnl_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric("Net P&L", f"${total_pnl:.2f}", delta=None)
        
        # Additional metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Value", f"${total_value:.2f}")
        with col2:
            st.metric("Total Cost", f"${total_cost:.2f}")
        with col3:
            if total_cost > 0:
                roi = ((total_value - total_cost) / total_cost) * 100
                st.metric("ROI", f"{roi:.1f}%")
        
        st.markdown("---")
        
        # Positions table
        st.subheader("📋 Position Details")
        
        # Filter options
        col1, col2, col3 = st.columns(3)
        with col1:
            show_only_active = st.checkbox("Show only active positions", value=True)
        with col2:
            sort_by = st.selectbox("Sort by", ["Position Size", "P&L", "Ticker", "Value"])
        with col3:
            search_ticker = st.text_input("🔍 Search ticker", placeholder="Filter by ticker...")
        
        # Filter and sort positions
        filtered_positions = positions.copy()
        
        if show_only_active:
            filtered_positions = [p for p in filtered_positions if p.position != 0]
        
        if search_ticker:
            filtered_positions = [p for p in filtered_positions if search_ticker.lower() in p.ticker.lower()]
        
        # Sort positions
        if sort_by == "Position Size":
            filtered_positions.sort(key=lambda p: abs(p.position), reverse=True)
        elif sort_by == "P&L":
            filtered_positions.sort(key=lambda p: p.net_pnl or 0.0, reverse=True)
        elif sort_by == "Ticker":
            filtered_positions.sort(key=lambda p: p.ticker)
        elif sort_by == "Value":
            filtered_positions.sort(key=lambda p: p.total_value or 0.0, reverse=True)
        
        if not filtered_positions:
            st.info("No positions match the current filters.")
            return
        
        # Create positions DataFrame
        positions_data = []
        for pos in filtered_positions:
            # Use market title if available, otherwise use ticker
            display_name = pos.market_title or pos.event_title or pos.ticker
            
            positions_data.append({
                'Market/Event': display_name[:50] + "..." if len(display_name) > 50 else display_name,
                'Ticker': pos.ticker,
                'Position': pos.position,
                'Type': pos.position_type,
                'Status': pos.market_status or "Unknown",
                'Total Value': f"${pos.total_value:.2f}" if pos.total_value is not None else "$0.00",
                'Total Cost': f"${pos.total_cost:.2f}" if pos.total_cost is not None else "$0.00",
                'Market Exposure': f"${pos.market_exposure:.2f}" if pos.market_exposure is not None else "$0.00",
                'Realized P&L': f"${pos.realized_pnl:.2f}" if pos.realized_pnl is not None else "$0.00",
                'Unrealized P&L': f"${pos.unrealized_pnl:.2f}" if pos.unrealized_pnl is not None else "Calculating...",
                'Net P&L': f"${pos.net_pnl:.2f}" if pos.net_pnl is not None else "$0.00",
                'Fees Paid': f"${pos.fees_paid:.2f}" if pos.fees_paid is not None else "$0.00"
            })
        
        df = pd.DataFrame(positions_data)
        
        # Display table
        st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            disabled=True,
            column_config={
                "Position": st.column_config.NumberColumn(
                    "Position",
                    help="Number of shares (positive=long, negative=short)"
                ),
                "Type": st.column_config.TextColumn(
                    "Type",
                    help="Position type"
                ),
                "Net P&L": st.column_config.TextColumn(
                    "Net P&L",
                    help="Total profit/loss (realized + unrealized)"
                )
            }
        )
        
        st.caption(f"Showing {len(filtered_positions)} of {len(positions)} positions")
        
        # Debug information (only show if there are issues)
        if st.checkbox("🔧 Show Debug Info", value=False):
            st.subheader("Debug Information")
            if positions:
                sample_pos = positions[0]
                st.json({
                    "Sample Position Data": {
                        "ticker": sample_pos.ticker,
                        "position": sample_pos.position,
                        "market_status": sample_pos.market_status,
                        "total_cost": sample_pos.total_cost,
                        "total_value": sample_pos.total_value,
                        "unrealized_pnl": sample_pos.unrealized_pnl,
                        "realized_pnl": sample_pos.realized_pnl,
                        "net_pnl": sample_pos.net_pnl,
                        "market_exposure": sample_pos.market_exposure,
                        "fees_paid": sample_pos.fees_paid
                    }
                })

def main():
    """Main function to run the dashboard."""
    dashboard = MarketDashboard()
    dashboard.run()

if __name__ == "__main__":
    main()
