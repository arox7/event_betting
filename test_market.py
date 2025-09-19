#!/usr/bin/env python3
"""
Test script to fetch a single market from Kalshi API and dump raw JSON.

Usage:
    python test_market.py TICKER

Example:
    python test_market.py KXHALFTRIL-26
    python test_market.py PRES24DEM
"""

import sys
import json
from kalshi_client import KalshiAPIClient
from config import Config

def main():
    if len(sys.argv) != 2:
        print("Usage: python test_market.py TICKER")
        print("Example: python test_market.py KXHALFTRIL-26")
        sys.exit(1)
    
    ticker = sys.argv[1]
    
    try:
        # Initialize client
        config = Config()
        client = KalshiAPIClient(config)
        
        print(f"Fetching market: {ticker}")
        print("=" * 60)
        
        # Get market data
        market = client.get_market_by_ticker(ticker)
        
        if market:
            # Dump raw JSON in pretty format
            print(json.dumps(market.model_dump(), indent=2, default=str))
        else:
            print(f"❌ Market not found: {ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching market: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
