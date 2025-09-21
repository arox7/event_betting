"""
Portfolio Overview Page
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from pprint import pprint

import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from kalshi import KalshiAPIClient, WebSocketManager
from kalshi.models import utc_now, MarketPosition

# Note: portfolio.py doesn't need to configure logging as it's imported by dashboard modules
# that will configure logging. We just get the logger here.
logger = logging.getLogger(__name__)

class PortfolioPage:
    """Portfolio overview page."""
    
    def __init__(self, kalshi_client: KalshiAPIClient, ws_manager: WebSocketManager):
        """Initialize the portfolio page."""
        self.kalshi_client = kalshi_client
        self.ws_manager = ws_manager
        
        # Initialize session state for portfolio
        if 'portfolio_data' not in st.session_state:
            st.session_state.portfolio_data = None
        if 'portfolio_last_update' not in st.session_state:
            st.session_state.portfolio_last_update = None
        if 'enriched_closed_positions_cache' not in st.session_state:
            st.session_state.enriched_closed_positions_cache = None
    
    def render(self):
        """Render the portfolio page."""
        st.header("💼 Portfolio Overview")
        
        # Auto-load portfolio data if not already loaded
        if st.session_state.portfolio_data is None:
            self._load_portfolio_data()
        
        # Portfolio summary section
        self._render_portfolio_summary()
        
        # Show data freshness indicator
        if st.session_state.portfolio_data and st.session_state.portfolio_last_update:
            time_diff = utc_now() - st.session_state.portfolio_last_update
            minutes_ago = int(time_diff.total_seconds() / 60)
            if minutes_ago < 1:
                st.caption("🟢 Data is current (just loaded)")
            elif minutes_ago < 5:
                st.caption(f"🟡 Data is {minutes_ago} minute(s) old")
            else:
                st.caption(f"🔴 Data is {minutes_ago} minutes old - consider refreshing")
        
        # Create tabs for different portfolio sections
        if st.session_state.portfolio_data:
            tab1, tab2, tab3, tab4 = st.tabs(["📊 Current Positions", "📋 Closed Positions", "📈 P&L Chart", "⏳ Resting Orders"])
            
            with tab1:
                self._render_current_positions()
            
            with tab2:
                self._render_closed_positions()
            
            with tab3:
                self._render_pnl_chart()
            
            with tab4:
                self._render_resting_orders()
        else:
            st.info("Loading portfolio data...")
    
    def _render_portfolio_summary(self):
        """Render portfolio summary metrics."""
        st.subheader("📊 Portfolio Summary")
        
        # Date range filter
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.subheader("📅 Date Range Filter")
            
            # Default to last 30 days
            default_start = datetime.now() - timedelta(days=30)
            default_end = datetime.now()
            
            date_range = st.date_input(
                "Select date range for calculations:",
                value=(default_start, default_end),
                max_value=datetime.now().date() + timedelta(days=1),  # Allow today's date
                help="Filter portfolio calculations and P&L by this date range"
            )
            
            # Store date range in session state
            if len(date_range) == 2:
                st.session_state.date_range_start = date_range[0]
                st.session_state.date_range_end = date_range[1]
            else:
                st.session_state.date_range_start = None
                st.session_state.date_range_end = None
        
        # Refresh portfolio data button
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("🔄 Refresh Portfolio Data", width='stretch'):
                self._load_portfolio_data()
        
        # Apply date filtering to existing data (no need to refetch from API)
        if st.session_state.portfolio_data:
            self._apply_date_filtering()
        
        # Portfolio metrics are now displayed within the Current Positions tab
        if not st.session_state.portfolio_data:
            st.info("Loading portfolio data...")
    
    def _load_portfolio_data(self):
        """Load portfolio data from Kalshi API (unfiltered)."""
        try:
            with st.spinner("Loading portfolio data..."):
                # Get comprehensive portfolio metrics (unfiltered - we'll filter client-side)
                portfolio_metrics = self.kalshi_client.get_portfolio_metrics()

                if portfolio_metrics is None:
                    st.error("Unable to load portfolio data. Please check your API credentials and ensure you have positions.")
                    return
                
                # Validate that we have the expected data structure
                if not isinstance(portfolio_metrics, dict):
                    st.error("Invalid portfolio data format received from API.")
                    return
                
                # Check if we have positions
                enriched_positions = portfolio_metrics['enriched_positions']
                market_positions = portfolio_metrics['market_positions']
                
                if not enriched_positions and not market_positions:
                    st.warning("No positions found in your portfolio.")
                
                # Store unfiltered data in session state
                st.session_state.portfolio_data = portfolio_metrics
                st.session_state.portfolio_last_update = utc_now()
                
                # Clear enriched closed positions cache since we have new data
                st.session_state.enriched_closed_positions_cache = None
                
                st.success(f"Portfolio data loaded successfully - {len(enriched_positions)} current positions, {len(market_positions)} total market positions")
                
        except Exception as e:
            st.error(f"Error loading portfolio data: {e}")
            st.error("This might be due to API authentication issues or network problems.")
    
    def _apply_date_filtering(self):
        """Apply date filtering using client-side filtering (much faster than API calls)."""
        if not st.session_state.portfolio_data:
            return
        
        # Get date range from session state
        start_date = None
        end_date = None
        if hasattr(st.session_state, 'date_range_start') and st.session_state.date_range_start:
            start_date = datetime.combine(st.session_state.date_range_start, datetime.min.time())
        if hasattr(st.session_state, 'date_range_end') and st.session_state.date_range_end:
            end_date = datetime.combine(st.session_state.date_range_end, datetime.max.time())
        
        # Use client-side filtering instead of expensive API calls
        all_market_positions = st.session_state.portfolio_data['market_positions']
        
        # Debug logging
        logger.info(f"Date filtering: start_date={start_date}, end_date={end_date}")
        logger.info(f"Total market positions before filtering: {len(all_market_positions)}")
        
        # Apply date filtering using the existing client method
        filtered_market_positions = self.kalshi_client.filter_market_positions_by_date(
            all_market_positions, start_date, end_date
        )
        
        logger.info(f"Total market positions after filtering: {len(filtered_market_positions)}")
        
        # Calculate ALL metrics from filtered data (not just realized P&L)
        total_realized_pnl_cents = 0
        total_fees_paid_cents = 0
        
        # Calculate win/loss metrics from filtered positions
        winning_positions = 0
        losing_positions = 0
        total_unrealized_pnl_cents = 0
        
        # Filter enriched positions by date as well (for win rate calculations)
        enriched_positions = st.session_state.portfolio_data['enriched_positions']
        filtered_enriched_positions = []
        
        for pos in enriched_positions:
            # Check if this position's last update falls within the date range
            position_data = pos.get('position', {})
            if position_data.get('last_updated_ts'):
                try:
                    last_updated_str = position_data['last_updated_ts']
                    if last_updated_str.endswith('Z'):
                        last_updated_str = last_updated_str[:-1] + '+00:00'
                    
                    pos_datetime = datetime.fromisoformat(last_updated_str)
                    
                    # Check if position falls within date range (inclusive)
                    include_position = True
                    if start_date:
                        start_date_only = start_date.date() if hasattr(start_date, 'date') else start_date
                        if pos_datetime.date() < start_date_only:
                            include_position = False
                    if end_date and include_position:
                        end_date_only = end_date.date() if hasattr(end_date, 'date') else end_date
                        if pos_datetime.date() > end_date_only:  # Exclude dates after end_date
                            include_position = False
                    
                    if include_position:
                        filtered_enriched_positions.append(pos)
                        # Count wins/losses for win rate calculation
                        unrealized_pnl = pos.get('unrealized_pnl', 0)
                        total_unrealized_pnl_cents += unrealized_pnl
                        if unrealized_pnl > 0:
                            winning_positions += 1
                        elif unrealized_pnl < 0:
                            losing_positions += 1
                except Exception as e:
                    logger.warning(f"Error parsing date for enriched position {pos.get('ticker', 'Unknown')}: {e}")
                    # Include position if we can't parse the date
                    filtered_enriched_positions.append(pos)
            else:
                # Include position if no date available
                filtered_enriched_positions.append(pos)
        
        # Calculate realized P&L from filtered market positions
        for pos in filtered_market_positions:
            realized_pnl_cents = pos['realized_pnl']
            fees_paid_cents = pos['fees_paid']
            total_realized_pnl_cents += realized_pnl_cents
            total_fees_paid_cents += fees_paid_cents
        
        # Convert to dollars
        total_realized_pnl_dollars = (total_realized_pnl_cents - total_fees_paid_cents) / 100.0
        total_fees_paid_dollars = total_fees_paid_cents / 100.0
        total_unrealized_pnl_dollars = total_unrealized_pnl_cents / 100.0
        
        # Calculate closed positions from filtered data
        closed_positions = [pos for pos in filtered_market_positions if pos['position'] == 0 and pos['total_traded'] > 0]
        
        # Calculate win rate from filtered data
        total_filtered_active_positions = len(filtered_enriched_positions)
        win_rate = (winning_positions / total_filtered_active_positions) * 100 if total_filtered_active_positions > 0 else 0
        
        # Calculate total portfolio value (cash + market value from filtered positions)
        cash_balance = st.session_state.portfolio_data['cash_balance']
        total_market_value_dollars = sum(abs(pos.get('market_value', 0)) for pos in filtered_enriched_positions) / 100.0
        total_portfolio_value_dollars = cash_balance + total_market_value_dollars
        
        # Calculate portfolio return
        portfolio_return = (total_unrealized_pnl_dollars / total_market_value_dollars) * 100 if total_market_value_dollars > 0 else 0
        
        # Update session state with ALL filtered metrics (no API call needed!)
        st.session_state.portfolio_data.update({
            'filtered_market_positions': filtered_market_positions,
            'filtered_enriched_positions': filtered_enriched_positions,
            'total_realized_pnl': total_realized_pnl_dollars,
            'total_unrealized_pnl': total_unrealized_pnl_dollars,
            'total_fees_paid': total_fees_paid_dollars,
            'closed_positions': closed_positions,
            'total_filtered_positions': len(filtered_market_positions),
            'total_closed_positions': len(closed_positions),
            'total_positions': total_filtered_active_positions,
            'winning_positions': winning_positions,
            'losing_positions': losing_positions,
            'win_rate': win_rate,
            'portfolio_return': portfolio_return,
            'total_market_value': total_market_value_dollars,
            'total_portfolio_value': total_portfolio_value_dollars,
            'date_range_start': start_date,
            'date_range_end': end_date
        })
        
        logger.info(f"Applied comprehensive date filtering: {len(filtered_market_positions)} market positions, {len(filtered_enriched_positions)} enriched positions, {len(closed_positions)} closed, win rate: {win_rate:.1f}%")
    
    def _display_portfolio_metrics(self):
        """Display portfolio metrics."""
        portfolio_data = st.session_state.portfolio_data
        
        # Key metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            cash_balance = portfolio_data['cash_balance']
            st.metric("Cash Balance", f"${cash_balance:.2f}")
        
        with col2:
            total_market_value = portfolio_data['total_market_value']
            st.metric("Market Value", f"${total_market_value:.2f}")
        
        with col3:
            total_portfolio_value = portfolio_data['total_portfolio_value']
            st.metric("Total Portfolio", f"${total_portfolio_value:.2f}")
        
        with col4:
            total_realized_pnl = portfolio_data['total_realized_pnl']
            st.metric("Realized P&L", f"${total_realized_pnl:.2f}")
        
        with col5:
            total_unrealized_pnl = portfolio_data['total_unrealized_pnl']
            st.metric("Unrealized P&L", f"${total_unrealized_pnl:.2f}")
        
        # Additional metrics
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            total_active_positions = portfolio_data['total_positions']
            st.metric("Active Positions", total_active_positions)
        
        with col2:
            total_closed_positions = portfolio_data['total_closed_positions']
            st.metric("Closed Positions", total_closed_positions)
        
        with col3:
            total_filtered_positions = portfolio_data['total_filtered_positions']
            st.metric("Filtered Markets", total_filtered_positions)
        
        with col4:
            if st.session_state.portfolio_last_update:
                st.caption(f"Last updated: {st.session_state.portfolio_last_update.strftime('%H:%M:%S UTC')}")
        
        # Show date range information
        if portfolio_data.get('date_range_start') or portfolio_data.get('date_range_end'):
            date_info = "📅 Date Range: "
            if portfolio_data.get('date_range_start'):
                date_info += f"{portfolio_data['date_range_start'].strftime('%Y-%m-%d')}"
            else:
                date_info += "Start"
            date_info += " to "
            if portfolio_data.get('date_range_end'):
                date_info += f"{portfolio_data['date_range_end'].strftime('%Y-%m-%d')}"
            else:
                date_info += "End"
            st.caption(date_info)
            
            # Show what's filtered vs current
            st.caption("💡 Realized P&L and Closed Positions are filtered by date range. Cash Balance and Market Value show current state.")
    
    def _render_current_positions(self):
        """Render current positions table."""
        if not st.session_state.portfolio_data:
            return
        
        # Get enriched positions
        enriched_positions = st.session_state.portfolio_data['enriched_positions']
        
        if not enriched_positions:
            st.info("No current positions found in your portfolio")
            return
        
        # Show consolidated portfolio summary with accurate calculations
        self._render_consolidated_portfolio_summary()
        
        # Create positions table
        self._render_positions_table(enriched_positions)
    
    def _render_consolidated_portfolio_summary(self):
        """Render consolidated portfolio summary with accurate unrealized P&L calculations."""
        try:
            # Get accurate unrealized P&L data
            all_unrealized_pnl = self.kalshi_client.get_all_unrealized_pnl()
            portfolio_data = st.session_state.portfolio_data
            
            if not all_unrealized_pnl:
                st.warning("Could not load unrealized P&L data")
                return
            
            # Extract accurate values
            total_unrealized_pnl = all_unrealized_pnl.get('total_unrealized_pnl', 0)
            accurate_market_value = all_unrealized_pnl.get('total_market_value', 0)
            position_count = all_unrealized_pnl.get('position_count', 0)
            
            # Calculate overall return percentage
            if accurate_market_value > 0:
                overall_return_pct = (total_unrealized_pnl / accurate_market_value) * 100
            else:
                overall_return_pct = 0
            
            # Get other portfolio data
            cash_balance = portfolio_data['cash_balance']
            total_realized_pnl = portfolio_data['total_realized_pnl']
            total_portfolio_value = cash_balance + accurate_market_value
            
            # First row - Core financial metrics
            col1, col2, col3, col4, col5 = st.columns(5)
            
            with col1:
                st.metric("Cash Balance", f"${cash_balance:.2f}")
            
            with col2:
                st.metric("Market Value", f"${accurate_market_value:.2f}")
            
            with col3:
                st.metric("Total Portfolio", f"${total_portfolio_value:.2f}")
            
            with col4:
                st.metric("Realized P&L", f"${total_realized_pnl:.2f}")
            
            with col5:
                st.metric("Unrealized P&L", f"${total_unrealized_pnl:.2f}")
            
            # Second row - Additional metrics
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Active Positions", position_count)
            
            with col2:
                total_closed_positions = portfolio_data['total_closed_positions']
                st.metric("Closed Positions", total_closed_positions)
            
            with col3:
                total_filtered_positions = portfolio_data['total_filtered_positions']
                st.metric("Filtered Markets", total_filtered_positions)

            
            # Last updated timestamp
            if st.session_state.portfolio_last_update:
                st.caption(f"Last updated: {st.session_state.portfolio_last_update.strftime('%H:%M:%S UTC')}")
            
            # Add explanation
            with st.expander("ℹ️ How Unrealized P&L is Calculated"):
                st.markdown("""
                **Unrealized P&L Calculation:**
                
                1. **Cost Basis**: Average price per contract = Total cost ÷ Quantity
                2. **Current Price**: Latest market price from the last trade
                3. **Unrealized P&L**: (Current Price - Cost Basis) × Quantity
                4. **Fees**: Total fees paid for trading this position
                5. **Net P&L**: Realized P&L + Unrealized P&L - Fees
                
                **Example:**
                - Bought 10 contracts at $0.54 each = $5.40 total cost
                - Current market price = $0.53
                - Unrealized P&L = ($0.53 - $0.54) × 10 = -$0.10
                - Fees paid = $0.02
                - Net P&L = Realized P&L + (-$0.10) - $0.02
                """)
                
        except Exception as e:
            st.warning(f"Could not load consolidated portfolio summary: {e}")
    
    
    def _render_positions_table(self, positions: List[Dict[str, Any]]):
        """Render positions in a table format with unrealized P&L."""
        if not positions:
            st.info("No positions to display")
            return
        
        # Get unrealized P&L data for all positions
        unrealized_pnl_data = {}
        try:
            all_unrealized_pnl = self.kalshi_client.get_all_unrealized_pnl()
            if all_unrealized_pnl:
                unrealized_pnl_data = all_unrealized_pnl.get('positions', {})
        except Exception as e:
            st.warning(f"Could not load unrealized P&L data: {e}")
            
        # Prepare data for table
        table_data = []
        for pos in positions:
            try:
                # Extract position data from the actual structure
                ticker = pos['ticker']
                
                # Extract values directly (values are in cents from kalshi_client)
                quantity = pos['quantity']  # This is the actual position value (int)
                position_value = pos['quantity']  # Use quantity as the position value for comparisons
                market_value_cents = pos['market_value']  # In cents
                realized_pnl_cents = pos['realized_pnl']  # In cents
                pos_cost_cents = pos["position"]['market_exposure']  # In cents
                
                # Convert to dollars
                market_value = market_value_cents / 100.0
                realized_pnl = realized_pnl_cents / 100.0
                total_cost = pos_cost_cents / 100.0
                
                # Calculate average price from total cost and quantity
                average_price = total_cost / abs(quantity) if quantity != 0 else 0
                
                # Get unrealized P&L data for this ticker
                pnl_info = unrealized_pnl_data.get(ticker, {})
                current_price = pnl_info.get('current_price', 0)
                unrealized_pnl = pnl_info.get('unrealized_pnl', 0)
                fees_paid = pnl_info.get('fees_paid', 0)
                net_pnl = pnl_info.get('net_pnl', realized_pnl)
                
                # Get market and event info for display
                market = pos.get('market')
                event = pos.get('event')
                
                # Create display title
                display_title = f"{event.title} - {market.yes_sub_title}"
                
                # Determine position direction using quantity (which is the actual position value)
                if quantity > 0:
                    direction = "LONG"
                    direction_color = "🟢"
                elif quantity < 0:
                    direction = "SHORT"
                    direction_color = "🔴"
                else:
                    direction = "CLOSED"
                    direction_color = "⚪"
                
                # Create Kalshi link for the market using event.series_ticker
                kalshi_link = f"https://kalshi.com/markets/{event.series_ticker}"
            
                table_data.append({
                    'Ticker': market.ticker,
                    'Kalshi Link': kalshi_link,
                    'Market': display_title,
                    'Direction': f"{direction_color} {direction}",
                    'Quantity': abs(quantity),
                    'Avg Price': f"${average_price:.2f}",
                    'Current Price': f"${current_price:.2f}",
                    'Market Value': f"${abs(market_value):.2f}",
                    'Unrealized P&L': f"${unrealized_pnl:.2f}",
                    'Fees': f"${fees_paid:.2f}",
                    'Net P&L': f"${net_pnl:.2f}",
                })
            except Exception as e:
                st.warning(f"Error processing position {pos['ticker']}: {e}")
                continue
        
        # Create DataFrame and display
        df = pd.DataFrame(table_data)
        
        # Sort by absolute market value (highest first)
        try:
            df['sort_value'] = df['Market Value'].str.replace('$', '').str.replace(',', '').astype(float)
            df = df.sort_values('sort_value', ascending=False)
            df = df.drop('sort_value', axis=1)
        except Exception as e:
            st.warning(f"Could not sort positions by market value: {e}")
            # Continue without sorting
        
        # Display table
        st.dataframe(
            df,
            width='stretch',
            hide_index=True,
            column_config={
                "Ticker": st.column_config.TextColumn(
                    "Ticker",
                    help="Market ticker symbol"
                ),
                "Kalshi Link": st.column_config.LinkColumn(
                    "Kalshi Link",
                    help="Click to view market on Kalshi",
                    display_text="View on Kalshi"
                ),
                "Market": st.column_config.TextColumn(
                    "Market",
                    help="Market description"
                ),
                "Direction": st.column_config.TextColumn(
                    "Direction",
                    help="Position direction (LONG/SHORT/FLAT)"
                ),
                "Quantity": st.column_config.NumberColumn(
                    "Quantity",
                    help="Number of contracts held",
                    format="%d"
                ),
                "Avg Price": st.column_config.NumberColumn(
                    "Avg Price",
                    help="Average cost basis per contract (total cost ÷ quantity)",
                    format="$%.2f"
                ),
                "Current Price": st.column_config.NumberColumn(
                    "Current Price",
                    help="Latest market price per contract (from last trade)",
                    format="$%.2f"
                ),
                "Market Value": st.column_config.NumberColumn(
                    "Market Value",
                    help="Current market value (quantity × current price)",
                    format="$%.2f"
                ),
                "Unrealized P&L": st.column_config.NumberColumn(
                    "Unrealized P&L",
                    help="Unrealized profit/loss: (current price - avg price) × quantity",
                    format="$%.2f"
                ),
                "Fees": st.column_config.NumberColumn(
                    "Fees",
                    help="Total fees paid for this position",
                    format="$%.2f"
                ),
                "Net P&L": st.column_config.NumberColumn(
                    "Net P&L",
                    help="Total P&L including realized and unrealized gains/losses minus fees",
                    format="$%.2f"
                )
            }
        )
        
        st.caption(f"Showing {len(positions)} positions")
    
    def _render_closed_positions(self):
        """Render closed positions from cached portfolio data."""
        try:
            # Get closed positions from cached portfolio data (pre-computed during data load)
            if not st.session_state.portfolio_data:
                st.info("No portfolio data available")
                return
            
            # Get raw closed positions and enrich them on-demand for display
            closed_positions = st.session_state.portfolio_data.get('closed_positions', [])
            
            if not closed_positions:
                st.info("No closed positions found")
                return
            
            # Check if we have cached enriched closed positions
            enriched_closed_positions = st.session_state.enriched_closed_positions_cache
            
            if not enriched_closed_positions:
                # Enrich closed positions on-demand for display (only when this tab is accessed)
                with st.spinner("Loading closed positions details..."):
                    enriched_closed_positions = self.kalshi_client.enrich_positions(closed_positions)
                
                # Cache the enriched positions for this session
                st.session_state.enriched_closed_positions_cache = enriched_closed_positions
            
            if not enriched_closed_positions:
                st.info("No enriched closed positions available")
                return
            
            # Process enriched closed positions for display (same format as open positions)
            table_data = []
            for enriched_pos in enriched_closed_positions:
                try:
                    # Extract data from enriched position
                    ticker = enriched_pos['ticker']
                    market = enriched_pos['market']
                    event = enriched_pos['event']
                    position_data = enriched_pos['position']
                    
                    # Create MarketPosition object for validation
                    market_pos = MarketPosition.model_validate(position_data)
                    
                    # Calculate net realized P&L (realized P&L - fees)
                    net_realized_pnl = market_pos.net_realized_pnl_dollars
                    
                    # Calculate average price from total traded and position history
                    total_traded = market_pos.total_traded_dollars_float
                    
                    # Parse timestamp and format
                    dt = datetime.fromisoformat(market_pos.last_updated_ts.replace('Z', '+00:00'))
                    formatted_time = dt.strftime('%Y-%m-%d %H:%M UTC')
                    
                    # Create display title with event and market information (same as open positions)
                    display_title = f"{event.title} - {market.yes_sub_title}"
                    
                    # Create Kalshi link using event.series_ticker (same as open positions)
                    kalshi_link = f"https://kalshi.com/markets/{event.series_ticker}"
                    
                    # Determine position direction based on trading history
                    # Since position is 0, we can't determine original direction easily
                    direction = "CLOSED"
                    direction_color = "⚪"
                    
                    table_data.append({
                        'Ticker': market.ticker,  # Use market.ticker (same as open positions)
                        'Kalshi Link': kalshi_link,
                        'Market': display_title,
                        'Direction': f"{direction_color} {direction}",
                        'Total Traded': f"${total_traded:.2f}",
                        'Fees Paid': f"${market_pos.fees_paid_dollars_float:.2f}",
                        'Net Realized P&L': f"${net_realized_pnl:.2f}",
                        'Last Updated': formatted_time
                    })
                except Exception as e:
                    logger.warning(f"Error processing enriched closed position {enriched_pos.get('ticker', 'Unknown')}: {e}")
                    continue
            
            if not table_data:
                st.info("No valid closed positions to display")
                return
            
            # Create DataFrame and display
            df = pd.DataFrame(table_data)
            
            # Sort by last updated time (most recent first)
            if 'Last Updated' in df.columns:
                df = df.sort_values('Last Updated', ascending=False)
            
            # Display table
            st.dataframe(
                df,
                width='stretch',
                hide_index=True,
                column_config={
                    "Ticker": st.column_config.TextColumn(
                        "Ticker",
                        help="Market ticker symbol"
                    ),
                    "Kalshi Link": st.column_config.LinkColumn(
                        "Kalshi Link",
                        help="Click to view market on Kalshi",
                        display_text="View on Kalshi"
                    ),
                    "Market": st.column_config.TextColumn(
                        "Market",
                        help="Market ticker"
                    ),
                    "Direction": st.column_config.TextColumn(
                        "Direction",
                        help="Position status (CLOSED)"
                    ),
                    "Total Traded": st.column_config.NumberColumn(
                        "Total Traded",
                        help="Total value traded in this market",
                        format="$%.2f"
                    ),
                    "Fees Paid": st.column_config.NumberColumn(
                        "Fees Paid",
                        help="Total fees paid for this position",
                        format="$%.2f"
                    ),
                    "Net Realized P&L": st.column_config.NumberColumn(
                        "Net Realized P&L",
                        help="Net realized profit/loss after fees",
                        format="$%.2f"
                    ),
                    "Last Updated": st.column_config.TextColumn(
                        "Last Updated",
                        help="When the position was last updated"
                    )
                }
            )
            
            st.caption(f"Showing {len(table_data)} closed positions")
            
            
                
        except Exception as e:
            st.error(f"Error loading closed positions: {e}")
            st.info("This feature requires market positions data from the portfolio API")
    
    def _render_pnl_chart(self):
        """Render P&L chart showing realized P&L over time."""
        try:
            if not st.session_state.portfolio_data:
                st.info("No portfolio data available")
                return
            
            # Get closed positions data
            closed_positions = st.session_state.portfolio_data.get('closed_positions', [])
            
            if not closed_positions:
                st.info("No closed positions found to display P&L chart")
                return
            
            # Process closed positions data for chart
            chart_data = []
            cumulative_pnl = 0
            
            # Sort positions by last updated timestamp
            sorted_positions = sorted(closed_positions, key=lambda x: x['last_updated_ts'])
            
            for pos in sorted_positions:
                # Parse timestamp
                last_updated_str = pos['last_updated_ts']
                if last_updated_str.endswith('Z'):
                    last_updated_str = last_updated_str[:-1] + '+00:00'
                
                timestamp = datetime.fromisoformat(last_updated_str)
                
                # Calculate net P&L for this position (realized P&L - fees)
                realized_pnl = pos['realized_pnl']
                fees_paid = pos['fees_paid']
                net_pnl = realized_pnl - fees_paid
                
                # Convert to dollars
                net_pnl_dollars = net_pnl / 100.0
                cumulative_pnl += net_pnl_dollars
                
                chart_data.append({
                    'Date': timestamp,
                    'Position P&L': net_pnl_dollars,
                    'Cumulative P&L': cumulative_pnl,
                    'Ticker': pos['ticker'],
                    'Realized P&L': realized_pnl / 100.0,
                    'Fees Paid': fees_paid / 100.0
                })
            
            if not chart_data:
                st.info("No valid position data available for chart")
                return
            
            # Create DataFrame
            df = pd.DataFrame(chart_data)
            
            # Chart controls
            col1, col2 = st.columns(2)
            
            with col1:
                chart_type = st.selectbox(
                    "Chart Type",
                    ["Cumulative P&L", "Position P&L", "Both"],
                    help="Choose what to display on the chart"
                )
            
            with col2:
                show_details = st.checkbox(
                    "Show Position Details",
                    help="Display individual position data below the chart"
                )
            
            # Create the chart
            if chart_type == "Cumulative P&L":
                st.line_chart(
                    df.set_index('Date')['Cumulative P&L'],
                    height=400
                )
                
                # Summary stats
                total_pnl = df['Cumulative P&L'].iloc[-1] if len(df) > 0 else 0
                st.metric("Total Realized P&L", f"${total_pnl:.2f}")
                
            elif chart_type == "Position P&L":
                st.bar_chart(
                    df.set_index('Date')['Position P&L'],
                    height=400
                )
                
                # Summary stats
                avg_pnl = df['Position P&L'].mean()
                st.metric("Average Position P&L", f"${avg_pnl:.2f}")
                
            else:  # Both
                
                fig = make_subplots(
                    rows=2, cols=1,
                    subplot_titles=('Cumulative P&L', 'Individual Position P&L'),
                    vertical_spacing=0.1
                )
                
                # Cumulative P&L line
                fig.add_trace(
                    go.Scatter(
                        x=df['Date'],
                        y=df['Cumulative P&L'],
                        mode='lines',
                        name='Cumulative P&L',
                        line=dict(color='blue', width=2)
                    ),
                    row=1, col=1
                )
                
                # Individual position P&L bars
                colors = ['green' if x >= 0 else 'red' for x in df['Position P&L']]
                fig.add_trace(
                    go.Bar(
                        x=df['Date'],
                        y=df['Position P&L'],
                        name='Position P&L',
                        marker_color=colors
                    ),
                    row=2, col=1
                )
                
                fig.update_layout(
                    height=600,
                    showlegend=True,
                    title_text="Portfolio P&L Analysis"
                )
                
                fig.update_xaxes(title_text="Date", row=2, col=1)
                fig.update_yaxes(title_text="P&L ($)", row=1, col=1)
                fig.update_yaxes(title_text="P&L ($)", row=2, col=1)
                
                st.plotly_chart(fig, width='stretch')
                
                # Summary stats
                col1, col2, col3 = st.columns(3)
                with col1:
                    total_pnl = df['Cumulative P&L'].iloc[-1] if len(df) > 0 else 0
                    st.metric("Total P&L", f"${total_pnl:.2f}")
                with col2:
                    avg_pnl = df['Position P&L'].mean()
                    st.metric("Avg Position P&L", f"${avg_pnl:.2f}")
                with col3:
                    winning_positions = len(df[df['Position P&L'] > 0])
                    total_positions = len(df)
                    win_rate = (winning_positions / total_positions * 100) if total_positions > 0 else 0
                    st.metric("Win Rate", f"{win_rate:.1f}%")
            
            # Show position details if requested
            if show_details:
                st.subheader("Position Details")
                
                # Format the data for display
                display_df = df.copy()
                display_df['Date'] = display_df['Date'].dt.strftime('%Y-%m-%d %H:%M')
                display_df['Position P&L'] = display_df['Position P&L'].apply(lambda x: f"${x:.2f}")
                display_df['Realized P&L'] = display_df['Realized P&L'].apply(lambda x: f"${x:.2f}")
                display_df['Fees Paid'] = display_df['Fees Paid'].apply(lambda x: f"${x:.2f}")
                
                st.dataframe(
                    display_df[['Date', 'Ticker', 'Position P&L', 'Realized P&L', 'Fees Paid']],
                    hide_index=True,
                    width='stretch'
                )
            
            # Date range information
            if st.session_state.portfolio_data.get('date_range_start') or st.session_state.portfolio_data.get('date_range_end'):
                date_info = "📅 Date Range: "
                if st.session_state.portfolio_data.get('date_range_start'):
                    date_info += f"{st.session_state.portfolio_data['date_range_start'].strftime('%Y-%m-%d')}"
                else:
                    date_info += "Start"
                date_info += " to "
                if st.session_state.portfolio_data.get('date_range_end'):
                    date_info += f"{st.session_state.portfolio_data['date_range_end'].strftime('%Y-%m-%d')}"
                else:
                    date_info += "End"
                st.caption(date_info)
            
            st.caption(f"Showing {len(chart_data)} closed positions")
            
        except Exception as e:
            st.error(f"Error rendering P&L chart: {e}")
            logger.error(f"P&L chart error: {e}")
    
    def _render_resting_orders(self):
        """Render resting orders."""
        st.info("Resting orders feature is not yet available")
        st.markdown("""
        **Coming Soon:**
        - View all open orders
        - Cancel orders
        - Modify order parameters
        
        This feature requires additional API endpoints that are currently being implemented.
        """)
        
        # Placeholder for future implementation
        # TODO: Implement when Kalshi API supports order management