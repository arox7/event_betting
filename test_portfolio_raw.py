"""
Simple test script to download and display unsettled and settled positions.
Also calculates PnL from fills and settlements data.
"""
import json
from collections import defaultdict
from config import Config
from kalshi_client import KalshiAPIClient
from pprint import pprint

def calculate_trading_pnl(fills_data):
    """Calculate PnL from fills (trading activity)."""
    if not fills_data or not fills_data.get('fills'):
        return {}
    
    fills = fills_data.get('fills', [])
    ticker_pnl = defaultdict(lambda: {'total_cost': 0, 'total_proceeds': 0, 'net_pnl': 0, 'trades': 0})
    
    for fill in fills:
        ticker = fill.ticker
        side = fill.side  # 'yes' or 'no'
        count = fill.count or 0
        price = fill.price or 0  # in cents
        
        if not ticker or count == 0:
            continue
        
        # Calculate trade value in cents
        trade_value = count * price
        
        if side == 'yes':
            # Bought Yes shares - this is a cost
            ticker_pnl[ticker]['total_cost'] += trade_value
            ticker_pnl[ticker]['net_pnl'] -= trade_value
        else:
            # Sold positions or bought No shares - treat as revenue
            ticker_pnl[ticker]['total_proceeds'] += trade_value
            ticker_pnl[ticker]['net_pnl'] += trade_value
        
        ticker_pnl[ticker]['trades'] += 1
    
    return dict(ticker_pnl)

def calculate_settlement_pnl(settlements_data):
    """Calculate PnL from settlements (final payouts)."""
    if not settlements_data or not settlements_data.get('settlements'):
        return {}
    
    settlements = settlements_data.get('settlements', [])
    ticker_pnl = {}
    
    for settlement in settlements:
        ticker = settlement.get('ticker')
        if not ticker:
            continue
        
        yes_count = settlement.get('yes_count', 0)
        no_count = settlement.get('no_count', 0)
        yes_total_cost = settlement.get('yes_total_cost', 0)
        no_total_cost = settlement.get('no_total_cost', 0)
        market_result = settlement.get('market_result')
        value = settlement.get('value', 0)  # payout per winning contract
        revenue = settlement.get('revenue', 0)  # actual payout received
        
        # Calculate net position
        net_yes = yes_count - no_count
        total_cost = yes_total_cost + no_total_cost
        
        # Calculate settlement PnL
        if market_result == "yes":
            settlement_revenue = net_yes * value if net_yes > 0 else 0
        elif market_result == "no":
            settlement_revenue = (-net_yes) * value if net_yes < 0 else 0
        else:
            settlement_revenue = 0
        
        # Total PnL = settlement revenue - total cost
        total_pnl = settlement_revenue - total_cost
        
        ticker_pnl[ticker] = {
            'yes_count': yes_count,
            'no_count': no_count,
            'net_position': net_yes,
            'yes_total_cost': yes_total_cost,
            'no_total_cost': no_total_cost,
            'total_cost': total_cost,
            'market_result': market_result,
            'value': value,
            'revenue': revenue,
            'settlement_revenue': settlement_revenue,
            'total_pnl': total_pnl
        }
    
    return ticker_pnl

def print_pnl_summary(trading_pnl, settlement_pnl):
    """Print comprehensive PnL summary."""
    print("\n" + "="*80)
    print(" PnL ANALYSIS")
    print("="*80)
    
    # Trading PnL
    print("\n📈 TRADING PnL (from fills):")
    print("-" * 50)
    if trading_pnl:
        total_trading_pnl = 0
        for ticker, data in trading_pnl.items():
            pnl_dollars = data['net_pnl'] / 100.0
            total_trading_pnl += pnl_dollars
            print(f"{ticker}:")
            print(f"  Trades: {data['trades']}")
            print(f"  Total Cost: ${data['total_cost'] / 100:.2f}")
            print(f"  Total Proceeds: ${data['total_proceeds'] / 100:.2f}")
            print(f"  Net PnL: ${pnl_dollars:+.2f}")
            print()
        print(f"Total Trading PnL: ${total_trading_pnl:+.2f}")
    else:
        print("No trading PnL data available.")
    
    # Settlement PnL
    print("\n💰 SETTLEMENT PnL (from settlements):")
    print("-" * 50)
    if settlement_pnl:
        total_settlement_pnl = 0
        for ticker, data in settlement_pnl.items():
            pnl_dollars = data['total_pnl'] / 100.0
            total_settlement_pnl += pnl_dollars
            print(f"{ticker}:")
            print(f"  Net Position: {data['net_position']} (Yes: {data['yes_count']}, No: {data['no_count']})")
            print(f"  Market Result: {data['market_result']}")
            print(f"  Total Cost: ${data['total_cost'] / 100:.2f}")
            print(f"  Settlement Revenue: ${data['settlement_revenue'] / 100:.2f}")
            print(f"  Actual Revenue: ${data['revenue'] / 100:.2f}")
            print(f"  Total PnL: ${pnl_dollars:+.2f}")
            print()
        print(f"Total Settlement PnL: ${total_settlement_pnl:+.2f}")
    else:
        print("No settlement PnL data available.")
    
    # Combined PnL
    print("\n🎯 COMBINED PnL:")
    print("-" * 50)
    total_trading = sum(data['net_pnl'] for data in trading_pnl.values()) / 100.0
    total_settlement = sum(data['total_pnl'] for data in settlement_pnl.values()) / 100.0
    total_combined = total_trading + total_settlement
    
    print(f"Trading PnL: ${total_trading:+.2f}")
    print(f"Settlement PnL: ${total_settlement:+.2f}")
    print(f"Combined Total: ${total_combined:+.2f}")

def print_positions(positions, title, limit=1000):
    """Print first N positions with key details."""
    print(f"\n{title}")
    print("=" * 60)
    
    if not positions:
        print("No positions found.")
        return
    
    print(f"Total positions: {len(positions)}")
    print(f"Showing first {min(limit, len(positions))} positions:\n")
    
    for i, pos in enumerate(positions[:limit]):
        print(f"Position {i+1}:")
        pprint(pos)
        print()

def main():
    """Download and display unsettled and settled positions, plus PnL analysis."""
    try:
        # Initialize client
        config = Config()
        kalshi_client = KalshiAPIClient(config)
        
        # Check API connection
        if not kalshi_client.health_check():
            print("❌ API connection failed. Check your credentials.")
            return
        
        print("✅ API connection successful")
        
        # Get unsettled positions
        print("\nFetching unsettled positions...")
        unsettled_data = kalshi_client.get_unsettled_positions()
        
        if unsettled_data:
            unsettled_positions = unsettled_data["all_market_positions"]
            print_positions(unsettled_positions, "UNSETTLED POSITIONS")
        else:
            print("❌ Failed to fetch unsettled positions")
        
        # # Get settled positions
        # print("\nFetching settled positions...")
        # settled_data = kalshi_client.get_settled_positions()
        
        # if settled_data:
        #     settled_positions = settled_data.get('active_positions', [])
        #     print_positions(settled_positions, "SETTLED POSITIONS")
        # else:
        #     print("❌ Failed to fetch settled positions")
        
        # # Get fills data for trading PnL calculation
        # print("\nFetching fills data for trading PnL...")
        # fills_data = kalshi_client.get_fills(limit=500)  # Get more fills for better PnL calculation
        
        # # Get settlements data for settlement PnL calculation
        # print("Fetching settlements data for settlement PnL...")
        # settlements_data = kalshi_client.get_settlements(limit=500)
        
        # # Calculate PnL
        # trading_pnl = calculate_trading_pnl(fills_data)
        # settlement_pnl = calculate_settlement_pnl(settlements_data)
        
        # # Print PnL summary
        # print_pnl_summary(trading_pnl, settlement_pnl)
        
        print("\n✅ Test completed successfully!")
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
