"""
Kalshi Metrics Calculator - Portfolio metrics and P&L calculation functions.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from .http_client import KalshiHTTPClient
from .portfolio_functions import get_balance_dollars, get_all_positions, get_recent_pnl, filter_market_positions_by_date
from .data_enricher import get_enriched_positions

logger = logging.getLogger(__name__)

def calculate_portfolio_metrics(client: KalshiHTTPClient) -> Optional[Dict[str, Any]]:
    """Get comprehensive portfolio metrics including cash, positions, and P&L."""
    try:
        # Get cash balance (in dollars)
        cash_balance_dollars = get_balance_dollars(client)
        if cash_balance_dollars is None:
            return None
        
        # Get all positions data (includes both active and market positions)
        all_positions_data = get_all_positions(client)
        if all_positions_data is None:
            return None
        
        # Get enriched positions for detailed calculations (active positions only)
        enriched_positions = get_enriched_positions(client, include_closed=False)
        if enriched_positions is None:
            enriched_positions = []
        
        # Extract market positions (all positions including closed ones)
        all_market_positions = all_positions_data['all_market_positions']
        
        # Calculate metrics from enriched positions (active positions only)
        total_active_positions = len(enriched_positions)
        
        # Market value calculations (convert from cents to dollars)
        total_market_value_dollars = sum(abs(pos['market_value']) for pos in enriched_positions) / 100.0
        total_unrealized_pnl_dollars = sum(pos['unrealized_pnl'] for pos in enriched_positions) / 100.0
        
        # Calculate realized P&L from all market positions, accounting for fees
        total_realized_pnl_cents = 0
        total_fees_paid_cents = 0
        
        for pos in all_market_positions:
            realized_pnl_cents = pos['realized_pnl']  # Already in cents
            fees_paid_cents = pos['fees_paid']  # Already in cents
            total_realized_pnl_cents += realized_pnl_cents
            total_fees_paid_cents += fees_paid_cents
        
        # Net realized P&L after fees (convert to dollars)
        total_realized_pnl_dollars = (total_realized_pnl_cents - total_fees_paid_cents) / 100.0
        total_fees_paid_dollars = total_fees_paid_cents / 100.0
        
        # Calculate portfolio totals
        total_portfolio_value_dollars = cash_balance_dollars + total_market_value_dollars
        
        # Calculate win/loss metrics from active positions
        winning_positions = len([pos for pos in enriched_positions if pos['unrealized_pnl'] > 0])
        losing_positions = len([pos for pos in enriched_positions if pos['unrealized_pnl'] < 0])
        win_rate = (winning_positions / total_active_positions) * 100 if total_active_positions > 0 else 0
        portfolio_return = (total_unrealized_pnl_dollars / total_market_value_dollars) * 100 if total_market_value_dollars > 0 else 0
        
        # Calculate closed positions from all data (client-side filtering will handle date ranges)
        closed_positions = [pos for pos in all_market_positions if pos['position'] == 0 and pos['total_traded'] > 0]
        
        # Don't enrich closed positions by default - only when specifically requested for display
        # This keeps the portfolio metrics calculation fast
        enriched_closed_positions = []
        
        return {
            'cash_balance': cash_balance_dollars,  # In dollars
            'total_market_value': total_market_value_dollars,  # In dollars
            'total_portfolio_value': total_portfolio_value_dollars,  # In dollars
            'total_unrealized_pnl': total_unrealized_pnl_dollars,  # In dollars
            'total_realized_pnl': total_realized_pnl_dollars,  # In dollars (after fees)
            'total_fees_paid': total_fees_paid_dollars,  # In dollars
            'total_positions': total_active_positions,
            'winning_positions': winning_positions,
            'losing_positions': losing_positions,
            'win_rate': win_rate,
            'portfolio_return': portfolio_return,
            'enriched_positions': enriched_positions,  # Active positions only
            'enriched_closed_positions': enriched_closed_positions,  # Closed positions with market/event data
            'market_positions': all_market_positions,  # All market positions (unfiltered)
            'closed_positions': closed_positions,  # All closed positions (raw data)
            'total_closed_positions': len(closed_positions)
        }
        
    except Exception as e:
        logger.error(f"Failed to get portfolio metrics: {e}")
        return None

def calculate_filtered_portfolio_metrics(client: KalshiHTTPClient, start_date=None, end_date=None) -> Optional[Dict[str, Any]]:
    """Calculate portfolio metrics filtered by date range."""
    try:
        # Get base portfolio metrics
        base_metrics = calculate_portfolio_metrics(client)
        if not base_metrics:
            return None
        
        # Get all market positions for filtering
        all_market_positions = base_metrics['market_positions']
        
        # Apply date filtering using the existing client method
        filtered_market_positions = filter_market_positions_by_date(
            all_market_positions, start_date, end_date
        )
        
        # Calculate ALL metrics from filtered data (not just realized P&L)
        total_realized_pnl_cents = 0
        total_fees_paid_cents = 0
        
        # Calculate win/loss metrics from filtered positions
        winning_positions = 0
        losing_positions = 0
        total_unrealized_pnl_cents = 0
        
        # Filter enriched positions by date as well (for win rate calculations)
        enriched_positions = base_metrics['enriched_positions']
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
        cash_balance = base_metrics['cash_balance']
        total_market_value_dollars = sum(abs(pos.get('market_value', 0)) for pos in filtered_enriched_positions) / 100.0
        total_portfolio_value_dollars = cash_balance + total_market_value_dollars
        
        # Calculate portfolio return
        portfolio_return = (total_unrealized_pnl_dollars / total_market_value_dollars) * 100 if total_market_value_dollars > 0 else 0
        
        # Update base metrics with filtered data
        base_metrics.update({
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
        
        return base_metrics
        
    except Exception as e:
        logger.error(f"Failed to calculate filtered portfolio metrics: {e}")
        return None
