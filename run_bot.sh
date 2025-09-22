#!/bin/bash

# Kalshi Market Making Bot - Quick Start Script
# This script provides easy commands to run the market making bot

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if environment is set up
check_environment() {
    print_status "Checking environment setup..."
    
    # Check if .env file exists
    if [ -f ".env" ]; then
        print_success ".env file found - environment variables will be loaded automatically"
    else
        print_warning ".env file not found - using system environment variables"
        print_status "Create a .env file with your Kalshi API credentials for easier configuration"
    fi
    
    if [ -z "$KALSHI_API_KEY_ID" ]; then
        print_error "KALSHI_API_KEY_ID environment variable is not set"
        print_status "Please set it in your .env file or with: export KALSHI_API_KEY_ID='your-api-key-id'"
        exit 1
    fi
    
    if [ -z "$KALSHI_PRIVATE_KEY_PATH" ]; then
        print_error "KALSHI_PRIVATE_KEY_PATH environment variable is not set"
        print_status "Please set it in your .env file or with: export KALSHI_PRIVATE_KEY_PATH='/path/to/private_key.pem'"
        exit 1
    fi
    
    if [ ! -f "$KALSHI_PRIVATE_KEY_PATH" ]; then
        print_error "Private key file not found: $KALSHI_PRIVATE_KEY_PATH"
        exit 1
    fi
    
    print_success "Environment variables are set correctly"
}

# Function to run tests
run_tests() {
    print_status "Running bot tests..."
    python test_market_making_bot.py
    if [ $? -eq 0 ]; then
        print_success "All tests passed!"
    else
        print_error "Some tests failed. Please fix the issues before running the bot."
        exit 1
    fi
}

# Function to show help
show_help() {
    echo "Kalshi Market Making Bot - Quick Start Script"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  test        Run bot tests to verify setup"
    echo "  dry-run     Run bot in dry-run mode (no actual orders)"
    echo "  conservative Run bot in conservative mode"
    echo "  moderate    Run bot in moderate mode (default)"
    echo "  aggressive  Run bot in aggressive mode"
    echo "  custom      Run bot with custom config file"
    echo "  status      Show bot status and configuration"
    echo "  help        Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 test                    # Run tests"
    echo "  $0 dry-run                 # Test without placing orders"
    echo "  $0 conservative            # Start with conservative settings"
    echo "  $0 custom my_config.yaml   # Use custom configuration"
    echo ""
    echo "Environment Variables:"
    echo "  The bot uses your existing .env file for configuration"
    echo "  KALSHI_API_KEY_ID          # Your Kalshi API key ID"
    echo "  KALSHI_PRIVATE_KEY_PATH    # Path to your private key file"
    echo "  KALSHI_DEMO_MODE           # Set to 'true' for demo mode"
    echo "  BOT_CONFIG_FILE            # Path to bot configuration file"
}

# Function to show status
show_status() {
    print_status "Bot Status and Configuration:"
    echo ""
    
    # Check .env file
    if [ -f ".env" ]; then
        echo "✅ .env file: Found"
    else
        echo "❌ .env file: Not found"
    fi
    
    # Check environment
    if [ -n "$KALSHI_API_KEY_ID" ]; then
        echo "✅ KALSHI_API_KEY_ID: Set"
    else
        echo "❌ KALSHI_API_KEY_ID: Not set"
    fi
    
    if [ -n "$KALSHI_PRIVATE_KEY_PATH" ]; then
        if [ -f "$KALSHI_PRIVATE_KEY_PATH" ]; then
            echo "✅ KALSHI_PRIVATE_KEY_PATH: $KALSHI_PRIVATE_KEY_PATH (exists)"
        else
            echo "❌ KALSHI_PRIVATE_KEY_PATH: $KALSHI_PRIVATE_KEY_PATH (not found)"
        fi
    else
        echo "❌ KALSHI_PRIVATE_KEY_PATH: Not set"
    fi
    
    if [ -n "$KALSHI_DEMO_MODE" ]; then
        echo "✅ KALSHI_DEMO_MODE: $KALSHI_DEMO_MODE"
    else
        echo "⚠️  KALSHI_DEMO_MODE: Not set (defaults to true)"
    fi
    
    if [ -n "$BOT_CONFIG_FILE" ]; then
        if [ -f "$BOT_CONFIG_FILE" ]; then
            echo "✅ BOT_CONFIG_FILE: $BOT_CONFIG_FILE (exists)"
        else
            echo "❌ BOT_CONFIG_FILE: $BOT_CONFIG_FILE (not found)"
        fi
    else
        echo "⚠️  BOT_CONFIG_FILE: Not set (will use defaults)"
    fi
    
    echo ""
    print_status "Available configuration files:"
    if [ -f "bot_config.yaml" ]; then
        echo "✅ bot_config.yaml"
    else
        echo "❌ bot_config.yaml (not found)"
    fi
    
    if [ -f "bot_config_example.yaml" ]; then
        echo "✅ bot_config_example.yaml (template)"
    else
        echo "❌ bot_config_example.yaml (not found)"
    fi
}

# Main script logic
case "${1:-help}" in
    "test")
        check_environment
        run_tests
        ;;
    "dry-run")
        check_environment
        print_status "Starting bot in dry-run mode..."
        python run_market_making_bot.py --dry-run
        ;;
    "conservative")
        check_environment
        print_status "Starting bot in conservative mode..."
        python run_market_making_bot.py --mode conservative
        ;;
    "moderate")
        check_environment
        print_status "Starting bot in moderate mode..."
        python run_market_making_bot.py --mode moderate
        ;;
    "aggressive")
        check_environment
        print_status "Starting bot in aggressive mode..."
        python run_market_making_bot.py --mode aggressive
        ;;
    "custom")
        if [ -z "$2" ]; then
            print_error "Please specify a config file: $0 custom my_config.yaml"
            exit 1
        fi
        check_environment
        print_status "Starting bot with custom config: $2"
        python run_market_making_bot.py --config "$2"
        ;;
    "status")
        show_status
        ;;
    "help"|"--help"|"-h")
        show_help
        ;;
    *)
        print_error "Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
