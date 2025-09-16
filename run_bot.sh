#!/bin/bash

# Kalshi Market Making Bot Startup Script

echo "🚀 Starting Kalshi Market Making Bot..."

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 is not installed or not in PATH"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

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
    echo ""
    echo "Choose an option:"
    echo "1. Run bot (market screening)"
    echo "2. Run dashboard (web interface)"
    echo "3. Exit"
    echo ""
    read -p "Enter your choice (1-3): " choice
    
    case $choice in
        1)
            echo "🤖 Starting market screening bot..."
            python main.py --mode bot
            ;;
        2)
            echo "📊 Starting dashboard..."
            python main.py --mode dashboard
            ;;
        3)
            echo "👋 Goodbye!"
            exit 0
            ;;
        *)
            echo "❌ Invalid choice"
            exit 1
            ;;
    esac
else
    echo "❌ Setup test failed. Please check your configuration."
    exit 1
fi
