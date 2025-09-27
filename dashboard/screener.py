"""
Market Screener Page
"""
import streamlit as st
import pandas as pd
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from kalshi import KalshiAPIClient
from screening import MarketScreener, GeminiScreener
from kalshi.models import ScreeningResult, et_now
from config import Config
from constants import (
    DEFAULT_SCREENING_CRITERIA, 
    AI_QUICK_EXAMPLES, 
    FILTER_CONFIGS, 
    MESSAGES
)

class ScreenerPage:
    """Market screening page with AI and rule-based filters."""
    
    def __init__(self, kalshi_client: KalshiAPIClient, screener: MarketScreener, 
                 gemini_screener: GeminiScreener, config: Config):
        """Initialize the screener page."""
        self.kalshi_client = kalshi_client
        self.screener = screener
        self.gemini_screener = gemini_screener
        self.config = config
        
        # Initialize session state for screener
        if 'screening_mode' not in st.session_state:
            st.session_state.screening_mode = 'rule_based'
        if 'screening_criteria' not in st.session_state:
            st.session_state.screening_criteria = DEFAULT_SCREENING_CRITERIA.copy()
        if 'ai_query_used' not in st.session_state:
            st.session_state.ai_query_used = None
        if 'ai_screening_code' not in st.session_state:
            st.session_state.ai_screening_code = None
        if 'ai_criteria_explanation' not in st.session_state:
            st.session_state.ai_criteria_explanation = None
        if 'cached_events' not in st.session_state:
            st.session_state.cached_events = None
        if 'cached_events_timestamp' not in st.session_state:
            st.session_state.cached_events_timestamp = None
    
    def _is_cache_valid(self) -> bool:
        """Check if the cached events are still valid (within 1 minute TTL)."""
        if not st.session_state.cached_events or not st.session_state.cached_events_timestamp:
            return False
        
        cache_age = et_now() - st.session_state.cached_events_timestamp
        return cache_age.total_seconds() < 60  # 1 minute TTL
    
    def _get_cached_or_fresh_events(self, force_refresh: bool = False):
        """Get events from cache if valid, otherwise fetch fresh data."""
        if not force_refresh and self._is_cache_valid():
            return st.session_state.cached_events
        
        # Fetch fresh data
        events = self.kalshi_client.get_events(limit=200, status="open", max_events=self.config.MAX_EVENTS)
        
        # Update cache
        st.session_state.cached_events = events
        st.session_state.cached_events_timestamp = et_now()
        
        return events
    
    def _clear_cache(self):
        """Clear the cached events data."""
        st.session_state.cached_events = None
        st.session_state.cached_events_timestamp = None
    
    def render(self):
        """Render the screener page."""
        # Header with refresh button
        col1, col2 = st.columns([3, 1])
        with col1:
            st.header("🎯 Market Screener")
        with col2:
            if st.button("🔄 Hard Refresh", help="Force refresh market data (bypasses cache)"):
                self._clear_cache()
                st.rerun()
        
        # Show cache status
        if st.session_state.cached_events_timestamp:
            cache_age = et_now() - st.session_state.cached_events_timestamp
            if cache_age.total_seconds() < 60:
                st.caption(f"📊 Data cached {int(cache_age.total_seconds())}s ago")
            else:
                st.caption("📊 Cache expired - will refresh on next screening")
        else:
            st.caption("📊 No cached data - will fetch fresh data")
        
        # Auto-run initial screening if no results exist
        if not st.session_state.screening_results:
            self._run_initial_screening()
        
        # Side-by-side screening interface
        st.subheader("🔍 Screening Options")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 📊 Rule-Based Filters")
            self._render_rule_based_screening()
        
        with col2:
            st.markdown("#### 🤖 AI-Powered Search")
            self._render_ai_screening()
        
        # Results section
        self._render_results()
    
    def _run_initial_screening(self):
        """Run initial screening with default criteria on dashboard load."""
        try:
            with st.spinner(MESSAGES['loading_initial']):
                # Get events from cache or fetch fresh
                events = self._get_cached_or_fresh_events()
                
                if not events:
                    return
                
                # Screen events (which screens markets within them)
                results = self.screener.screen_events(events)
                
                # Store results
                st.session_state.screening_results = results
                st.session_state.last_update = et_now()
                
        except Exception as e:
            st.error(f"{MESSAGES['screening_error']}: {e}")
    
    def _render_rule_based_screening(self):
        """Render rule-based screening interface."""
        # Compact filter layout
        col1, col2 = st.columns(2)
        
        # Left column filters
        left_filters = ['min_volume_24h', 'min_liquidity_dollars', 'max_spread_percentage']
        # Right column filters
        right_filters = ['min_open_interest', 'max_spread_cents', 'max_time_to_close_days']
        
        filter_values = {}
        
        with col1:
            for filter_key in left_filters:
                config = FILTER_CONFIGS[filter_key]
                value = st.number_input(
                    config['label'],
                    min_value=config['min_value'],
                    max_value=config.get('max_value'),
                    value=st.session_state.screening_criteria[filter_key],
                    step=config['step'],
                    help=config['help']
                )
                filter_values[filter_key] = value
        
        with col2:
            for filter_key in right_filters:
                config = FILTER_CONFIGS[filter_key]
                value = st.number_input(
                    config['label'],
                    min_value=config['min_value'],
                    max_value=config.get('max_value'),
                    value=st.session_state.screening_criteria[filter_key],
                    step=config['step'],
                    help=config['help']
                )
                filter_values[filter_key] = value
        
        # Update session state
        st.session_state.screening_criteria.update(filter_values)
        
        # Run screening button
        if st.button("🔍 Apply Filters", width='stretch'):
            self._run_rule_based_screening()
    
    def _render_ai_screening(self):
        """Render AI-powered screening interface."""
        # Handle quick example selection first
        for i, example in enumerate(AI_QUICK_EXAMPLES):
            if f"ai_query_temp_{i}" in st.session_state:
                # Set the query in session state and clear the temp
                st.session_state.ai_query = st.session_state[f"ai_query_temp_{i}"]
                del st.session_state[f"ai_query_temp_{i}"]
                st.rerun()
        
        # AI query input with fixed key
        user_query = st.text_area(
            "Describe what markets you're looking for:",
            placeholder="e.g., find markets closing in the next 2 hours with high volume and tight spreads",
            height=80,
            key="ai_query"
        )
        
        # Quick examples
        col1, col2 = st.columns(2)
        for i, example in enumerate(AI_QUICK_EXAMPLES):
            with col1 if i % 2 == 0 else col2:
                if st.button(example['label'], width='stretch', key=f"ai_quick_{i}"):
                    # Store the query to be applied on next rerun
                    st.session_state[f"ai_query_temp_{i}"] = example['query']
                    st.rerun()
        
        # Run AI screening button
        if st.button("🤖 Run AI Search", width='stretch', disabled=not user_query.strip()):
            self._run_ai_screening(user_query)
    
    def _run_rule_based_screening(self):
        """Run rule-based screening."""
        try:
            with st.spinner(MESSAGES['loading_markets']):
                # Get events from cache or fetch fresh
                events = self._get_cached_or_fresh_events()
                
                if not events:
                    st.error(MESSAGES['no_markets'])
                    return
                
                # Update screening criteria
                criteria = self.screener.get_current_criteria()
                criteria.min_volume_24h = st.session_state.screening_criteria['min_volume_24h']
                criteria.min_liquidity_dollars = st.session_state.screening_criteria['min_liquidity_dollars']
                criteria.min_open_interest = st.session_state.screening_criteria['min_open_interest']
                # Convert percentage to decimal (UI shows 0-100%, model expects 0-1)
                criteria.max_spread_percentage = st.session_state.screening_criteria['max_spread_percentage'] / 100.0
                criteria.max_spread_cents = st.session_state.screening_criteria['max_spread_cents']
                criteria.max_time_to_close_days = st.session_state.screening_criteria['max_time_to_close_days']
                
                # Screen events (which screens markets within them)
                results = self.screener.screen_events(events)
                
                # Store results
                st.session_state.screening_results = results
                st.session_state.last_update = et_now()
                
                # Show summary
                stats = self.screener.get_market_statistics(events)
                passing_markets = len([r for r in results if r.score > 0])
                
                st.success(f"Found {passing_markets} markets passing criteria out of {stats['total_markets']} total markets")
                
        except Exception as e:
            st.error(f"{MESSAGES['screening_error']}: {e}")
    
    def _run_ai_screening(self, user_query: str):
        """Run AI-powered screening."""
        if not self.gemini_screener.is_available():
            st.error(MESSAGES['gemini_not_configured'])
            return
        
        try:
            with st.spinner(MESSAGES['generating_ai']):
                # Generate screening function from user query
                screening_code = self.gemini_screener.generate_screening_function(user_query)
                
                if not screening_code:
                    st.error(MESSAGES['ai_generation_failed'])
                    return
            
            # Generate explanation of criteria used
            with st.spinner(MESSAGES['analyzing_criteria']):
                criteria_explanation = self.gemini_screener.explain_screening_criteria(user_query, screening_code)
            
            # Get market data from cache or fetch fresh
            with st.spinner(MESSAGES['loading_markets']):
                events = self._get_cached_or_fresh_events()
                
                if not events:
                    st.error(MESSAGES['no_markets'])
                    return
                
                # Extract all markets from events
                all_markets = []
                for event in events:
                    all_markets.extend(event.markets)
            # Execute AI screening directly (removed threading for better performance)
            with st.spinner(MESSAGES['running_ai']):
                try:
                    results = self.gemini_screener.execute_screening_function(screening_code, all_markets, events)
                    st.write(f"✅ AI screening completed, found {len(results)} results")
                except Exception as e:
                    st.error(f"AI execution failed: {e}")
                    st.write("Generated code that failed:")
                    st.code(screening_code, language='python')
                    return
                
                # Store results and AI information
                st.session_state.screening_results = results
                st.session_state.ai_query_used = user_query
                st.session_state.ai_screening_code = screening_code
                st.session_state.ai_criteria_explanation = criteria_explanation
                st.session_state.last_update = et_now()
                
                # Show summary
                stats = self.screener.get_market_statistics(events)
                passing_markets = len([r for r in results if r.score > 0])
                
                st.success(f"AI found {passing_markets} markets matching your criteria out of {stats['total_markets']} total markets")
                
        except Exception as e:
            st.error(f"{MESSAGES['ai_screening_error']}: {e}")
    
    def _render_results(self):
        """Render screening results."""
        if not st.session_state.screening_results:
            return
        
        st.divider()
        st.subheader("📊 Screening Results")
        
        # Show AI criteria explanation if available
        if 'ai_criteria_explanation' in st.session_state and st.session_state.ai_criteria_explanation:
            with st.expander("🤖 AI Criteria Used", expanded=True):
                st.markdown(f"**Your Query:** \"{st.session_state.get('ai_query_used', 'Unknown')}\"")
                st.markdown("**AI Interpretation:**")
                st.info(st.session_state.ai_criteria_explanation)
        elif 'ai_query_used' in st.session_state and st.session_state.ai_query_used:
            # Show basic info if we have a query but no explanation
            with st.expander("🤖 AI Criteria Used", expanded=True):
                st.markdown(f"**Your Query:** \"{st.session_state.ai_query_used}\"")
                st.warning("AI criteria explanation not available")
        
        # Filter results
        results = [r for r in st.session_state.screening_results if r.score > 0]
        
        if not results:
            st.info("No markets passed the screening criteria")
            return
        
        # Results table
        self._render_results_table(results)
    
    def _render_results_table(self, results: List[ScreeningResult]):
        """Render results in a table format."""
        # Prepare data for table
        table_data = []
        for result in results:
            market = result.market
            
            # Calculate spread info
            bid = market.yes_bid / 100 if market.yes_bid else 0
            ask = market.yes_ask / 100 if market.yes_ask else 0
            spread = ask - bid if ask and bid else 0
            spread_pct = (spread / ask * 100) if ask > 0 else 0
            
            # Time to close
            if market.close_time:
                time_to_close = (market.close_time - et_now()).total_seconds() / 3600  # hours
                
                if time_to_close < 1:
                    # Less than 1 hour - show in minutes
                    minutes = int(time_to_close * 60)
                    time_display = f"{minutes}m"
                elif time_to_close < 24:
                    # Less than 24 hours - show hours
                    hours = int(time_to_close)
                    minutes = int((time_to_close - hours) * 60)
                    if minutes > 0:
                        time_display = f"{hours}h {minutes}m"
                    else:
                        time_display = f"{hours}h"
                else:
                    # 24+ hours - show days and hours
                    days = int(time_to_close / 24)
                    remaining_hours = int(time_to_close % 24)
                    if remaining_hours > 0:
                        time_display = f"{days}d {remaining_hours}h"
                    else:
                        time_display = f"{days}d"
            else:
                time_display = "Unknown"
            
            table_data.append({
                'Ticker': market.ticker,
                'Kalshi Link': f"https://kalshi.com/markets/{result.event.series_ticker}",
                'Event': f"{result.event.title} - {market.yes_sub_title}",
                'Bid/Ask': f"${bid:.2f}/${ask:.2f}",
                'Spread': f"${spread:.2f} ({spread_pct:.1f}%)",
                'Volume (24h)': market.volume_24h,
                'Open Interest': market.open_interest,
                'Close Time (days)': time_display,
                'Reason': '; '.join(result.reasons) if result.reasons else 'No reasons provided',
            })
        
        # Create DataFrame and display
        df = pd.DataFrame(table_data)
        
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
                    help="View market on Kalshi",
                    display_text="🔗 View"
                ),
                "Volume (24h)": st.column_config.NumberColumn(
                    "Volume (24h)",
                    help="Number of contracts traded in last 24 hours",
                    format="%d"
                ),
                "Open Interest": st.column_config.NumberColumn(
                    "Open Interest",
                    help="Number of outstanding contracts",
                    format="%d"
                ),
                "Close Time (days)": st.column_config.TextColumn(
                    "Close Time (days)",
                    help="Time until market closes (h = hours, d = days)"
                ),
                "Reason": st.column_config.TextColumn(
                    "Reason",
                    help="Explanation of why this market was selected"
                )
            }
        )
        
        st.caption(f"Showing {len(results)} results")
