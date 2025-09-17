"""
Market screening logic for identifying profitable trading opportunities.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import numpy as np

from models import Market, ScreeningCriteria, ScreeningResult, Event, utc_now
from kalshi_client import KalshiAPIClient
from config import Config

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
            min_liquidity=self.config.MIN_LIQUIDITY,
            max_time_to_expiry_days=self.config.MAX_TIME_TO_EXPIRY_DAYS,
            min_open_interest=self.config.MIN_OPEN_INTEREST,
            categories=None  # No category filtering by default
        )
    
    def update_criteria(self, **kwargs) -> None:
        """
        Update screening criteria with new values.
        
        Args:
            **kwargs: Criteria to update (e.g., min_volume=2000, max_spread_percentage=0.15)
        """
        for key, value in kwargs.items():
            if hasattr(self.screening_criteria, key):
                setattr(self.screening_criteria, key, value)
            else:
                logger.warning(f"Unknown criteria: {key}")
    
    def get_current_criteria(self) -> ScreeningCriteria:
        """Get current screening criteria."""
        return self.screening_criteria
    
    def screen_markets(self, markets: List[Market]) -> List[ScreeningResult]:
        """
        Screen markets for profitable characteristics.
        
        Args:
            markets: List of markets to screen
            
        Returns:
            List of screening results
        """
        results = []
        
        for market in markets:
            try:
                result = self._screen_single_market(market)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to screen market {market.ticker}: {e}")
                continue
        
        # Sort by score (highest first)
        results.sort(key=lambda x: x.score, reverse=True)
        return results
    
    def screen_events(self, events: List[Event]) -> List[ScreeningResult]:
        """
        Screen events for profitable market opportunities.
        
        Args:
            events: List of events to screen
            
        Returns:
            List of screening results with event context
        """
        all_results = []
        
        for event in events:
            # Screen all markets in this event
            market_results = self.screen_markets(event.markets)
            
            # Add event context to each result
            for result in market_results:
                result.event = event
                all_results.append(result)
        
        # Sort by score (highest first)
        all_results.sort(key=lambda x: x.score, reverse=True)
        
        # Sort by score (highest first)
        all_results.sort(key=lambda x: x.score, reverse=True)
        return all_results
    
    def _screen_single_market(self, market: Market) -> ScreeningResult:
        """
        Screen a single market against the screening criteria.
        
        Args:
            market: Market to screen
            
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
                if hasattr(market, 'spread_percentage'):
                    spread_pct = market.spread_percentage
                    if spread_pct is not None:
                        if spread_pct <= self.screening_criteria.max_spread_percentage:
                            reasons.append(f"Spread percentage within range: {spread_pct:.1%} <= {self.screening_criteria.max_spread_percentage:.1%}")
                        else:
                            reasons.append(f"Spread percentage too high: {spread_pct:.1%} > {self.screening_criteria.max_spread_percentage:.1%}")
                            passes_filters = False
                    else:
                        reasons.append("Spread percentage calculated as None")
                        passes_filters = False
                else:
                    logger.warning(f"Market {market.ticker} missing spread_percentage property. Available attributes: {[attr for attr in dir(market) if not attr.startswith('_')]}")
                    reasons.append("Market object missing spread_percentage property")
                    passes_filters = False
            except Exception as e:
                logger.error(f"Error calculating spread percentage for market {market.ticker}: {e}")
                reasons.append(f"Error calculating spread percentage: {e}")
                passes_filters = False
        
        # Check spread in cents (if criteria is set)
        if (self.screening_criteria.min_spread_cents is not None or 
            self.screening_criteria.max_spread_cents is not None):
            try:
                if hasattr(market, 'spread_cents'):
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
                        reasons.append("Spread cents calculated as None")
                        passes_filters = False
                else:
                    logger.warning(f"Market {market.ticker} missing spread_cents property")
                    reasons.append("Market object missing spread_cents property")
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
            score=1.0 if passes_filters else 0.0,
            reasons=reasons,
            timestamp=utc_now()
        )
    
    def _check_basic_requirements(self, market: Market, reasons: List[str]) -> bool:
        """Check if market meets basic requirements."""
        # Market must be active (open)
        if market.status not in ["active"]:
            reasons.append(f"Market is not active (status: {market.status})")
            return False
        
        # Must have minimum volume (check both total volume and 24h volume)
        if self.screening_criteria.min_volume is not None:
            if market.volume < self.screening_criteria.min_volume:
                reasons.append(f"Total volume too low: {market.volume} < {self.screening_criteria.min_volume}")
                return False
        
        if self.screening_criteria.min_volume_24h is not None:
            if market.volume_24h < self.screening_criteria.min_volume_24h:
                reasons.append(f"24h volume too low: {market.volume_24h} < {self.screening_criteria.min_volume_24h}")
                return False
        
        # Must have minimum open interest
        if self.screening_criteria.min_open_interest is not None:
            if market.open_interest < self.screening_criteria.min_open_interest:
                reasons.append(f"Open interest too low: {market.open_interest} < {self.screening_criteria.min_open_interest}")
                return False
        
        # Must have minimum liquidity (volume + open interest)
        if self.screening_criteria.min_liquidity is not None:
            if market.liquidity_dollars < self.screening_criteria.min_liquidity:
                reasons.append(f"Liquidity too low: {market.liquidity_dollars} < {self.screening_criteria.min_liquidity}")
                return False
        
        # Must be within time limit
        if (self.screening_criteria.max_time_to_expiry_days is not None and 
            market.days_to_expiry > self.screening_criteria.max_time_to_expiry_days):
            reasons.append(f"Too far from expiry: {market.days_to_expiry} days")
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
            self.screening_criteria.min_liquidity is None,
            self.screening_criteria.max_time_to_expiry_days is None,
            self.screening_criteria.min_open_interest is None,
            self.screening_criteria.categories is None
        ])
    
    def get_passing_markets(self, results: List[ScreeningResult], limit: Optional[int] = None) -> List[ScreeningResult]:
        """
        Get markets that pass the screening criteria.
        
        Args:
            results: List of screening results
            limit: Maximum number of markets to return (None for all)
            
        Returns:
            Markets that pass the screening criteria (score > 0)
        """
        passing_results = [r for r in results if r.score > 0]
        if limit is None:
            return passing_results
        return passing_results[:limit]
    
    def update_criteria(self, new_criteria: ScreeningCriteria):
        """Update screening criteria."""
        self.screening_criteria = new_criteria
    
    def get_screening_summary(self, results: List[ScreeningResult]) -> Dict[str, Any]:
        """
        Get summary statistics of screening results.
        
        Args:
            results: List of screening results
            
        Returns:
            Summary statistics
        """
        total_markets = len(results)
        passing_markets = len([r for r in results if r.score > 0])
        
        if total_markets == 0:
            return {
                'total_markets': 0,
                'passing_markets': 0,
                'profitability_rate': 0.0,
                'avg_score': 0.0,
                'top_score': 0.0
            }
        
        scores = [r.score for r in results]
        
        return {
            'total_markets': total_markets,
            'passing_markets': passing_markets,
            'pass_rate': passing_markets / total_markets,
            'avg_score': np.mean(scores),
            'top_score': max(scores),
            'min_score': min(scores)
        }
