"""
Market screening logic for identifying profitable trading opportunities.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime
import numpy as np

from models import Market, ScreeningCriteria, ScreeningResult, MarketStatus, Event
from kalshi_client import KalshiAPIClient
from config import Config

logger = logging.getLogger(__name__)

class MarketScreener:
    """Screens markets for profitable trading characteristics."""
    
    def __init__(self, kalshi_client: KalshiAPIClient, config: Config):
        """Initialize the market screener."""
        self.kalshi_client = kalshi_client
        self.config = config
        self.screening_criteria = self._create_default_criteria()
        
    def _create_default_criteria(self) -> ScreeningCriteria:
        """Create default screening criteria from config."""
        return ScreeningCriteria(
            min_volume=self.config.MIN_VOLUME,
            max_spread_percentage=self.config.MAX_SPREAD_PERCENTAGE,
            min_liquidity=self.config.MIN_LIQUIDITY,
            max_time_to_expiry_days=self.config.MAX_TIME_TO_EXPIRY_DAYS,
            min_open_interest=100,  # Minimum open interest
            categories=None  # No category filtering by default
        )
    
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
        
        total_markets = sum(len(event.markets) for event in events)
        profitable_count = len([r for r in all_results if r.is_profitable])
        
        logger.info(f"Screened {len(events)} events ({total_markets} total markets), found {profitable_count} profitable opportunities")
        return all_results
    
    def _screen_single_market(self, market: Market) -> ScreeningResult:
        """
        Screen a single market for profitable characteristics.
        
        Args:
            market: Market to screen
            
        Returns:
            Screening result
        """
        reasons = []
        score = 0.0
        
        # Check basic requirements
        if not self._check_basic_requirements(market, reasons):
            from datetime import timezone
            return ScreeningResult(
                market=market,
                score=0.0,
                reasons=reasons,
                is_profitable=False,
                timestamp=datetime.now(timezone.utc)
            )
        
        # Check spread requirement (the main profitability criterion)
        if market.spread_percentage is not None:
            if market.spread_percentage <= self.screening_criteria.max_spread_percentage:
                reasons.append(f"Profitable spread: {market.spread_percentage:.1%} <= {self.screening_criteria.max_spread_percentage:.1%}")
                score = 1.0  # Simple pass/fail scoring
                is_profitable = True
            else:
                reasons.append(f"Spread too high: {market.spread_percentage:.1%} > {self.screening_criteria.max_spread_percentage:.1%}")
                score = 0.0
                is_profitable = False
        else:
            reasons.append("No spread data available")
            score = 0.0
            is_profitable = False
        
        from datetime import timezone
        return ScreeningResult(
            market=market,
            score=score,
            reasons=reasons,
            is_profitable=is_profitable,
            timestamp=datetime.now(timezone.utc)
        )
    
    def _check_basic_requirements(self, market: Market, reasons: List[str]) -> bool:
        """Check if market meets basic requirements."""
        # Market must be open
        if market.status != MarketStatus.OPEN:
            reasons.append("Market is not open")
            return False
        
        # Must have valid pricing
        if market.yes_bid is None or market.yes_ask is None:
            reasons.append("No valid pricing available")
            return False
        
        # Must have minimum volume
        if market.volume < self.screening_criteria.min_volume:
            reasons.append(f"Volume too low: {market.volume} < {self.screening_criteria.min_volume}")
            return False
        
        # Must have minimum open interest
        if market.open_interest < self.screening_criteria.min_open_interest:
            reasons.append(f"Open interest too low: {market.open_interest} < {self.screening_criteria.min_open_interest}")
            return False
        
        # Must have minimum liquidity (volume + open interest)
        total_liquidity = market.volume + market.open_interest
        if total_liquidity < self.screening_criteria.min_liquidity:
            reasons.append(f"Liquidity too low: {total_liquidity} < {self.screening_criteria.min_liquidity}")
            return False
        
        # Must be within time limit
        if market.days_to_expiry > self.screening_criteria.max_time_to_expiry_days:
            reasons.append(f"Too far from expiry: {market.days_to_expiry} days")
            return False
        
        # Must be in allowed categories (if categories are specified)
        if self.screening_criteria.categories and market.category not in self.screening_criteria.categories:
            reasons.append(f"Category not allowed: {market.category}")
            return False
        
        return True
    
    
    def get_top_opportunities(self, results: List[ScreeningResult], limit: int = 10) -> List[ScreeningResult]:
        """
        Get top trading opportunities.
        
        Args:
            results: List of screening results
            limit: Maximum number of opportunities to return
            
        Returns:
            Top opportunities sorted by score
        """
        profitable_results = [r for r in results if r.is_profitable]
        return profitable_results[:limit]
    
    def update_criteria(self, new_criteria: ScreeningCriteria):
        """Update screening criteria."""
        self.screening_criteria = new_criteria
        logger.info("Updated screening criteria")
    
    def get_screening_summary(self, results: List[ScreeningResult]) -> Dict[str, Any]:
        """
        Get summary statistics of screening results.
        
        Args:
            results: List of screening results
            
        Returns:
            Summary statistics
        """
        total_markets = len(results)
        profitable_markets = len([r for r in results if r.is_profitable])
        
        if total_markets == 0:
            return {
                'total_markets': 0,
                'profitable_markets': 0,
                'profitability_rate': 0.0,
                'avg_score': 0.0,
                'top_score': 0.0
            }
        
        scores = [r.score for r in results]
        
        return {
            'total_markets': total_markets,
            'profitable_markets': profitable_markets,
            'profitability_rate': profitable_markets / total_markets,
            'avg_score': np.mean(scores),
            'top_score': max(scores),
            'min_score': min(scores)
        }
