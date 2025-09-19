#!/usr/bin/env python3
"""
Test script to fetch a single event from Kalshi API and dump raw JSON.

Usage:
    python test_event.py EVENT_TICKER

Example:
    python test_event.py PRES24
    python test_event.py SENATE24
"""

import sys
import json
from kalshi_client import KalshiAPIClient
from config import Config

def main():
    if len(sys.argv) != 2:
        print("Usage: python test_event.py EVENT_TICKER")
        print("Example: python test_event.py PRES24")
        sys.exit(1)
    
    event_ticker = sys.argv[1]
    
    try:
        # Initialize client
        config = Config()
        client = KalshiAPIClient(config)
        
        print(f"Fetching event: {event_ticker}")
        print("=" * 60)
        
        # Get event data (search through all events for matching ticker)
        events = client.get_events(limit=100, status="open")
        event = None
        for e in events:
            if e.event_ticker == event_ticker:
                event = e
                break
        
        if event:
            # Dump raw JSON in pretty format
            print(json.dumps(event.model_dump(), indent=2, default=str))
        else:
            print(f"❌ Event not found: {event_ticker}")
            
    except Exception as e:
        print(f"❌ Error fetching event: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
