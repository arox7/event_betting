#!/bin/bash

# Cron Job Setup Script
# This script helps set up cron jobs for the ETL pipeline

echo "Setting up ETL cron jobs with safety checks..."
echo ""
echo "The ETL job includes safety checks to prevent duplicate data and ensure data integrity:"
echo "- Only runs between 2 AM and 6 AM Eastern Time"
echo "- Checks for existing data to prevent duplicates"
echo "- Verifies data continuity from previous days"
echo "- Validates data quality (minimum trade counts)"
echo ""

# Get the current directory
PROJECT_DIR="/Users/nikhilmalkani/Documents/event_betting"
ETL_SCRIPT="$PROJECT_DIR/scripts/run_etl.sh"
EVENTS_ETL_SCRIPT="$PROJECT_DIR/scripts/run_events_etl.py"

# Create log directory
mkdir -p "$PROJECT_DIR/logs"

# Add cron jobs (uncomment the ones you want to use)

# Run ETL job every 15 minutes
# echo "*/15 * * * * $ETL_SCRIPT >> $PROJECT_DIR/logs/etl.log 2>&1" | crontab -

# Run ETL job every hour
# echo "0 * * * * $ETL_SCRIPT >> $PROJECT_DIR/logs/etl.log 2>&1" | crontab -

# Run ETL job every 6 hours
# echo "0 */6 * * * $ETL_SCRIPT >> $PROJECT_DIR/logs/etl.log 2>&1" | crontab -

# Run ETL job daily at 2 AM ET (7 AM UTC) - RECOMMENDED
echo "0 7 * * * $ETL_SCRIPT >> $PROJECT_DIR/logs/etl.log 2>&1" | crontab -

# Run Events ETL job daily at 2:10 AM ET (7:10 AM UTC) - 10 minutes after trades ETL
echo "10 7 * * * cd $PROJECT_DIR && source ~/.bash_profile && conda activate event_betting && python $EVENTS_ETL_SCRIPT >> $PROJECT_DIR/logs/events_etl.log 2>&1" | crontab -

echo "Cron job setup complete!"
echo ""
echo "The following ETL jobs will run daily at 2 AM ET (7 AM UTC):"
echo "1. Trades ETL (7:00 AM UTC) - includes safety checks"
echo "2. Events ETL (7:10 AM UTC) - refreshes all events data"
echo ""
echo "If safety checks fail, the job will exit with code 0 (success) so cron won't send error emails."
echo ""
echo "To view current cron jobs:"
echo "  crontab -l"
echo ""
echo "To remove all cron jobs:"
echo "  crontab -r"
echo ""
echo "To view ETL logs:"
echo "  tail -f $PROJECT_DIR/logs/etl.log"
echo "  tail -f $PROJECT_DIR/logs/events_etl.log"
