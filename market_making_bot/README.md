# Kalshi Market Making Bot

A dual-sided mock market maker for Kalshi prediction markets. The bot listens to Kalshi WebSocket streams, maintains passive bids on both YES and NO legs simultaneously, and posts maker-only exit asks after fills. It prints every intended action (quotes, reprices, exits, reduce-only suggestions) so you can validate the strategy before wiring real order placement.

## Features

### 🧠 Strategy Logic
- **Dual passive bids**: Posts bid-YES and bid-NO together while respecting `bid_yes + bid_no ≤ 100 - cushion` to avoid guaranteed losses.
- **Post-only exits**: After a fill, rests a post-only ask on the filled leg to farm the spread (default `take_profit_ticks` over the entry).
- **Inventory-aware**: Positions are sourced directly from the Kalshi `market_positions` WebSocket; exits cancel automatically when inventory goes flat.
- **Reduce-only prompts**: When inventory hits caps, prints guidance to send reduce-only IOC orders (real order wiring omitted).

### 📊 Real-time Market Data
- **Orderbook Updates**: Live ladder maintained via `OrderBookTracker` with best N levels printed on every snapshot/delta.
- **Market Ticker**: Bid/ask/last/volume logs for additional context.
- **Public Trades**: Logs every exchange trade for the subscribed market.
- **User Fills & Positions**: WebSocket streams for fills and positions are consumed (the strategy uses positions for inventory, fills for logging only).

### 🔐 Authentication Support
- Uses credentials from `.env` when `--with-private` is supplied.
- Supports both demo and production Kalshi endpoints via the existing `Config` class.

## Quick Start

```bash
source ~/.bash_profile
conda activate event_betting
python market_making_bot/mm_ws_listener.py --ticker KXKIMMELAPOLOGY-25SEP29 --with-private
```

### Handy CLI Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--ticker` | Kalshi market ticker (e.g., `KXKIMMELAPOLOGY-25SEP29`) | — |
| `--with-private` | Subscribe to authenticated `fills` + `market_positions` streams | off |
| `--min-spread` | Minimum spread (per leg) required before bidding | `3` |
| `--bid-size` | Contracts per passive bid on each leg | `5` |
| `--exit-size` | Contracts per exit ask after fills | `5` |
| `--sum-cushion` | Guardrail: ensure `bid_yes + bid_no ≤ 100 - cushion` | `3` |
| `--take-profit` | Ticks above entry to target when posting an exit ask | `2` |
| `--quote-ttl` | Seconds before entry bids auto-refresh | `6` |
| `--exit-ttl` | Seconds before exit asks auto-expire | `20` |
| `--max-inventory` | Per-leg inventory cap (contracts) | `100` |
| `--reduce-step` | Suggested reduce-only IOC size when at cap | `10` |
| `--no-improve` | Disable the one-tick improvement on thin queues | off |

## How It Works

1. **Orderbook tracking**: `orderbook_tracker.py` maintains YES/NO ladders from Kalshi snapshots/deltas, exposing utilities like `best_bid`, `best_ask`, `spread`, and `top_levels`.
2. **Strategy core** (`strategy.py`):
   - `DualSidedMockMaker` receives order book updates, market positions, will compute entry targets for both legs, and prints the bids it would post, repricing as midpoints move.
   - On fills, inventory is read from the `market_positions` stream; the strategy logs fills and refreshes exit asks accordingly.
   - Reduce-only suggestions are printed when inventory exceeds configured caps.
3. **Listener** (`mm_ws_listener.py`): wires the Kalshi WebSocket client to the tracker and strategy, logs public ticker/trade data, and forwards private events when authenticated.

## Project Layout

```
market_making_bot/
├── mm_ws_listener.py     # WebSocket entry point + CLI
├── orderbook_tracker.py  # Snapshot/delta bookkeeping for YES/NO ladders
├── strategy.py           # Dual-sided mock maker
├── README.md             # You are here
└── example_usage.py      # Sample script showing direct strategy usage
```

## Limitations & Next Steps

- **No real orders**: The bot prints the actions it would take. To place actual orders, swap in Kalshi order-placement calls inside the strategy’s logging blocks.
- **No risk engine**: Inventory caps are static; more advanced risk measures (delta, margin, total cash) should be bolted on before going live.
- **Single market**: Currently handles one ticker. Extend the tracker and strategy to juggle multiple markets if needed.

## References

- [Kalshi WebSocket API](https://docs.kalshi.com/api-reference/websockets/)
- [Orderbook Updates stream](https://docs.kalshi.com/api-reference/websockets/orderbook-updates)
- [Public Trades stream](https://docs.kalshi.com/api-reference/websockets/public-trades)
- [Market Positions stream](https://docs.kalshi.com/api-reference/websockets/market-positions)

Happy quoting! 🎯
