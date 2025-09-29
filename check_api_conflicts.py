#!/usr/bin/env python3
"""
Check if other processes are using the same Kalshi API key.
"""
import subprocess
import re
import sys

def check_api_conflicts():
    """Check for processes that might be using the same API key."""
    print("Checking for potential API key conflicts...")
    
    # Get all Python processes
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        processes = result.stdout.split('\n')
    except Exception as e:
        print(f"Error getting process list: {e}")
        return False
    
    # Look for processes that might be using Kalshi API
    kalshi_processes = []
    for process in processes:
        if 'python' in process.lower() and any(keyword in process.lower() for keyword in ['kalshi', 'dashboard', 'streamlit', 'mm_ws_listener']):
            kalshi_processes.append(process.strip())
    
    if kalshi_processes:
        print("⚠️  Found processes that might be using the same API key:")
        for process in kalshi_processes:
            print(f"   {process}")
        print("\n💡 Solution: Stop these processes before running the bot")
        return False
    else:
        print("✅ No conflicting processes found")
        return True

if __name__ == "__main__":
    if check_api_conflicts():
        print("✅ Safe to run the bot")
        sys.exit(0)
    else:
        print("❌ Stop conflicting processes first")
        sys.exit(1)
