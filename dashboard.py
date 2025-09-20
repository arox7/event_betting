"""
Streamlit dashboard for Kalshi market making bot.
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timezone
import logging

from kalshi_client import KalshiAPIClient
from market_screener import MarketScreener
from gemini_screener import GeminiScreener
from config import Config
from models import utc_now

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
        self.gemini_screener = GeminiScreener(self.config)
        
        # Initialize session state
        session_defaults = {
            'screening_results': [],
            'last_update': None,
            'initial_load_complete': False
        }
        
        for key, default_value in session_defaults.items():
            if key not in st.session_state:
                st.session_state[key] = default_value
    
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
        
        # Auto-refresh on first load
        if not st.session_state.initial_load_complete:
            self._initial_data_load()
        
        # AI Components at the top
        self._render_ai_components_top()
        
        # Main content with tabs
        self._render_tabbed_content()
    
    def _initial_data_load(self):
        """Perform initial data load when dashboard first starts."""
        try:
            # Show loading message in a toast/temporary area
            with st.spinner("🔄 Loading market data..."):
                # Refresh markets data
                self._refresh_markets()
            
            # Mark initial load as complete
            st.session_state.initial_load_complete = True
            
            # Show brief success message if data was loaded
            if st.session_state.screening_results:
                st.success(f"✅ Loaded {len(st.session_state.screening_results)} markets successfully!")
            
        except Exception as e:
            st.error(f"❌ Failed to load initial data: {e}")
            st.session_state.initial_load_complete = True  # Mark as complete even on error to avoid infinite retries
    
    def _render_ai_components_top(self):
        """Render AI components at the top of the interface."""
        st.header("🤖 AI-Powered Tools")
        
        # Two columns for the AI tools
        col1, col2 = st.columns(2)
        
        with col1:
            self._render_bespoke_screening_compact()
        
        with col2:
            self._render_ai_quick_actions()
        
        st.divider()
    
    def _render_sidebar(self):
        """Render sidebar controls."""
        st.sidebar.header("🎛️ Controls")
        
        # API Status
        st.sidebar.subheader("API Status")
        if self.kalshi_client.health_check():
            st.sidebar.success("✅ Kalshi API Connected")
        else:
            st.sidebar.error("❌ Kalshi API Disconnected")
        
        # Portfolio Overview (if authenticated)
        self._render_portfolio_overview()
        
        # Refresh controls
        st.sidebar.subheader("Refresh Controls")
        if st.sidebar.button("🔄 Refresh Markets", type="primary"):
            self._refresh_markets()
        
        st.sidebar.caption("💡 Tip: Click 'Refresh Markets' after changing criteria to see updated results")
        
        # Screening criteria with organized sections
        self._render_screening_criteria()
    
    def _render_portfolio_overview(self):
        """Render comprehensive portfolio overview in sidebar."""
        st.sidebar.subheader("💰 Portfolio Overview")
        
        try:
            # Get portfolio metrics using shared method
            portfolio_metrics = self.kalshi_client.get_portfolio_metrics()
            
            if portfolio_metrics is None:
                st.sidebar.info("💡 Login required for portfolio data")
                return
            
            # Main portfolio metrics
            cash_balance = portfolio_metrics.get('cash_balance', 0)
            total_market_value = portfolio_metrics.get('total_market_value', 0)
            total_portfolio_value = portfolio_metrics.get('total_portfolio_value', 0)
            total_unrealized_pnl = portfolio_metrics.get('total_unrealized_pnl', 0)
            total_positions = portfolio_metrics.get('total_positions', 0)
            
            # Display main balance metrics
            col1, col2 = st.sidebar.columns(2)
            with col1:
                st.metric(
                    "Total Balance",
                    f"${total_portfolio_value:.2f}",
                    delta=f"${total_unrealized_pnl:.2f}" if total_unrealized_pnl != 0 else None,
                    delta_color="normal"
                )
            with col2:
                st.metric("Positions", f"{total_positions}")
            
            # Balance breakdown
            st.sidebar.markdown("**Balance Breakdown**")
            
            # Create a simple breakdown chart
            if total_portfolio_value > 0:
                cash_pct = (cash_balance / total_portfolio_value) * 100
                position_pct = (total_market_value / total_portfolio_value) * 100
                
                st.sidebar.markdown(f"💵 Cash: ${cash_balance:.2f} ({cash_pct:.1f}%)")
                st.sidebar.markdown(f"📊 Positions: ${total_market_value:.2f} ({position_pct:.1f}%)")
                
                # Progress bars for visual representation
                st.sidebar.progress(cash_pct / 100, text=f"Cash {cash_pct:.1f}%")
                st.sidebar.progress(position_pct / 100, text=f"Positions {position_pct:.1f}%")
            else:
                st.sidebar.markdown(f"💵 Cash: ${cash_balance:.2f}")
                st.sidebar.markdown(f"📊 Positions: ${total_market_value:.2f}")
            
            # 24h Trading Performance
            st.sidebar.markdown("**24h Trading Activity**")
            
            # Get recent trading P&L
            recent_pnl_data = self.kalshi_client.get_recent_pnl(hours=24)
            
            if recent_pnl_data:
                realized_pnl = recent_pnl_data.get('realized_pnl', 0)
                trade_count = recent_pnl_data.get('trade_count', 0)
                trade_volume = recent_pnl_data.get('trade_volume', 0)
                
                # Calculate return percentage based on total portfolio value
                return_pct = 0
                if total_portfolio_value > 0 and realized_pnl != 0:
                    return_pct = (realized_pnl / total_portfolio_value) * 100
                
                # Display PnL metrics
                pnl_color = "normal" if realized_pnl >= 0 else "inverse"
                pnl_delta = f"{return_pct:+.2f}%" if return_pct != 0 else None
                
                col1, col2 = st.sidebar.columns(2)
                with col1:
                    st.metric(
                        "24h P&L",
                        f"${realized_pnl:+.2f}",
                        delta=pnl_delta,
                        delta_color=pnl_color
                    )
                with col2:
                    st.metric("Trades", f"{trade_count}")
                
                # Additional metrics
                if trade_volume > 0:
                    st.sidebar.metric("Volume", f"${trade_volume:.2f}")
                
                # Performance indicator
                if realized_pnl > 0:
                    st.sidebar.success(f"📈 Profitable trading day (+{return_pct:.2f}%)")
                elif realized_pnl < 0:
                    st.sidebar.error(f"📉 Trading loss ({return_pct:.2f}%)")
                elif trade_count > 0:
                    st.sidebar.info("📊 Break-even trading")
                else:
                    st.sidebar.info("💤 No trades today")
                
                st.sidebar.caption("📊 Based on realized gains/losses from completed trades")
            
            # Top positions (if any)
            enriched_positions = portfolio_metrics.get('enriched_positions', [])
            if enriched_positions:
                st.sidebar.markdown("**Top Positions**")
                
                # Sort positions by absolute market value
                sorted_positions = sorted(enriched_positions, key=lambda x: abs(x.get('market_value', 0)), reverse=True)
                
                # Show top 3 positions
                for i, position in enumerate(sorted_positions[:3]):
                    ticker = position.get('ticker', 'Unknown')
                    market_value = abs(position.get('market_value', 0)) / 100.0  # Convert cents to dollars
                    quantity = position.get('quantity', 0)
                    
                    # Determine position direction
                    direction = "📈" if quantity > 0 else "📉"
                    
                    st.sidebar.markdown(f"{direction} {ticker}: ${market_value:.2f}")
                
                if len(enriched_positions) > 3:
                    st.sidebar.markdown(f"... and {len(enriched_positions) - 3} more positions")
            
            # Refresh button for portfolio data
            if st.sidebar.button("🔄 Refresh Portfolio", key="refresh_portfolio"):
                st.rerun()
                
        except Exception as e:
            st.sidebar.error(f"❌ Portfolio data unavailable: {str(e)}")
            logger.error(f"Error rendering portfolio overview: {e}")
    
    def _render_screening_criteria(self):
        """Render comprehensive screening criteria with organized sections."""
        st.sidebar.subheader("🎯 Screening Criteria")
        
        # Show warning if AI screening is active
        screening_mode = st.session_state.get('screening_mode', 'standard')
        if screening_mode in ['bespoke', 'bespoke_custom']:
            st.sidebar.warning("⚠️ **AI Screening Active** - Manual criteria below are not currently being used. AI is using dynamic criteria based on your query.")
            st.sidebar.info("💡 Use 'Return to Standard Screening' to use manual criteria again.")
        
        # Initialize session state for criteria if not exists
        if 'screening_criteria' not in st.session_state:
            st.session_state.screening_criteria = {
                'min_volume': self.config.MIN_VOLUME,
                'min_volume_24h': self.config.MIN_VOLUME_24H,
                'max_spread_percentage': self.config.MAX_SPREAD_PERCENTAGE,
                'max_spread_cents': self.config.MAX_SPREAD_CENTS,
                'min_spread_cents': self.config.MIN_SPREAD_CENTS,
                'min_liquidity': self.config.MIN_LIQUIDITY,
                'max_time_to_close_days': self.config.MAX_TIME_TO_CLOSE_DAYS,
                'min_open_interest': self.config.MIN_OPEN_INTEREST,
                'categories': None
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
        
        # Time & Close Section
        with st.sidebar.expander("⏰ Time & Close", expanded=True):
            max_days = st.number_input(
                "Max Days to Close", 
                step=1,
                help="Maximum days until market close"
            )
        
        # Category Filtering Section
        with st.sidebar.expander("🏷️ Category Filtering", expanded=False):
            # Get available categories from current results
            available_categories = self._get_available_categories()
            
            if available_categories:
                # Add "All Categories" option
                category_options = ["All Categories"] + available_categories
                
                # Get current selection
                current_category = st.session_state.screening_criteria.get('categories', None)
                if current_category and len(current_category) == 1:
                    current_selection = current_category[0]
                else:
                    current_selection = "All Categories"
                
                selected_category = st.selectbox(
                    "Filter by Category",
                    options=category_options,
                    index=category_options.index(current_selection) if current_selection in category_options else 0,
                    help="Select a category to filter markets (or 'All Categories' for no filtering)"
                )
                
                # Convert selection to criteria format
                if selected_category == "All Categories":
                    selected_categories = None
                else:
                    selected_categories = [selected_category]
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
            'max_time_to_close_days': max_days,
            'min_open_interest': min_open_interest,
            'categories': selected_categories
        })
        
        # Apply criteria button
        if st.sidebar.button("🔄 Apply Criteria & Refresh", type="primary", use_container_width=True):
            self._apply_screening_criteria()
        
        # Return to standard screening button (if in bespoke mode)
        if st.session_state.get('screening_mode', 'standard') in ['bespoke', 'bespoke_custom']:
            if st.sidebar.button("🔙 Return to Standard Screening", use_container_width=True):
                self._return_to_standard_screening()
        
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
            max_time_to_close_days=criteria['max_time_to_close_days'],
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
                    'max_time_to_close_days', 'min_open_interest'
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
    
    def _render_bespoke_screening_compact(self):
        """Render compact bespoke screening section at the top."""
        st.subheader("🔍 AI Market Finder")
        
        if not self.gemini_screener.is_available():
            st.warning("⚠️ Gemini API not configured. Set GEMINI_API_KEY in your .env file.")
            return
        
        # Text input for user query
        user_query = st.text_area(
            "Describe what markets you're looking for:",
            placeholder="e.g., find all markets closing in the next hour with volume > 5000",
            height=80,
            key="bespoke_query_top"
        )
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("🔍 Generate & Run", type="primary", disabled=not user_query.strip(), key="run_top"):
                self._run_bespoke_screening(user_query.strip())
        
        with col2:
            if st.button("👁️ Preview Code", disabled=not user_query.strip(), key="preview_top"):
                self._preview_bespoke_code(user_query.strip())
        
        with col3:
            with st.expander("💡 Examples"):
                st.markdown("""
                - "find markets closing in the next 30 minutes"
                - "show me markets with volume > 10,000 and spread < 3 cents"
                - "markets about elections with high volatility"
                - "find undervalued markets trading below 20 cents"
                """)
        
        # Show generated code if available
        if 'bespoke_code' in st.session_state and st.session_state.bespoke_code:
            with st.expander("📝 Generated Code", expanded=False):
                st.code(st.session_state.bespoke_code, language="python")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✏️ Edit & Run", key="edit_bespoke_top"):
                        st.session_state.show_code_editor = True
                with col2:
                    if st.button("🗑️ Clear Code", key="clear_code_top"):
                        if 'bespoke_code' in st.session_state:
                            del st.session_state.bespoke_code
                        st.rerun()
        
        # Code editor for manual editing
        if st.session_state.get('show_code_editor', False):
            st.markdown("**Edit the code:**")
            edited_code = st.text_area(
                "Python code:",
                value=st.session_state.get('bespoke_code', ''),
                height=200,
                key="edited_bespoke_code_top"
            )
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("▶️ Run Edited Code", key="run_edited_top"):
                    self._run_bespoke_screening_with_code(edited_code)
            with col2:
                if st.button("❌ Cancel Edit", key="cancel_edit_top"):
                    st.session_state.show_code_editor = False
                    st.rerun()
    
    def _render_ai_quick_actions(self):
        """Render AI quick actions section."""
        st.subheader("⚡ Quick AI Actions")
        
        if not self.gemini_screener.is_available():
            st.info("AI features require Gemini API key")
            return
        
        st.markdown("**Popular screening patterns:**")
        
        # Quick action buttons
        if st.button("🕒 Closing Soon", use_container_width=True, key="quick_closing"):
            self._run_bespoke_screening("find markets closing in the next 2 hours")
        
        if st.button("📈 High Volume", use_container_width=True, key="quick_volume"):
            self._run_bespoke_screening("show me markets with volume > 5000 and tight spreads")
        
        if st.button("💰 Undervalued", use_container_width=True, key="quick_undervalued"):
            self._run_bespoke_screening("find undervalued markets trading below 30 cents with decent volume")
        
        if st.button("🗳️ Elections", use_container_width=True, key="quick_elections"):
            self._run_bespoke_screening("show me all election-related markets")
        
        # Return to standard screening if in bespoke mode
        if st.session_state.get('screening_mode', 'standard') in ['bespoke', 'bespoke_custom']:
            st.divider()
            if st.button("🔙 Return to Standard Screening", use_container_width=True, key="return_standard_top"):
                self._return_to_standard_screening()
    
    def _render_bespoke_screening(self):
        """Render bespoke screening section with Gemini integration."""
        with st.sidebar.expander("🤖 AI-Powered Bespoke Screening", expanded=False):
            if not self.gemini_screener.is_available():
                st.warning("⚠️ Gemini API not configured. Set GEMINI_API_KEY in your .env file.")
                st.markdown("Get your API key at: https://makersuite.google.com/app/apikey")
                return
            
            st.markdown("**Describe what markets you're looking for:**")
            
            # Examples to help users
            with st.expander("💡 Example Queries", expanded=False):
                st.markdown("""
                - "find markets closing in the next 30 minutes"
                - "show me markets with volume > 10,000 and spread < 3 cents"
                - "markets about elections with high volatility"
                - "find undervalued markets trading below 20 cents"
                - "markets closing today with low volume"
                - "show me markets with recent price movement"
                """)
            
            # Text input for user query
            user_query = st.text_area(
                "Your query:",
                placeholder="e.g., find all markets closing in the next hour with volume > 5000",
                height=100,
                key="bespoke_query"
            )
            
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("🔍 Generate & Run", type="primary", disabled=not user_query.strip()):
                    self._run_bespoke_screening(user_query.strip())
            
            with col2:
                if st.button("👁️ Preview Code", disabled=not user_query.strip()):
                    self._preview_bespoke_code(user_query.strip())
            
            # Show generated code if available
            if 'bespoke_code' in st.session_state and st.session_state.bespoke_code:
                st.markdown("**Generated Code:**")
                st.code(st.session_state.bespoke_code, language="python")
                
                # Option to edit and re-run
                if st.button("✏️ Edit & Run", key="edit_bespoke"):
                    st.session_state.show_code_editor = True
            
            # Code editor for manual editing
            if st.session_state.get('show_code_editor', False):
                st.markdown("**Edit the code:**")
                edited_code = st.text_area(
                    "Python code:",
                    value=st.session_state.get('bespoke_code', ''),
                    height=200,
                    key="edited_bespoke_code"
                )
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("▶️ Run Edited Code"):
                        self._run_bespoke_screening_with_code(edited_code)
                with col2:
                    if st.button("❌ Cancel Edit"):
                        st.session_state.show_code_editor = False
                        st.rerun()
    
    def _preview_bespoke_code(self, user_query: str):
        """Generate and preview the bespoke screening code without running it."""
        with st.spinner("🤖 Generating screening code..."):
            code = self.gemini_screener.generate_screening_function(user_query)
            
            if code:
                st.session_state.bespoke_code = code
                st.success("✅ Code generated successfully! Review it below.")
                st.rerun()
            else:
                st.error("❌ Failed to generate screening code. Please try rephrasing your query.")
    
    def _run_bespoke_screening(self, user_query: str):
        """Generate and run bespoke screening based on user query."""
        with st.spinner("🤖 Generating and running screening..."):
            # Set AI screening mode first to ensure we get unfiltered data
            st.session_state.screening_mode = "bespoke"
            st.session_state.bespoke_query = user_query
            
            # If we're coming from standard mode, refresh to get all markets
            current_mode = st.session_state.get('screening_mode', 'standard')
            if not st.session_state.get('screening_results') or current_mode == 'standard':
                st.info("🔄 Loading all markets for AI screening...")
                self._refresh_markets()
            
            # Generate the screening function
            code = self.gemini_screener.generate_screening_function(user_query)
            
            if not code:
                st.error("❌ Failed to generate screening code. Please try rephrasing your query.")
                return
            
            st.session_state.bespoke_code = code
            
            # Get current markets and events
            if not st.session_state.screening_results:
                st.warning("⚠️ No market data available. Please try again.")
                return
            
            # Use the existing market-event pairs from screening results
            try:
                results = self.gemini_screener.execute_screening_function_from_results(code, st.session_state.screening_results)
                
                if results:
                    # Replace current screening results with bespoke results
                    st.session_state.screening_results = results
                    st.session_state.last_update = utc_now()
                    
                    passing_count = len([r for r in results if r.score > 0])
                    st.success(f"✅ AI screening complete! Found {passing_count} matching markets out of {len(results)} total.")
                    st.rerun()
                else:
                    st.error("❌ Failed to execute screening. Please check the generated code.")
                    st.error("💡 Check the logs for detailed error information.")
            
            except ValueError as e:
                error_msg = str(e)
                if "Critical market validation failed" in error_msg or "Critical NoneType error" in error_msg:
                    st.error("🚨 **CRITICAL ERROR**: Market data validation failed!")
                    st.error("**This indicates a serious issue with the market data from the API.**")
                    st.error(f"**Error details**: {error_msg}")
                    st.error("**Next steps**:")
                    st.markdown("1. Check the application logs for detailed error information")
                    st.markdown("2. Try refreshing the markets data")
                    st.markdown("3. If the issue persists, there may be a problem with the Kalshi API response format")
                    st.stop()  # Stop execution to prevent further errors
                else:
                    st.error(f"❌ Screening failed: {error_msg}")
                    st.error("💡 Check the logs for detailed error information.")
            
            except Exception as e:
                st.error(f"❌ Unexpected error during screening: {str(e)}")
                st.error("💡 Check the logs for detailed error information.")
    
    def _run_bespoke_screening_with_code(self, code: str):
        """Run bespoke screening with user-provided/edited code."""
        with st.spinner("▶️ Running custom screening code..."):
            # Ensure we're in AI screening mode
            st.session_state.screening_mode = "bespoke_custom"
            st.session_state.bespoke_code = code
            
            # Get current markets and events
            if not st.session_state.screening_results:
                st.warning("⚠️ No market data available. Please refresh markets first.")
                return
            
            # Use the existing market-event pairs from screening results
            try:
                results = self.gemini_screener.execute_screening_function_from_results(code, st.session_state.screening_results)
                
                if results:
                    # Replace current screening results with bespoke results
                    st.session_state.screening_results = results
                    st.session_state.last_update = utc_now()
                    st.session_state.screening_mode = "bespoke_custom"
                    st.session_state.bespoke_code = code
                    st.session_state.show_code_editor = False
                    
                    passing_count = len([r for r in results if r.score > 0])
                    st.success(f"✅ Custom AI screening complete! Found {passing_count} matching markets out of {len(results)} total.")
                    st.rerun()
                else:
                    st.error("❌ Failed to execute screening. Please check your code.")
                    st.error("💡 Check the logs for detailed error information.")
            
            except ValueError as e:
                error_msg = str(e)
                if "Critical market validation failed" in error_msg or "Critical NoneType error" in error_msg:
                    st.error("🚨 **CRITICAL ERROR**: Market data validation failed!")
                    st.error("**This indicates a serious issue with the market data from the API.**")
                    st.error(f"**Error details**: {error_msg}")
                    st.error("**Next steps**:")
                    st.markdown("1. Check the application logs for detailed error information")
                    st.markdown("2. Try refreshing the markets data")
                    st.markdown("3. If the issue persists, there may be a problem with the Kalshi API response format")
                    st.stop()  # Stop execution to prevent further errors
                else:
                    st.error(f"❌ Custom screening failed: {error_msg}")
                    st.error("💡 Check the logs for detailed error information.")
            
            except Exception as e:
                st.error(f"❌ Unexpected error during custom screening: {str(e)}")
                st.error("💡 Check the logs for detailed error information.")
    
    def _return_to_standard_screening(self):
        """Return to standard screening mode."""
        # Clear bespoke screening state
        if 'screening_mode' in st.session_state:
            del st.session_state.screening_mode
        if 'bespoke_query' in st.session_state:
            del st.session_state.bespoke_query
        if 'bespoke_code' in st.session_state:
            del st.session_state.bespoke_code
        if 'show_code_editor' in st.session_state:
            del st.session_state.show_code_editor
        
        # Refresh with standard screening (this will now apply manual screening criteria)
        st.info("🔄 Switching back to standard screening with manual criteria...")
        self._refresh_markets()
    
    def _render_tabbed_content(self):
        """Render main content with tabs."""
        tab1, tab2 = st.tabs(["🎯 Market Screening", "💼 Portfolio Overview"])
        
        with tab1:
            self._render_main_content()
        
        with tab2:
            self._render_portfolio_tab()
    
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
            st.metric("Passing Markets", summary['passing_markets'])
        
        with col3:
            st.metric("Pass Rate", f"{summary['pass_rate']:.1%}")
        
        with col4:
            st.metric("Failing Markets", summary['total_markets'] - summary['passing_markets'])
        
        # Current criteria summary - show different info based on screening mode
        screening_mode = st.session_state.get('screening_mode', 'standard')
        
        if screening_mode in ['bespoke', 'bespoke_custom']:
            # Show AI screening information
            with st.expander("🤖 Current AI Screening Criteria", expanded=False):
                if screening_mode == 'bespoke' and 'bespoke_query' in st.session_state:
                    st.markdown("**AI Query:**")
                    st.text(st.session_state.bespoke_query)
                elif screening_mode == 'bespoke_custom':
                    st.markdown("**Custom AI Screening:**")
                    st.text("Using custom-edited AI code")
                
                if 'bespoke_code' in st.session_state and st.session_state.bespoke_code:
                    st.markdown("**Generated Screening Logic:**")
                    # Show a condensed version of the code
                    code_lines = st.session_state.bespoke_code.split('\n')
                    # Find the main logic (skip function definition and docstring)
                    logic_lines = []
                    in_logic = False
                    for line in code_lines:
                        if 'passes = ' in line or 'reasons = ' in line or in_logic:
                            in_logic = True
                            if line.strip() and not line.strip().startswith('"""') and not line.strip().startswith('#'):
                                logic_lines.append(line.strip())
                        if 'return passes, reasons' in line:
                            logic_lines.append(line.strip())
                            break
                    
                    if logic_lines:
                        st.code('\n'.join(logic_lines[:6]) + ('...' if len(logic_lines) > 6 else ''), language='python')
                    
                    if st.button("📝 View Full Code", key="view_full_ai_code"):
                        st.code(st.session_state.bespoke_code, language='python')
                
                st.info("💡 AI screening uses dynamic criteria based on your natural language query, not the manual criteria below.")
        
        elif 'screening_criteria' in st.session_state:
            # Show manual screening criteria
            criteria = st.session_state.screening_criteria
            with st.expander("📋 Current Manual Screening Criteria", expanded=False):
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
                    st.markdown("**Time & Close**")
                    st.text(f"Max Days: {criteria['max_time_to_close_days']}")
        
        # Last update time and screening mode
        if st.session_state.last_update:
            update_text = f"Last updated: {st.session_state.last_update.strftime('%H:%M:%S')}"
            
            # Show screening mode with enhanced clarity
            if screening_mode == 'bespoke':
                query = st.session_state.get('bespoke_query', 'Unknown query')
                update_text += f" | 🤖 **AI Screening**: \"{query[:50]}{'...' if len(query) > 50 else ''}\""
            elif screening_mode == 'bespoke_custom':
                update_text += " | 🤖 **AI Screening**: Custom Code"
            else:
                update_text += " | 📊 **Manual Screening**: Using criteria from sidebar"
            
            st.caption(update_text)
    
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
            show_only_passing = st.checkbox("Show only passing markets", value=True)
        with col2:
            search_term = st.text_input("🔍 Search events", placeholder="Search by event title...", help="Filter events by title")
        
        # Filter results
        if show_only_passing:
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
            
            # Create event title - this should always be present
            event_title = event.title
            
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
                'Market Title': market.title,
                'Category': event.category or "Unknown",
                'Score': f"{result.score:.2f}",
                'Status': "✅ Pass" if result.score > 0 else "❌ Fail",
                'Total Volume': f"{market.volume:,}" if market.volume else "0",
                '24h Volume': f"{volume_24h:,}" if volume_24h else "0",
                'Open Interest': f"{market.open_interest:,}" if market.open_interest else "0",
                'Spread %': f"{market.spread_percentage:.1%}" if market.spread_percentage else "N/A",
                'Spread (¢)': f"{spread_cents}¢" if spread_cents else "N/A",
                'Mid Price': f"{market.mid_price:.2f}" if market.mid_price else "N/A",
                'Days to Close': market.days_to_close,
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
                "Days to Close": st.column_config.NumberColumn(
                    "Days to Close",
                    help="Days until market closes"
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
        passing_count = len([r for r in filtered_results if r.score == 1.0])
        
        # Build caption with filter info
        caption_parts = [f"Showing {len(filtered_results)} markets ({passing_count} passing, {len(filtered_results) - passing_count} failing)"]
        
        if search_term:
            caption_parts.append(f"filtered by '{search_term}'")
        
        if show_only_passing:
            caption_parts.append("(passing only)")
        
        st.caption(" | ".join(caption_parts))
    
    def _render_score_distribution(self):
        """Render pass/fail distribution chart."""
        st.subheader("📊 Pass/Fail Distribution")
        
        if not st.session_state.screening_results:
            st.info("No data available")
            return
        
        passing = len([r for r in st.session_state.screening_results if r.score == 1.0])
        failing = len([r for r in st.session_state.screening_results if r.score == 0.0])
        
        fig = px.pie(
            values=[passing, failing],
            names=['Passing', 'Failing'],
            title="Markets Passing vs Failing Criteria",
            color_discrete_map={'Passing': 'green', 'Failing': 'red'}
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
            event_info = f" ({r.event.title})"
            key = f"{r.market.ticker} - {r.market.title}{event_info}"
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
                st.markdown(f"**Days to Close:** {market.days_to_close}")
                st.markdown(f"**Close Date:** {market.close_date.strftime('%Y-%m-%d %H:%M')}")
            
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
                
                # Check if AI screening is active
                screening_mode = st.session_state.get('screening_mode', 'standard')
                
                if screening_mode in ['bespoke', 'bespoke_custom']:
                    # For AI screening, create screening results without manual filtering
                    from models import ScreeningResult
                    results = []
                    for event in events:
                        for market in event.markets:
                            # Create screening result with score 1.0 (passing) for AI to filter
                            result = ScreeningResult(
                                market=market,
                                event=event,
                                score=1.0,  # Let AI do the filtering
                                reasons=["Available for AI screening"]
                            )
                            results.append(result)
                else:
                    # Standard manual screening
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
                
                if screening_mode in ['bespoke', 'bespoke_custom']:
                    st.success(f"Refreshed {len(events)} events ({total_markets} markets) - Ready for AI screening")
                else:
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
    
    def _render_portfolio_tab(self):
        """Render comprehensive portfolio overview tab."""
        st.header("💼 Portfolio Overview")
        
        try:
            # Get portfolio metrics using shared method
            with st.spinner("Loading portfolio data..."):
                portfolio_metrics = self.kalshi_client.get_portfolio_metrics()
            
            if portfolio_metrics is None:
                st.error("❌ Unable to load portfolio data. Please check your API credentials.")
                return
            
            # Extract metrics from shared method
            cash_balance = portfolio_metrics.get('cash_balance', 0)
            total_positions = portfolio_metrics.get('total_positions', 0)
            total_market_value = portfolio_metrics.get('total_market_value', 0)
            total_unrealized_pnl = portfolio_metrics.get('total_unrealized_pnl', 0)
            total_portfolio_value = portfolio_metrics.get('total_portfolio_value', 0)
            enriched_positions = portfolio_metrics.get('enriched_positions', [])
            
            # Portfolio Summary Section
            st.subheader("📊 Portfolio Summary")
            
            # Display key metrics
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Cash Balance", f"${cash_balance:.2f}")
            
            with col2:
                st.metric("Total Positions", total_positions)
            
            with col3:
                st.metric(
                    "Market Value", 
                    f"${total_market_value:.2f}",
                    delta=f"${total_unrealized_pnl:.2f}" if total_unrealized_pnl != 0 else None,
                    delta_color="normal" if total_unrealized_pnl >= 0 else "inverse"
                )
            
            with col4:
                st.metric("Total Portfolio", f"${total_portfolio_value:.2f}")
            
            if not enriched_positions:
                st.info("📭 No positions found in your portfolio.")
                return
            
            # Position Analysis
            st.subheader("📈 Position Analysis")
            
            # Create tabs for different views
            pos_tab1, pos_tab2, pos_tab3 = st.tabs(["📋 All Positions", "🏆 Winners & Losers", "📊 Analytics"])
            
            with pos_tab1:
                self._render_positions_table(enriched_positions)
            
            with pos_tab2:
                self._render_winners_losers(enriched_positions)
            
            with pos_tab3:
                self._render_portfolio_analytics(enriched_positions, portfolio_metrics)
                
        except Exception as e:
            st.error(f"❌ Error loading portfolio data: {e}")
            logger.error(f"Portfolio tab error: {e}")
    
    def _render_positions_table(self, enriched_positions):
        """Render detailed positions table."""
        st.markdown("### 📋 Current Positions")
        
        # Filters
        col1, col2, col3 = st.columns(3)
        
        with col1:
            show_closed_markets = st.checkbox("Include closed markets", value=False)
        
        with col2:
            min_position_value = st.number_input("Min position value ($)", min_value=0.0, value=0.0, step=1.0)
        
        with col3:
            search_term = st.text_input("Search positions", placeholder="Search by ticker or event...")
        
        # Filter positions
        filtered_positions = enriched_positions
        
        if not show_closed_markets:
            filtered_positions = [
                pos for pos in filtered_positions 
                if pos.get('market') and pos['market'].status == 'active'
            ]
        
        if min_position_value > 0:
            filtered_positions = [
                pos for pos in filtered_positions 
                if abs(pos.get('market_value', 0)) / 100.0 >= min_position_value
            ]
        
        if search_term:
            search_lower = search_term.lower()
            filtered_positions = [
                pos for pos in filtered_positions
                if (search_lower in pos.get('ticker', '').lower() or
                    (pos.get('event') and search_lower in pos['event'].title.lower()) or
                    (pos.get('market') and search_lower in pos['market'].title.lower()))
            ]
        
        if not filtered_positions:
            st.info("No positions match the current filters.")
            return
        
        # Create positions DataFrame
        positions_data = []
        for pos in filtered_positions:
            market = pos.get('market')
            event = pos.get('event')
            
            quantity = pos.get('quantity', 0)
            market_value = pos.get('market_value', 0) / 100.0
            unrealized_pnl = pos.get('unrealized_pnl', 0) / 100.0
            
            # Determine position direction and side
            if quantity > 0:
                side = "YES"
                direction = "📈"
                shares = quantity
            else:
                side = "NO" 
                direction = "📉"
                shares = abs(quantity)
            
            # Market info - these should always be present
            market_title = market.title
            event_title = event.title
            
            # Current market price
            if market:
                if side == "YES":
                    current_price = (market.yes_bid + market.yes_ask) / 2 if market.yes_bid and market.yes_ask else market.last_price
                else:
                    current_price = (market.no_bid + market.no_ask) / 2 if market.no_bid and market.no_ask else (1.0 - market.last_price if market.last_price else None)
            else:
                current_price = None
            
            # Status
            status = market.status if market else "Unknown"
            status_emoji = {"active": "🟢", "closed": "🔴", "settled": "✅"}.get(status, "⚪")
            
            # Days to close
            days_to_close = market.days_to_close if market else "N/A"
            
            positions_data.append({
                'Ticker': pos.get('ticker', 'N/A'),
                'Event': event_title,
                'Market': market_title,
                'Side': f"{direction} {side}",
                'Shares': f"{shares:,}",
                'Market Value': f"${market_value:.2f}",
                'Unrealized P&L': f"${unrealized_pnl:+.2f}",
                'Current Price': f"{current_price:.2f}" if current_price else "N/A",
                'Status': f"{status_emoji} {status.title()}",
                'Days to Close': days_to_close,
                'Kalshi Link': f"https://kalshi.com/events/{market.event_ticker}"
            })
        
        df = pd.DataFrame(positions_data)
        
        # Display table
        st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            disabled=True,
            column_config={
                "Kalshi Link": st.column_config.LinkColumn(
                    "Kalshi Link",
                    help="View market on Kalshi",
                    display_text="🔗 View"
                ),
                "Market Value": st.column_config.TextColumn(
                    "Market Value",
                    help="Current market value of position"
                ),
                "Unrealized P&L": st.column_config.TextColumn(
                    "Unrealized P&L",
                    help="Unrealized profit/loss"
                ),
                "Current Price": st.column_config.TextColumn(
                    "Current Price",
                    help="Current market price"
                )
            }
        )
        
        st.caption(f"Showing {len(filtered_positions)} positions")
    
    def _render_winners_losers(self, enriched_positions):
        """Render winners and losers analysis."""
        st.markdown("### 🏆 Winners & Losers")
        
        if not enriched_positions:
            st.info("No positions to analyze.")
            return
        
        # Sort by unrealized P&L
        sorted_positions = sorted(
            enriched_positions, 
            key=lambda x: x.get('unrealized_pnl', 0), 
            reverse=True
        )
        
        # Split into winners and losers
        winners = [pos for pos in sorted_positions if pos.get('unrealized_pnl', 0) > 0]
        losers = [pos for pos in sorted_positions if pos.get('unrealized_pnl', 0) < 0]
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 🎉 Top Winners")
            if winners:
                for i, pos in enumerate(winners[:5]):
                    market = pos.get('market')
                    event = pos.get('event')
                    pnl = pos.get('unrealized_pnl', 0) / 100.0
                    
                    event_name = event.title
                    
                    st.success(f"**{pos.get('ticker', 'N/A')}** - {event_name}")
                    st.markdown(f"💰 **+${pnl:.2f}** unrealized gain")
                    if i < len(winners) - 1:
                        st.markdown("---")
            else:
                st.info("No winning positions yet.")
        
        with col2:
            st.markdown("#### 📉 Top Losers")
            if losers:
                for i, pos in enumerate(losers[:5]):
                    market = pos.get('market')
                    event = pos.get('event')
                    pnl = pos.get('unrealized_pnl', 0) / 100.0
                    
                    event_name = event.title
                    
                    st.error(f"**{pos.get('ticker', 'N/A')}** - {event_name}")
                    st.markdown(f"💸 **${pnl:.2f}** unrealized loss")
                    if i < len(losers) - 1:
                        st.markdown("---")
            else:
                st.info("No losing positions.")
    
    def _render_portfolio_analytics(self, enriched_positions, portfolio_metrics):
        """Render portfolio analytics and charts."""
        st.markdown("### 📊 Portfolio Analytics")
        
        if not enriched_positions:
            st.info("No positions to analyze.")
            return
        
        # Portfolio composition by category
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 📂 Portfolio by Category")
            category_values = {}
            for pos in enriched_positions:
                event = pos.get('event')
                if event and event.category:
                    category = event.category
                    market_value = abs(pos.get('market_value', 0)) / 100.0
                    category_values[category] = category_values.get(category, 0) + market_value
            
            if category_values:
                fig = px.pie(
                    values=list(category_values.values()),
                    names=list(category_values.keys()),
                    title="Position Value by Category"
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No category data available.")
        
        with col2:
            st.markdown("#### 💰 P&L Distribution")
            pnl_data = []
            labels = []
            
            for pos in enriched_positions:
                pnl = pos.get('unrealized_pnl', 0) / 100.0
                if pnl != 0:
                    pnl_data.append(pnl)
                    ticker = pos.get('ticker', 'Unknown')
                    labels.append(f"{ticker}: ${pnl:+.2f}")
            
            if pnl_data:
                fig = go.Figure()
                colors = ['green' if x > 0 else 'red' for x in pnl_data]
                
                fig.add_trace(go.Bar(
                    x=labels[:10],  # Show top 10
                    y=pnl_data[:10],
                    marker_color=colors[:10],
                    text=[f"${x:+.2f}" for x in pnl_data[:10]],
                    textposition='auto',
                ))
                
                fig.update_layout(
                    title="Unrealized P&L by Position (Top 10)",
                    xaxis_title="Position",
                    yaxis_title="P&L ($)",
                    showlegend=False
                )
                
                fig.update_xaxis(tickangle=45)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No P&L data to display.")
        
        # Portfolio metrics summary
        st.markdown("#### 📈 Portfolio Metrics")
        
        # Use pre-calculated metrics from shared method
        total_unrealized = portfolio_metrics.get('total_unrealized_pnl', 0)
        total_market_value = portfolio_metrics.get('total_market_value', 0)
        winning_positions = portfolio_metrics.get('winning_positions', 0)
        losing_positions = portfolio_metrics.get('losing_positions', 0)
        win_rate = portfolio_metrics.get('win_rate', 0)
        portfolio_return = portfolio_metrics.get('portfolio_return', 0)
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Total Unrealized P&L", f"${total_unrealized:+.2f}")
        
        with col2:
            st.metric("Win Rate", f"{win_rate:.1f}%")
        
        with col3:
            st.metric("Winners", winning_positions, delta=f"vs {losing_positions} losers")
        
        with col4:
            st.metric("Portfolio Return", f"{portfolio_return:+.2f}%")

def main():
    """Main function to run the dashboard."""
    dashboard = MarketDashboard()
    dashboard.run()

if __name__ == "__main__":
    main()
