"""
Market screening logic for identifying profitable trading opportunities.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from kalshi.models import Market, Event, utc_now, ScreeningCriteria, ScreeningResult
from kalshi import KalshiAPIClient
from config import Config, setup_logging

# Configure logging with centralized setup
setup_logging(level=logging.INFO, include_filename=True)
logger = logging.getLogger(__name__)

class MarketScreener:
    """Screens markets for profitable trading characteristics."""
    
    def __init__(self, kalshi_client: KalshiAPIClient, config: Config, custom_criteria: Optional[ScreeningCriteria] = None):
        """Initialize the market screener."""
        self.kalshi_client = kalshi_client
        self.config = config
        self.screening_criteria = custom_criteria or self._create_default_criteria()
        
    def _create_default_criteria(self) -> ScreeningCriteria:
        """Create default screening criteria from config."""
        return ScreeningCriteria(
            min_volume=self.config.MIN_VOLUME,
            min_volume_24h=self.config.MIN_VOLUME_24H,
            max_spread_percentage=self.config.MAX_SPREAD_PERCENTAGE,
            max_spread_cents=self.config.MAX_SPREAD_CENTS,
            min_spread_cents=self.config.MIN_SPREAD_CENTS,
            min_liquidity_dollars=self.config.MIN_LIQUIDITY,
            max_time_to_close_days=self.config.MAX_TIME_TO_CLOSE_DAYS,
            min_open_interest=self.config.MIN_OPEN_INTEREST,
            categories=None  # No category filtering by default
        )
    
    def get_current_criteria(self) -> ScreeningCriteria:
        """Get current screening criteria."""
        return self.screening_criteria
    
    def screen_events(self, events: List[Event]) -> List[ScreeningResult]:
        """
        Screen events by screening all markets within each event.
        
        This is the primary screening method that should be used for event-based screening.
        It screens each market within each event and returns results with proper event context.
        
        Args:
            events: List of events to screen
            
        Returns:
            List of screening results with event context, sorted by score (highest first)
        """
        all_results = []
        
        for event in events:
            # Screen all markets within this event
            event_results = self._screen_markets_in_event(event)
            all_results.extend(event_results)
        
        # Sort by score (highest first)
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results
    
    def _screen_markets_in_event(self, event: Event) -> List[ScreeningResult]:
        """
        Screen all markets within a single event.
        
        Args:
            event: Event containing markets to screen
            
        Returns:
            List of screening results for markets in this event
        """
        results = []
        
        for market in event.markets:
            try:
                result = self._screen_single_market(market, event)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to screen market {market.ticker} in event {event.event_ticker}: {e}")
                continue
        
        return results
    
    def get_market_statistics(self, events: List[Event]) -> Dict[str, int]:
        """
        Get statistics about markets in events.
        
        Args:
            events: List of events
            
        Returns:
            Dictionary with market statistics
        """
        total_markets = sum(len(event.markets) for event in events)
        active_markets = sum(
            len([m for m in event.markets if m.status == 'active']) 
            for event in events
        )
        
        return {
            'total_markets': total_markets,
            'active_markets': active_markets,
            'total_events': len(events)
        }
    
    def _screen_single_market(self, market: Market, event: Event = None) -> ScreeningResult:
        """
        Screen a single market against the screening criteria.
        
        Args:
            market: Market to screen
            event: Associated event (optional)
            
        Returns:
            Screening result with pass/fail flag
        """
        reasons = []
        passes_filters = True
        
        # Check basic requirements
        if not self._check_basic_requirements(market, reasons):
            passes_filters = False
        
        # Check percentage spread (if criteria is set)
        if self.screening_criteria.max_spread_percentage is not None:
            try:
                spread_pct = market.spread_percentage
                if spread_pct is not None:
                    if spread_pct <= self.screening_criteria.max_spread_percentage:
                        reasons.append(f"Spread percentage within range: {spread_pct:.1%} <= {self.screening_criteria.max_spread_percentage:.1%}")
                    else:
                        reasons.append(f"Spread percentage too high: {spread_pct:.1%} > {self.screening_criteria.max_spread_percentage:.1%}")
                        passes_filters = False
                else:
                    # Try to calculate spread percentage manually if the property returns None
                    if market.yes_bid is not None and market.yes_ask is not None:
                        # Calculate mid price and spread percentage using mid price
                        mid_price_cents = (market.yes_bid + market.yes_ask) / 2
                        if mid_price_cents > 0:
                            manual_spread_pct = (market.yes_ask - market.yes_bid) / mid_price_cents
                            if manual_spread_pct <= self.screening_criteria.max_spread_percentage:
                                reasons.append(f"Manual spread percentage within range: {manual_spread_pct:.1%} <= {self.screening_criteria.max_spread_percentage:.1%}")
                            else:
                                reasons.append(f"Manual spread percentage too high: {manual_spread_pct:.1%} > {self.screening_criteria.max_spread_percentage:.1%}")
                                passes_filters = False
                        else:
                            reasons.append("Cannot calculate spread percentage - mid price is zero")
                    else:
                        reasons.append("Cannot calculate spread percentage - missing bid/ask data")
                        passes_filters = False
            except Exception as e:
                logger.error(f"Error calculating spread percentage for market {market.ticker}: {e}")
                reasons.append(f"Error calculating spread percentage: {e}")
                passes_filters = False
        
        # Check spread in cents (if criteria is set)
        if (self.screening_criteria.min_spread_cents is not None or 
            self.screening_criteria.max_spread_cents is not None):
            try:
                spread_cents = market.spread_cents
                if spread_cents is not None:
                    min_cents = self.screening_criteria.min_spread_cents or 0
                    max_cents = self.screening_criteria.max_spread_cents or float('inf')
                    
                    if min_cents <= spread_cents <= max_cents:
                        reasons.append(f"Spread cents within range: {spread_cents} cents (min: {min_cents}, max: {max_cents})")
                    else:
                        reasons.append(f"Spread cents outside range: {spread_cents} cents (min: {min_cents}, max: {max_cents})")
                        passes_filters = False
                else:
                    # Try to calculate spread cents manually if the property returns None
                    if market.yes_bid is not None and market.yes_ask is not None:
                        manual_spread_cents = market.yes_ask - market.yes_bid
                        min_cents = self.screening_criteria.min_spread_cents or 0
                        max_cents = self.screening_criteria.max_spread_cents or float('inf')
                        
                        if min_cents <= manual_spread_cents <= max_cents:
                            reasons.append(f"Manual spread cents within range: {manual_spread_cents} cents (min: {min_cents}, max: {max_cents})")
                        else:
                            reasons.append(f"Manual spread cents outside range: {manual_spread_cents} cents (min: {min_cents}, max: {max_cents})")
                            passes_filters = False
                    else:
                        reasons.append("Cannot calculate spread cents - missing bid/ask data")
                        passes_filters = False
            except Exception as e:
                logger.error(f"Error calculating spread cents for market {market.ticker}: {e}")
                reasons.append(f"Error calculating spread cents: {e}")
                passes_filters = False
        
        # If no criteria are set, market passes by default
        if self._no_criteria_set():
            reasons.append("No screening criteria set - market passes by default")
            passes_filters = True
        
        return ScreeningResult(
            market=market,
            event=event,
            score=1.0 if passes_filters else 0.0,
            reasons=reasons,
            timestamp=utc_now()
        )
    
    def _check_basic_requirements(self, market: Market, reasons: List[str]) -> bool:
        """Check if market meets basic requirements."""
        # Market must be active (open)
        if market.status not in ["active", "open"]:
            reasons.append(f"Market is not active (status: {market.status})")
            return False
        
        # Must have minimum volume (check both total volume and 24h volume)
        if self.screening_criteria.min_volume is not None:
            if market.volume is None or market.volume < self.screening_criteria.min_volume:
                reasons.append(f"Total volume too low: {market.volume or 0} < {self.screening_criteria.min_volume}")
                return False
        
        if self.screening_criteria.min_volume_24h is not None:
            if market.volume_24h is None or market.volume_24h < self.screening_criteria.min_volume_24h:
                reasons.append(f"24h volume too low: {market.volume_24h or 0} < {self.screening_criteria.min_volume_24h}")
                return False
        
        # Must have minimum open interest
        if self.screening_criteria.min_open_interest is not None:
            if market.open_interest is None or market.open_interest < self.screening_criteria.min_open_interest:
                reasons.append(f"Open interest too low: {market.open_interest or 0} < {self.screening_criteria.min_open_interest}")
                return False
        
        # Must have minimum liquidity (check liquidity_dollars field or use volume as proxy)
        if self.screening_criteria.min_liquidity_dollars is not None:
            if market.liquidity_dollars is not None:
                # Use direct liquidity field if available
                if market.liquidity_dollars < self.screening_criteria.min_liquidity_dollars:
                    reasons.append(f"Liquidity too low: ${market.liquidity_dollars:.2f} < ${self.screening_criteria.min_liquidity_dollars:.2f}")
                    return False
            elif market.volume is not None:
                # Fallback to volume as liquidity proxy (convert volume to dollars)
                volume_dollars = market.volume / 100.0  # Assuming volume is in cents
                if volume_dollars < self.screening_criteria.min_liquidity_dollars:
                    reasons.append(f"Volume-based liquidity too low: ${volume_dollars:.2f} < ${self.screening_criteria.min_liquidity_dollars:.2f}")
                    return False
            else:
                reasons.append("No liquidity data available")
                return False
        
        # Must be within time limit
        if (self.screening_criteria.max_time_to_close_days is not None and 
            market.days_to_close is not None and 
            market.days_to_close > self.screening_criteria.max_time_to_close_days):
            reasons.append(f"Too far from close: {market.days_to_close} days")
            return False
        
        return True
    
    def _no_criteria_set(self) -> bool:
        """Check if any screening criteria are set."""
        return all([
            self.screening_criteria.min_volume is None,
            self.screening_criteria.min_volume_24h is None,
            self.screening_criteria.max_spread_percentage is None,
            self.screening_criteria.max_spread_cents is None,
            self.screening_criteria.min_spread_cents is None,
            self.screening_criteria.min_liquidity_dollars is None,
            self.screening_criteria.max_time_to_close_days is None,
            self.screening_criteria.min_open_interest is None,
            self.screening_criteria.categories is None
        ])
