# Setup Guide for Kalshi Market Making Bot

## Prerequisites

1. **Python 3.8+** installed on your system
2. **Kalshi Account** with API access
3. **API Credentials** from Kalshi

## Step 1: Get Kalshi API Credentials

1. Log in to your Kalshi account at [kalshi.com](https://kalshi.com)
2. Navigate to **Account Settings** → **Profile Settings**
3. Go to the **API Keys** section
4. Click **"Create New API Key"**
5. **IMPORTANT**: Save both the **Private Key** and **Key ID** immediately - they won't be shown again!

## Step 2: Set Up Environment Variables

Create a `.env` file in the project root with your credentials:

```env
# Kalshi API Configuration
KALSHI_API_KEY_ID=your-api-key-id-here
KALSHI_PRIVATE_KEY_PATH=path/to/private_key.pem

# Environment (demo or production)
KALSHI_DEMO_MODE=true
KALSHI_API_HOST=https://api.elections.kalshi.com/trade-api/v2

# Dashboard Configuration
DASHBOARD_PORT=8501
DASHBOARD_HOST=localhost
```

## Step 3: Save Your Private Key

1. Create a file called `private_key.pem` in your project directory
2. Copy the private key from Kalshi (starts with `-----BEGIN RSA PRIVATE KEY-----`)
3. Paste it into the file exactly as shown
4. Update the `KALSHI_PRIVATE_KEY_PATH` in your `.env` file to point to this file

Example:
```env
KALSHI_PRIVATE_KEY_PATH=./private_key.pem
```

## Step 4: Choose Environment

### Demo Environment (Recommended for Testing)
```env
KALSHI_DEMO_MODE=true
```
- Uses `https://demo-api.kalshi.co/trade-api/v2`
- Safe for testing with fake money
- Same API structure as production

### Production Environment
```env
KALSHI_DEMO_MODE=false
```
- Uses `https://api.elections.kalshi.com/trade-api/v2`
- Real money and real markets
- Use with caution!

## Step 5: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 6: Test Your Setup

```bash
python test_setup.py
```

This will:
- Test API connection
- Verify authentication
- Fetch sample markets
- Run screening test

## Step 7: Run the Bot

### Option 1: Use the startup script
```bash
./run_bot.sh
```

### Option 2: Run directly
```bash
# Run market screening bot
python main.py --mode bot

# Run dashboard
python main.py --mode dashboard
```

## Troubleshooting

### Common Issues

1. **"Private key file not found"**
   - Check the path in `KALSHI_PRIVATE_KEY_PATH`
   - Ensure the file exists and is readable

2. **"Authentication failed"**
   - Verify your API Key ID is correct
   - Check that the private key file contains the full key
   - Ensure no extra spaces or characters in the key

3. **"No markets found"**
   - Check if you're in demo mode vs production
   - Verify the API host URL is correct
   - Check your internet connection

4. **"Health check failed"**
   - Verify your API credentials
   - Check if Kalshi API is accessible
   - Try switching between demo and production

### Demo vs Production

- **Demo**: Safe testing environment with fake money
- **Production**: Real markets with real money - use carefully!

### API Rate Limits

Kalshi has rate limits on API requests. The bot is designed to respect these limits, but if you encounter rate limiting errors, you may need to reduce the frequency of requests.

## Security Notes

- Never commit your `.env` file or private key to version control
- Keep your private key secure and don't share it
- Use demo mode for testing and development
- Only use production mode when you're ready to trade with real money

## Getting Help

If you encounter issues:
1. Check the logs in `market_bot.log`
2. Run the test setup script to diagnose problems
3. Verify your API credentials are correct
4. Check Kalshi's API documentation for any changes
