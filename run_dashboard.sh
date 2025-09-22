#!/bin/bash

# Run the Kalshi Dashboard
echo "🚀 Starting Kalshi Dashboard..."
echo "📁 Dashboard files are in the dashboard/ directory"
echo "🔧 Main file: dashboard/dashboard.py"
echo ""

# Set the Python path to include the current directory
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run the dashboard
streamlit run dashboard/dashboard.py --server.port 8501 --server.address localhost