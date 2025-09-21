"""
Simple test script to print raw data structures from Kalshi API.
Focuses on showing the actual JSON responses without analysis.
"""
import json
import logging
from config import Config, setup_logging
from kalshi_client import KalshiAPIClient

# Configure logging with centralized setup
setup_logging(level=logging.WARNING, include_filename=True)
logger = logging.getLogger(__name__)

def convert_pydantic_to_dict(obj):
    """Convert Pydantic model to dictionary."""
    if hasattr(obj, 'model_dump'):
        # Pydantic v2
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        # Pydantic v1
        return obj.dict()
    elif hasattr(obj, '__dict__'):
        # Regular object
        return obj.__dict__
    else:
        return {"error": "Could not convert to dict", "type": str(type(obj))}

def test_raw_data():
    """Print raw data from all portfolio methods."""
    print("🔍 KALSHI API RAW DATA TEST")
    print("=" * 50)
    
    try:
        config = Config()
        kalshi_client = KalshiAPIClient(config)
        
        # Test API connection
        if not kalshi_client.health_check():
            print("❌ API connection failed")
            return
        
        print("✅ API connected successfully")
        
        # 1. Balance
        print("\n1. BALANCE:")
        print("-" * 20)
        balance = kalshi_client.get_balance()
        print(f"Balance: {balance}")
        
        # 2. All Positions
        print("\n2. ALL POSITIONS:")
        print("-" * 20)
        positions_data = kalshi_client.get_unsettled_positions()
        if positions_data:
            print(f"Total market positions: {len(positions_data.get('market_positions', []))}")
            print(f"Active positions: {len(positions_data.get('positions', []))}")
            
            # Show first position structure
            positions = positions_data.get('positions', [])
            if positions:
                print("\nFirst position structure:")
                print(json.dumps(positions[0], indent=2, default=str))
        else:
            print("No positions data")
        
        # 3. Portfolio Summary
        print("\n3. PORTFOLIO SUMMARY:")
        print("-" * 20)
        portfolio_summary = kalshi_client.get_portfolio_summary()
        print(json.dumps(portfolio_summary, indent=2, default=str))
        
        # 4. Portfolio Metrics
        print("\n4. PORTFOLIO METRICS:")
        print("-" * 20)
        portfolio_metrics = kalshi_client.get_portfolio_metrics()
        print(json.dumps(portfolio_metrics, indent=2, default=str))
        
        # 5. Realized P&L
        print("\n5. REALIZED P&L (7 days):")
        print("-" * 20)
        realized_pnl = kalshi_client.get_realized_pnl(days=7)
        print(json.dumps(realized_pnl, indent=2, default=str))
        
        # 6. Recent P&L
        print("\n6. RECENT P&L (24 hours):")
        print("-" * 20)
        recent_pnl = kalshi_client.get_recent_pnl(hours=24)
        print(json.dumps(recent_pnl, indent=2, default=str))
        
        # 7. Fills
        print("\n7. FILLS (last 5):")
        print("-" * 20)
        fills_data = kalshi_client.get_fills(limit=5)
        if fills_data and fills_data.get('fills'):
            fills = fills_data.get('fills', [])
            print(f"Total fills: {len(fills)}")
            if fills:
                print("\nFirst fill structure:")
                first_fill = fills[0]
                fill_dict = convert_pydantic_to_dict(first_fill)
                print(json.dumps(fill_dict, indent=2, default=str))
        else:
            print("No fills data")
        
        # 8. Enriched Positions
        print("\n8. ENRICHED POSITIONS:")
        print("-" * 20)
        enriched_positions = kalshi_client.get_enriched_positions()
        if enriched_positions:
            print(f"Total enriched positions: {len(enriched_positions)}")
            if enriched_positions:
                # Show structure without the market/event objects
                sample = enriched_positions[0].copy()
                if 'market' in sample:
                    sample['market'] = f"<Market object: {type(sample['market'])}>"
                if 'event' in sample:
                    sample['event'] = f"<Event object: {type(sample['event'])}>"
                print("\nFirst enriched position structure:")
                print(json.dumps(sample, indent=2, default=str))
        else:
            print("No enriched positions")
        
        print("\n✅ Raw data test completed!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_raw_data()
