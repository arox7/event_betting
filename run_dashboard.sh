#!/bin/bash

# Kalshi Market Analysis Dashboard Startup Script

echo "🚀 Starting Kalshi Market Analysis Dashboard..."

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Please create one with your Kalshi API credentials."
    echo "   You can copy .env.example and fill in your details."
    exit 1
fi

# Test setup
echo "🧪 Testing setup..."
python test_setup.py

if [ $? -eq 0 ]; then
    echo "✅ Setup test passed!"
    echo "📊 Starting dashboard..."
    python main.py
else
    echo "❌ Setup test failed. Please check your configuration."
    exit 1
fi
