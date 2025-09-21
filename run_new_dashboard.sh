#!/bin/bash

# Run the new simple dashboard
echo "🚀 Starting Simple Kalshi Dashboard..."
echo "📁 Dashboard files are in the dashboard/ directory"
echo "🔧 Main file: dashboard_new.py"
echo ""

# Set the Python path to include the current directory
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run the dashboard
streamlit run dashboard_new.py --server.port 8501 --server.address localhost
