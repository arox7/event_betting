#!/bin/bash

# ETL Job Runner Script
# This script runs the trades ETL job and can be scheduled with cron

# Set up environment
cd /Users/nikhilmalkani/Documents/event_betting
source ~/.bash_profile
conda activate event_betting

# Create data directory if it doesn't exist
mkdir -p data

# Run ETL job
echo "Starting ETL job at $(date)"
python etl/trades_etl.py

# Check if job was successful
if [ $? -eq 0 ]; then
    echo "ETL job completed successfully at $(date)"
else
    echo "ETL job failed at $(date)"
    exit 1
fi
