# Acadia Market Making Bot - Technical Documentation

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Trading Algorithm](#trading-algorithm)
4. [Core Components](#core-components)
5. [Data Flow](#data-flow)
6. [Configuration](#configuration)
7. [Missing Features & Known Issues](#missing-features--known-issues)
8. [Potential Bugs & Edge Cases](#potential-bugs--edge-cases)
9. [Deployment](#deployment)

---

## Overview

**Acadia** is a market-making bot for Kalshi prediction markets. It's designed to provide liquidity by placing buy orders at the best bid and selling at the best ask to capture the spread as profit.

### Purpose
- Provide liquidity to Kalshi markets
- Capture bid-ask spreads through market making
- Manage inventory risk through position limits
- Automatically exit losing positions

### Current Status
✅ **FUNCTIONAL** - The bot is now operational with real order placement and cancellation. Tested in demo environment. Key features implemented:
- Real order placement via Kalshi API
- Real-time orderbook tracking
- Fill-triggered order regeneration
- Position limit enforcement
- Dry-run mode for testing
- Async order execution (non-blocking)

---

## Architecture

### High-Level Flow
```
┌─────────────────┐
│   main.py       │  Orchestrates everything
│ MarketMakingBot │
└────────┬────────┘
         │
         ├──► Loads config.yaml (market configs)
         ├──► Initializes KalshiAPIClient
         ├──► Initializes KalshiWebSocketClient
         ├──► Creates AcadiaStrategy
         └──► Creates MarketListener per market
                  │
                  ▼
         ┌────────────────────┐
         │  WebSocket Events  │
         └────────┬───────────┘
                  │
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼
OrderBook      Trades        Fills
   │              │             │
   └──────────────┴─────────────┘
                  │
                  ▼
         ┌────────────────┐
         │ MarketListener │
         └────────┬───────┘
                  │
                  ├──► Updates OrderBookTracker
                  ├──► Updates MarketState in Strategy
                  └──► Calls strategy.generate_orders()
                           │
                           ▼
                  ┌─────────────────┐
                  │ AcadiaStrategy  │
                  └────────┬────────┘
                           │
                           ├──► Calculates position return
                           ├──► Generates OrderIntents
                           └──► OrderManager.sync_target_orders()
                                    │
                                    ▼
                           ┌──────────────────┐
                           │  Order Actions   │
                           │ (add/cancel/keep)│
                           └────────┬─────────┘
                                    │
                                    ▼
                           ⚠️ TODO: Real execution
                           (Currently fake order IDs)
```

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| **MarketMakingBot** | Main orchestrator; manages WebSocket connections, subscriptions, message dispatch |
| **AcadiaStrategy** | Trading logic; generates order intents based on market state and positions |
| **MarketListener** | Per-market event processor; updates orderbook, handles fills/positions |
| **OrderManager** | Order lifecycle tracking; determines add/cancel/keep actions |
| **OrderBookTracker** | Maintains live orderbook state from WebSocket snapshots/deltas |
| **KalshiAPIClient** | REST API wrapper for positions, orders, market data |
| **KalshiWebSocketClient** | WebSocket client for real-time market data and fills |

---

## Trading Algorithm

### Strategy: "Acadia" - Simple Market Making

The Acadia strategy is a **stateless** market maker that:
1. Opens positions by buying at the best bid
2. Closes positions by selling at the best ask
3. Uses position limits to control risk
4. Exits aggressively if a position is down >25%

### Entry Logic (Position Below Limit)

When `abs(position) < position_limit`, the strategy places **opening orders** based on the configured `side`:

#### Side: "yes"
- Buy YES at best YES bid
- Size: Up to `position_limit - current_position` (remaining capacity)

#### Side: "no"
- Buy NO at best NO bid (equivalent to shorting YES)
- Size: Up to `position_limit + current_position` (remaining capacity)

#### Side: "both"
- Buy YES at best YES bid AND
- Buy NO at best NO bid
- Each side sized to remaining capacity in that direction

**Key Insight**: The bot only buys at the bid to open positions. It's designed to collect the spread by waiting for the market to come to it.

**Important**: Opening orders are placed **as long as position is below the limit**, not just when position is zero. This means the bot can scale into positions gradually.

### Exit Logic (Any Non-Zero Position)

When `position > 0` (any non-zero position), the strategy places **closing orders**:

#### Long YES Position (`position > 0`)
- Sell YES at best YES ask (normal exit)
- OR sell YES at best YES bid (aggressive exit if down >25%)

#### Long NO Position (`position < 0`)
- Sell NO at best NO ask (normal exit)
- OR sell NO at best NO bid (aggressive exit if down >25%)

**Aggressive Exit Trigger**: If unrealized P&L is down more than 25%, the bot exits immediately at the worse price (bid instead of ask) to cut losses.

### Position Tracking

Position state includes:
- `position`: Net position (positive = long YES, negative = long NO)
- `avg_entry_price_yes`: Average entry price for YES contracts
- `avg_entry_price_no`: Average entry price for NO contracts
- `unrealized_pnl_cents`: Current unrealized P&L in cents
- `position_return_pct`: Position return as percentage

**Entry Price Calculation**:
- Weighted average on new buys: `(old_qty * old_avg + new_qty * new_price) / total_qty`
- FIFO assumption on sells: Entry price remains constant until position closes

### Order Management Philosophy

The strategy uses a **target state** approach:
1. Generate desired order state (what orders *should* exist)
2. Compare with current orders
3. Determine actions: add, cancel, or keep orders
4. Execute actions

This approach ensures idempotency and makes the strategy easier to reason about.

---

## Core Components

### 1. `main.py` - MarketMakingBot

**Responsibilities**:
- Load market configurations from YAML
- Initialize API and WebSocket clients
- Fetch initial positions and outstanding orders
- Subscribe to WebSocket channels (orderbook, trades, fills, positions)
- Dispatch messages to appropriate MarketListener
- Auto-reconnect on WebSocket failures

**Key Methods**:
- `initialize_state()`: Fetch positions and orders from API, initialize strategy
- `run()`: Main event loop with auto-reconnect
- `_dispatch()`: Route messages to correct MarketListener based on ticker
- `_subscribe_to_markets()`: Subscribe to all necessary channels

**WebSocket Channels**:
- `orderbook_delta`: Live orderbook updates
- `trade`: Public trades
- `ticker`: Market ticker data
- `fill`: Private fill notifications
- `market_positions`: Position updates

### 2. `acadia.py` - AcadiaStrategy

**Responsibilities**:
- Maintain market state and position state for all markets
- Generate order intents based on current state
- Calculate position returns and unrealized P&L
- Determine when to enter/exit positions
- Implement aggressive exit logic

**Key Methods**:
- `initialize()`: Set up initial position and market states
- `generate_orders()`: Main strategy logic; returns order actions
- `_generate_opening_orders()`: Create orders to open positions (when below limit)
- `_generate_closing_orders()`: Create orders to close positions (when position > 0)
- `_calculate_position_return()`: Calculate unrealized P&L

**Strategy Behavior** (Important!):
The strategy can place **both** opening and closing orders simultaneously:
- If `abs(position) < position_limit`: Generate opening orders (buy at bid)
- If `abs(position) > 0`: Generate closing orders (sell at ask)

This means:
- With a small position (e.g., 3/10), bot places BOTH buy orders (to scale up) AND sell orders (to exit)
- Only when position == 0: Only opening orders
- Only when position == limit: Only closing orders

**State Management**:
- `market_states`: Dict[ticker, MarketState] - current orderbook state
- `position_states`: Dict[ticker, PositionState] - current positions
- `order_manager`: OrderManager instance

### 3. `acadia_listener.py` - MarketListener

**Responsibilities**:
- Process WebSocket messages for a single market
- Maintain orderbook state via OrderBookTracker
- Update strategy's MarketState with best bid/ask
- Handle fill notifications and update entry prices
- Execute order actions (TODO: real execution)

**Key Methods**:
- `process_message()`: Main message handler
- `_on_orderbook()`: Update orderbook, trigger strategy, execute actions
- `_on_fill()`: Update position and entry prices, regenerate orders
- `_on_market_position()`: Update position from API, regenerate orders if changed
- `_execute_order_actions()`: **Async** - Execute add/cancel/keep actions via Kalshi API
- `_update_entry_price()`: Calculate new entry prices on fills

**Important**: All order execution methods are async to prevent blocking the event loop during API calls and order throttling.

### 4. `order_manager.py` - OrderManager

**Responsibilities**:
- Track active orders in memory
- Detect stale orders (age-based or price-based)
- Sync target order state with current state
- Manage order lifecycle (add, fill, remove)

**Key Methods**:
- `add_order()`: Track a newly placed order
- `remove_order()`: Remove an order from tracking
- `update_order_fill()`: Update order size after partial fill
- `get_orders_to_cancel()`: Find stale orders
- `sync_target_orders()`: Compare target vs current, return actions

**Staleness Detection**:
- **Age-based**: Orders older than 60 seconds
- **Price-based**: Orders more than 5 cents away from current market

### 5. `orderbook_tracker.py` - OrderBookTracker

**Responsibilities**:
- Maintain live orderbook state from WebSocket
- Process snapshots and deltas
- Provide best bid/ask queries
- Calculate spreads

**Key Concepts**:
- YES and NO books are complementary: `YES price + NO price = 100 cents`
- Ask is derived from opposite side's bid
- Snapshot resets entire book; deltas are incremental

**Key Methods**:
- `apply_snapshot()`: Replace book with fresh snapshot
- `apply_delta()`: Apply incremental update
- `best_bid()`: Get best bid for YES or NO
- `best_ask()`: Get best ask (derived from opposite bid)
- `spread()`: Calculate bid-ask spread

### 6. `market_types.py` - Data Models

**Core Types**:
- `MarketConfig`: Configuration for a market (ticker, side, position_limit)
- `MarketState`: Current orderbook state (yes_bid, yes_ask, no_bid, no_ask)
- `PositionState`: Current position and entry prices
- `OrderIntent`: Desired order (ticker, side, price, size, action)
- `ActiveOrder`: Tracked order with metadata
- `OrderManagerState`: Container for all active orders

---

## Data Flow

### Startup Sequence
1. Load `config.yaml` (market configs)
2. Initialize API client (REST)
3. Initialize WebSocket client
4. Fetch initial positions via REST API
5. Fetch outstanding orders via REST API
6. Initialize strategy with current state
7. Connect WebSocket
8. Subscribe to channels (orderbook, trades, fills, positions)
9. Enter event loop

### Orderbook Update Flow
1. WebSocket receives orderbook delta
2. MarketListener receives message
3. OrderBookTracker applies delta
4. MarketListener updates strategy's MarketState
5. Strategy generates order intents
6. OrderManager syncs target orders with current orders
7. Actions returned: add, cancel, keep
8. MarketListener executes actions (STUBBED)

### Fill Flow
1. WebSocket receives fill notification
2. MarketListener receives fill message
3. Position updated: `position += count` (buy) or `position -= count` (sell)
4. Entry price recalculated using weighted average
5. Strategy recalculates position return on next orderbook update
6. Strategy may generate exit orders if position is losing

### Position Update Flow
1. WebSocket receives position update
2. MarketListener updates PositionState
3. Position logged for monitoring

---

## Configuration

### `config.yaml`

```yaml
# Global settings
dry_run: true  # If true, log order actions without actually placing/cancelling orders

# Market configurations
markets:
  - ticker: "KXNBAGAME-25OCT29LALMIN-MIN"
    side: "No"
    position_limit: 5
    min_spread_cents: 1        # Only quote when spread >= 2 cents
    min_price_delta_cents: 3   # Only requote if price moves >= 3 cents
    min_quote_time_seconds: 60.0  # Orders go stale after 60 seconds
    exit_edge_threshold_cents: 3  # Undercut exit price if profit >= 3¢
```

**Global Fields**:
- `dry_run`: Boolean - if true, simulates orders without real API calls

**Market Fields**:
- `ticker`: Market ticker symbol
- `side`: "Yes", "No", or "Both" (which side to trade)
- `position_limit`: Maximum position size in contracts
- `min_spread_cents`: Minimum spread width to quote
- `min_price_delta_cents`: Minimum price move to trigger requote
- `min_quote_time_seconds`: Maximum age before order is stale
- `exit_edge_threshold_cents`: Minimum profit to undercut on exits

### Environment Variables (`.env`)

Required:
- `KALSHI_API_KEY_ID`: API key ID from Kalshi
- `KALSHI_PRIVATE_KEY_PATH`: Path to private key PEM file
- `KALSHI_DEMO_MODE`: `true` for demo, `false` for production

Optional:
- `KALSHI_API_HOST`: Override API host
- `KALSHI_DEMO_HOST`: Override demo host

---

## Recent Bug Fixes (2025-10-30)

### ✅ Critical Bug: Blocking Sleep Causing Stale Orderbook

**Issue**: Bot was using `time.sleep()` in HTTP client throttling, which blocked the entire async event loop.
- During the ~0.6s sleep, WebSocket messages queued up but weren't processed
- Orderbook became stale, showing prices like 81¢ when actual was 80¢
- Led to constant cancel/replace loops as new data arrived

**Fix**: Converted entire order flow to async
- Changed `time.sleep()` → `await asyncio.sleep()`
- Made `create_order()` and `cancel_order()` async throughout the stack
- Made `_execute_order_actions()` async in `acadia_listener.py`

**Files Modified**:
- `kalshi/http_client.py`
- `kalshi/portfolio_functions.py`
- `kalshi/client.py`
- `market_making/acadia_listener.py`

**Impact**: Orderbook now stays current during order throttling. Cancel/replace loops eliminated.

---

### ✅ Bug: Order Placement 400 Error (EOF)

**Issue**: Order placement was failing with `400 - {"code":"bad_request","message":"bad request","details":"EOF"}`

**Root Cause**: `create_order()` was sending payload as `params` (query string) instead of `json_data` (request body) for POST requests.

**Fix**: Changed `client.make_authenticated_request("POST", "/portfolio/orders", params=payload)` to use `json_data=payload`

**Impact**: Orders now place successfully.

---

### ✅ Bug: Position Limit Exceeded

**Issue**: Bot was exceeding position limits due to race condition during async order cancellation.

**Root Cause**: Outstanding orders could fill before cancellation completed, causing effective position to exceed limit.

**Fix**: Re-introduced defensive check in `_generate_opening_orders()` to calculate `effective_long_position` including outstanding buy orders:
```python
effective_long_position = current_position + outstanding_buy_yes - outstanding_buy_no
remaining_long_capacity = max(0, position_limit - effective_long_position)
```

**Impact**: Position limits now properly enforced even during rapid order fills.

---

### ✅ Bug: Post-Only Cross Errors

**Issue**: Orders were being rejected with `"post only cross"` error.

**Root Cause**: All orders had `post_only=True`, but aggressive exit orders need to cross the spread.

**Fix**: Made `post_only` parameter dynamic based on `intent.intent_type`:
- `post_only=True` for market_making and normal exit orders
- `post_only=False` for aggressive_exit orders

**Impact**: Aggressive exits can now cross the spread to exit quickly.

---

### ✅ Bug: Datetime Timezone Issues

**Issue**: `TypeError: can't subtract offset-naive and offset-aware datetimes`

**Fix**: Changed all `datetime.now()` calls to `datetime.now(timezone.utc)` in `market_types.py`

**Impact**: No more timezone-related crashes.

---

## Implemented Features (2025-10-30)

### ✅ Real Order Execution

**Status**: **IMPLEMENTED**

Order placement and cancellation now fully functional via Kalshi API:
- POST `/portfolio/orders` for order creation
- DELETE `/portfolio/orders/{id}` for cancellation
- Real order IDs tracked in OrderManager
- Error handling for API failures

### ✅ Dry-Run Mode

**Status**: **IMPLEMENTED**

Configurable via `config.yaml`:
```yaml
dry_run: true  # Simulate orders without real API calls
```

When enabled:
- Logs order actions with 🧪 emoji
- Tracks simulated orders with fake IDs
- No real API calls made
- Perfect for testing strategy logic

### ✅ Fill-Triggered Order Regeneration

**Status**: **IMPLEMENTED**

Bot now immediately regenerates orders after:
1. **Fills**: When an order fills, bot immediately places exit orders if needed
2. **Position updates**: When WebSocket reports position change, bot adjusts quotes

**Impact**: Faster reaction time, better exit order placement

### ✅ Post-Only Order Control

**Status**: **IMPLEMENTED**

Orders now use `post_only` parameter:
- **Market-making orders**: `post_only=True` (rejected if would cross spread)
- **Exit orders**: `post_only=True` (wait for spread to come to us)
- **Aggressive exits**: `post_only=False` (cross spread immediately)

---

## Remaining Missing Features

### Missing: Order Status Tracking

Currently, orders are tracked in memory but there's no synchronization with the exchange. If the bot restarts, it loses track of outstanding orders until it fetches them from the API again.

**What's Needed**:
- Periodic reconciliation of OrderManager state with API
- Handle orders that fill between orderbook updates
- Handle partially filled orders

### Missing: Position Reconciliation

Position updates come via WebSocket, but there's no periodic verification against the REST API. This could lead to drift if WebSocket messages are missed.

**What's Needed**:
- Periodic position refresh from API (every 60 seconds?)
- Reconcile position state with API response
- Log discrepancies

### Missing: Risk Management

**No protection against**:
- Runaway losses (only 25% stop loss)
- Rapid price movements
- Orderbook manipulation
- Excessive trading costs

**Potential Additions**:
- Maximum daily loss limit
- Maximum number of trades per market
- Minimum profit threshold before closing
- Dynamic position sizing based on volatility

### Missing: Performance Metrics

**No tracking of**:
- Total P&L (realized + unrealized)
- Win rate
- Average profit per trade
- Sharpe ratio
- Max drawdown

**What's Needed**:
- Metrics collection and logging
- Dashboard or reporting
- Performance alerts

### Missing: Logging & Monitoring

**Current Logging**: Basic INFO-level logs to console

**What's Missing**:
- Structured logging (JSON format)
- Log aggregation
- Performance monitoring
- Alert system for errors/unusual conditions
- Trade history persistence

---

## Potential Bugs & Edge Cases

### 1. ~~Variable Overwrite Bug~~ (FIXED 2025-10-30)

**Issue**: When position was non-zero but below limit, opening orders were generated then immediately overwritten by closing orders.

**Fixed**: Changed from variable assignment to list concatenation using `extend()`.

### 2. Order Sync Race Conditions

**Issue**: If two orderbook updates arrive quickly, the bot might try to place duplicate orders.

**Example**:
1. Orderbook update 1 triggers order placement
2. Before order 1 is placed, orderbook update 2 arrives
3. Order 2 is also triggered (duplicate)

**Mitigation**: OrderManager should de-duplicate based on order signature (side, action, price, size).

**Current Status**: `sync_target_orders()` uses signatures, so this should be handled. But with fake order IDs, it's untested.

### 2. Fill Before Order Tracking

**Issue**: Order might fill before `add_order()` is called.

**Example**:
1. Order placed on exchange
2. Fill notification arrives via WebSocket
3. `add_order()` hasn't been called yet

**Result**: Fill is processed but order isn't tracked.

**Mitigation**: Place order, get order ID, add to tracking, THEN process any queued fill messages.

### 3. Position Calculation Errors

**Issue**: Entry price calculation assumes FIFO but doesn't track individual lots.

**Example**:
1. Buy 10 YES @ 50¢
2. Buy 10 YES @ 60¢ → avg = 55¢
3. Sell 5 YES @ 70¢ → avg remains 55¢ (correct if FIFO)
4. But if you sold the 60¢ lot first, true avg is 50¢

**Current Behavior**: Assumes FIFO, which may not match exchange's actual accounting.

**Mitigation**: Track individual lots or accept the approximation.

### 4. Orderbook Snapshot Loss

**Issue**: If a snapshot is missed or invalid, the orderbook becomes unusable.

**Current Handling**: `apply_delta()` throws `OrderBookError` if no snapshot exists.

**What's Missing**: Automatic snapshot re-request on error.

### 5. WebSocket Disconnection During Order Placement

**Issue**: If WebSocket disconnects while orders are being placed, state can desync.

**Example**:
1. Order placed successfully
2. WebSocket disconnects before fill notification
3. Bot reconnects, doesn't know order filled
4. May try to place duplicate order

**Mitigation**: On reconnect, fetch all outstanding orders and positions from API to resync.

**Current Status**: Bot does fetch on startup but not on reconnect.

### 6. Zero-Sized Orders

**Issue**: If `position_limit` is reached, `remaining_capacity` becomes 0, but code still tries to place 0-sized order.

**Current Handling**: Orders with size 0 are generated but likely rejected by exchange.

**Mitigation**: Add check: `if order_size > 0:` before appending order intent.

### 7. Aggressive Exit Oscillation

**Issue**: If position is down 25%, bot sells at bid. But if bid is also down 25%, it might trigger multiple exits.

**Example**:
1. Long YES @ 50¢, now bid is 35¢ → down 30%
2. Aggressive exit: sell at bid 35¢
3. Order placed but not filled immediately
4. Next orderbook update: still down 30%, tries to exit again (duplicate)

**Current Handling**: OrderManager should de-duplicate, but with fake order IDs, untested.

### 8. Complementary Orderbook Calculation

**Issue**: `best_ask()` is derived from opposite side's bid: `ask = 100 - opposite_bid`.

**Edge Case**: If NO bid is 101¢ (inverted market), YES ask would be calculated as -1¢.

**Current Handling**: `ask_price = max(0, min(100, 100 - best_opposite.price))` clamps to [0, 100].

**Risk**: If markets are frequently inverted, bot might see stale or invalid prices.

### 9. Message Ordering

**Issue**: WebSocket messages might arrive out of order (fill before orderbook update).

**Example**:
1. You place buy order @ 50¢
2. Fill notification arrives
3. Orderbook update arrives showing your order removed

**Current Handling**: No explicit ordering; assumes messages are processed in order.

**Mitigation**: Add sequence numbers or timestamps to detect out-of-order messages.

### 10. Position Limit Enforcement

**Issue**: Position limit is checked in strategy but not enforced at API level.

**Edge Case**: If multiple orders fill simultaneously, position could exceed limit.

**Mitigation**: Enforce position limit at exchange level (if supported) or add safety checks after fills.

---

## Deployment

### Prerequisites
- Python 3.8+
- Kalshi account with API access
- API key and private key (PEM file)
- Conda environment: `event_betting`

### Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment** (`.env` in project root):
   ```
   KALSHI_API_KEY_ID=your-key-id
   KALSHI_PRIVATE_KEY_PATH=/path/to/private_key.pem
   KALSHI_DEMO_MODE=true
   ```

3. **Configure markets** (`market_making/config.yaml`):
   ```yaml
   markets:
     - ticker: "MARKET-TICKER"
       side: "Both"
       position_limit: 10
   ```

4. **Activate conda environment**:
   ```bash
   conda activate event_betting
   ```

### Running the Bot

**Via script**:
```bash
./run_mm_bot.sh
```

**Direct invocation**:
```bash
python market_making/main.py
```

**With custom config**:
```bash
python market_making/main.py --config /path/to/config.yaml
```

### Monitoring

**Logs**: Printed to stdout with format:
```
YYYY-MM-DD HH:MM:SS - LEVEL - filename:line - message
```

**Key log patterns**:
- `[TICKER] Position Summary`: Position state after actions
- `[TICKER] Orders to add/cancel`: Order actions being taken
- `[TICKER] Fill`: Trade execution confirmation
- `[WS] Connection established`: WebSocket connected
- `[AUTH] 409 error`: Session conflict (may auto-retry)

### Stopping the Bot

Press `Ctrl+C` to gracefully shut down. The bot will:
1. Stop processing messages
2. Disconnect WebSocket
3. Log shutdown complete

**Note**: Outstanding orders are NOT automatically cancelled. You must cancel manually via API or UI.

---

## Development Notes

### Testing Strategy

1. **Unit Tests** (partially implemented):
   - ✅ `test_exit_logic.py`: Tests exit order generation logic for YES/NO positions
   - ✅ `test_acadia_throttling.py`: Tests order throttling, minimum quote time, position limits
   - ✅ `test_fill_triggered_orders.py`: Tests fill-triggered order regeneration
   - Location: `market_making/tests/`
   - Run with: `pytest market_making/tests/ -v`

2. **Test Coverage**:
   - ✅ Exit order generation for long YES positions
   - ✅ Exit order generation for long NO positions
   - ✅ Aggressive exit logic when position is losing
   - ✅ Minimum quote time throttling
   - ✅ Price movement hysteresis
   - ✅ Position limit enforcement with outstanding orders
   - ✅ Fill-triggered order regeneration
   - ❌ Multi-market operation (not tested)
   - ❌ WebSocket reconnection (not tested)
   - ❌ Order status reconciliation (not tested)

3. **Integration Tests** (manual):
   - ✅ Tested against Kalshi demo environment
   - ✅ Verified order placement and cancellation
   - ✅ Verified position tracking
   - ✅ Verified fill handling

4. **Backtesting** (not yet implemented):
   - Replay historical orderbook data
   - Measure strategy performance
   - Optimize parameters

### Code Quality Issues

**Inconsistent Error Handling**:
- Some methods return `None` on error
- Some methods raise exceptions
- Some methods log and continue

**Recommendation**: Standardize error handling approach.

**Missing Type Hints**:
- Most code has type hints, but some methods don't
- Add comprehensive type hints for better IDE support

**No Automated Tests**:
- Critical functions lack tests
- Risk of regressions when modifying code

### Performance Considerations

**Current Design**: Synchronous message processing in async loop

**Potential Bottleneck**: If message processing is slow, messages queue up in WebSocket client.

**Recommendation**: 
- Profile message processing time
- Consider async order placement if needed
- Monitor message queue depth

### Security Considerations

1. **API Keys**: Stored in environment variables, loaded from `.env`
   - ✅ Not committed to git
   - ⚠️ Accessible to any code running in the process

2. **Private Key**: Loaded from PEM file
   - ✅ File path in environment variable
   - ⚠️ Key loaded into memory as plaintext

3. **Demo Mode**: Uses demo credentials if `KALSHI_DEMO_MODE=true`
   - ✅ Prevents accidental production trading during development
   - ⚠️ Easy to flip to production by accident

**Recommendation**: Add additional safeguards before production use.

---

## Quick Reference

### File Structure
```
market_making/
├── main.py                 # Entry point, MarketMakingBot
├── acadia.py              # AcadiaStrategy (trading logic)
├── acadia_listener.py     # MarketListener (event processing)
├── order_manager.py       # OrderManager (order lifecycle)
├── orderbook_tracker.py   # OrderBookTracker (orderbook state)
├── market_types.py        # Data models
├── config.yaml            # Market configurations
└── CLAUDE.md              # This file
```

### Key Flows

**Order Placement Flow** (when working):
```
Orderbook Update → MarketState → Strategy → OrderIntent → 
OrderManager (sync) → Actions → API Call → Order ID → 
Track in OrderManager
```

**Fill Flow**:
```
WebSocket Fill → MarketListener → Update Position → 
Update Entry Price → Log → Strategy (on next orderbook update) → 
May trigger exit
```

### Common Commands

```bash
# Start bot
./run_mm_bot.sh

# Start with custom config
python market_making/main.py --config custom_config.yaml

# Check outstanding orders (Python)
from kalshi.client import KalshiAPIClient
from config import Config
client = KalshiAPIClient(Config())
orders = client.get_outstanding_orders()
```

---

## Summary for Future Agents

**What This Bot Does**:
- Connects to Kalshi via REST API and WebSocket
- Monitors orderbook updates for configured markets
- Places buy orders at best bid to open positions
- Places sell orders at best ask to close positions (capture spread)
- Tracks positions and entry prices
- Exits aggressively if position is down >25%

**Current State** (2025-10-30):
- ✅ Architecture and strategy logic complete
- ✅ Real order execution implemented
- ✅ Bot can place and cancel real orders
- ✅ Async order flow (non-blocking)
- ✅ Fill-triggered order regeneration
- ✅ Position limit enforcement
- ✅ Dry-run mode for testing
- ✅ Post-only order control
- ✅ Tested in demo environment
- ⚠️ Ready for live trading with careful monitoring

**Recent Major Fixes** (2025-10-30):
1. ✅ Fixed blocking sleep causing stale orderbook
2. ✅ Fixed 400 EOF error in order placement
3. ✅ Fixed position limit exceeded due to race condition
4. ✅ Fixed post-only cross errors
5. ✅ Fixed datetime timezone issues

**Known Issues**:
- Cancel/replace loops may still occur if:
  - Market is very volatile
  - `min_price_delta_cents` is set too low
  - Orders are being placed at stale prices
- Entry price calculation assumes FIFO, may not match exchange
- No order status reconciliation after bot restart

**Next Steps for Production**:
1. Add position reconciliation (periodic API sync)
2. Add performance metrics tracking
3. Add risk management (daily loss limits, circuit breakers)
4. Add comprehensive automated tests
5. Monitor for cancel/replace loops in live environment
6. Add logging and alerting infrastructure
7. Test with multiple markets simultaneously

**Risks & Warnings**:
- ⚠️ Aggressive exit at 25% loss may not be sufficient for volatile markets
- ⚠️ No daily loss limits implemented
- ⚠️ No circuit breakers for runaway losses
- ⚠️ Order throttling is 0.8s minimum between orders (configurable)
- ⚠️ Entry price calculation assumes FIFO, may not match exchange
- ⚠️ Outstanding orders can fill during cancellation (race condition mitigated but not eliminated)

**When Reading This Code**:
- Start with `main.py` to understand the flow
- Read `acadia.py` to understand the strategy
- Read `order_manager.py` to understand order lifecycle
- Check `acadia_listener.py` for async order execution
- Review recent bug fixes in this document to understand edge cases

**Testing Checklist**:
- [x] Implement real order execution
- [x] Test in demo environment
- [x] Test fill handling and position tracking
- [x] Test position limit enforcement
- [x] Test aggressive exit logic
- [ ] Test multi-market operation
- [ ] Test bot restart and state recovery
- [ ] Test WebSocket reconnection handling
- [ ] Monitor for cancel/replace loops in production

**Before Deploying to Production**:
- [x] Implement real order execution
- [x] Test thoroughly in demo environment
- [x] Add basic error handling
- [x] Add dry-run mode
- [x] Fix blocking sleep issue
- [ ] Add position reconciliation
- [ ] Add performance monitoring
- [ ] Add risk management (daily loss limits, etc.)
- [ ] Add comprehensive automated tests
- [ ] Review and adjust position limits for production
- [ ] Review and adjust aggressive exit threshold
- [ ] Set up logging and alerting infrastructure
- [ ] Add order status reconciliation after restart

**Critical Debugging Commands**:

```bash
# Enable dry-run mode
# Edit config.yaml: dry_run: true

# Check current orderbook state
# Logs show: [TICKER] Orderbook: YES bid/ask, NO bid/ask | YES levels: [...], NO levels: [...]

# Monitor cancel/replace decisions
# Look for: [ORDER] Cancel/replace due to PRICE/SIZE logs

# Check for blocking operations
# All sleep operations should be async: "await asyncio.sleep()"
```

---

*Last Updated: 2025-10-30*
*Bot Version: 0.2.0-beta (functional, tested in demo)*
*Next Session: Monitor cancel/replace behavior, implement position reconciliation*

