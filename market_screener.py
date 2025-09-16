"""
Market screening logic for identifying profitable trading opportunities.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime
import numpy as np

from models import Market, ScreeningCriteria, ScreeningResult, MarketCategory, MarketStatus
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
            categories=[MarketCategory.POLITICS, MarketCategory.ECONOMICS]  # Focus on these categories
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
        
        logger.info(f"Screened {len(markets)} markets, found {len([r for r in results if r.is_profitable])} profitable opportunities")
        return results
    
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
            return ScreeningResult(
                market=market,
                score=0.0,
                reasons=reasons,
                is_profitable=False,
                timestamp=datetime.now()
            )
        
        # Calculate profitability score
        score += self._calculate_volume_score(market)
        score += self._calculate_spread_score(market, reasons)
        score += self._calculate_liquidity_score(market)
        score += self._calculate_time_score(market)
        score += self._calculate_category_score(market)
        score += self._calculate_volatility_score(market, reasons)
        
        # Determine if profitable
        is_profitable = score >= 0.6 and len([r for r in reasons if "profitable" in r.lower()]) > 0
        
        return ScreeningResult(
            market=market,
            score=score,
            reasons=reasons,
            is_profitable=is_profitable,
            timestamp=datetime.now()
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
        
        # Must be within time limit
        if market.days_to_expiry > self.screening_criteria.max_time_to_expiry_days:
            reasons.append(f"Too far from expiry: {market.days_to_expiry} days")
            return False
        
        # Must be in allowed categories
        if market.category not in self.screening_criteria.categories:
            reasons.append(f"Category not allowed: {market.category.value}")
            return False
        
        return True
    
    def _calculate_volume_score(self, market: Market) -> float:
        """Calculate score based on volume."""
        # Higher volume = higher score (capped at 0.2)
        volume_score = min(market.volume / 10000, 0.2)
        return volume_score
    
    def _calculate_spread_score(self, market: Market, reasons: List[str]) -> float:
        """Calculate score based on spread."""
        if market.spread_percentage is None:
            return 0.0
        
        # Lower spread = higher score
        if market.spread_percentage <= 0.02:  # 2% or less
            reasons.append("Tight spread - profitable for market making")
            return 0.3
        elif market.spread_percentage <= 0.05:  # 5% or less
            reasons.append("Reasonable spread")
            return 0.15
        else:
            reasons.append("Wide spread - less profitable")
            return 0.0
    
    def _calculate_liquidity_score(self, market: Market) -> float:
        """Calculate score based on liquidity."""
        # Estimate liquidity from bid/ask sizes (simplified)
        liquidity = market.volume + market.open_interest
        if liquidity >= 5000:
            return 0.2
        elif liquidity >= 2000:
            return 0.1
        else:
            return 0.0
    
    def _calculate_time_score(self, market: Market) -> float:
        """Calculate score based on time to expiry."""
        days = market.days_to_expiry
        
        # Sweet spot is 7-14 days
        if 7 <= days <= 14:
            return 0.2
        elif 3 <= days <= 21:
            return 0.1
        else:
            return 0.0
    
    def _calculate_category_score(self, market: Market) -> float:
        """Calculate score based on market category."""
        # Politics and economics tend to be more liquid
        if market.category in [MarketCategory.POLITICS, MarketCategory.ECONOMICS]:
            return 0.1
        else:
            return 0.05
    
    def _calculate_volatility_score(self, market: Market, reasons: List[str]) -> float:
        """Calculate score based on price volatility."""
        if market.mid_price is None:
            return 0.0
        
        # Markets closer to 50/50 tend to be more volatile and profitable
        mid_price = market.mid_price
        if 0.3 <= mid_price <= 0.7:
            reasons.append("Good volatility range for market making")
            return 0.1
        elif 0.2 <= mid_price <= 0.8:
            return 0.05
        else:
            return 0.0
    
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
