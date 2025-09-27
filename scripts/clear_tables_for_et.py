#!/usr/bin/env python3
"""
Simple script to clear all tables and start fresh with ET timestamps.
Use this if you want to start over without re-processing historical data.
"""

import os
import sys
import shutil
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import TradesDatabase

def backup_and_clear():
    """Backup database and clear all tables."""
    db_path = "data/trades.db"
    backup_path = f"data/trades_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    
    # Backup existing database
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
        print(f"✅ Database backed up to: {backup_path}")
    else:
        print("⚠️  No existing database found")
    
    # Clear all tables
    db = TradesDatabase()
    
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        
        # Clear all tables
        tables = ['trades', 'daily_aggregations', 'hourly_aggregations', 'daily_category_aggregations']
        
        for table in tables:
            cursor.execute(f"DELETE FROM {table}")
            print(f"✅ Cleared {table} table")
        
        conn.commit()
        print("✅ All tables cleared")
        print("🔄 Ready for fresh ETL runs with ET timestamps")

if __name__ == "__main__":
    backup_and_clear()
