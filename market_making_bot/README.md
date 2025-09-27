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

To actually place orders, add `--live-mode`.

### Handy CLI Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--ticker` | Kalshi market ticker (e.g., `KXKIMMELAPOLOGY-25SEP29`) | — |
| `--with-private` | Subscribe to authenticated `fills` + `market_positions` streams | off |
| `--demo-mode` | Use Kalshi demo environment (recommended for first runs) | off |
| `--live-mode` | Execute real create/cancel orders via API (otherwise dry-run only) | off |
| `--min-spread` | Minimum spread (per leg) required before bidding | `3` |
| `--bid-size` | Contracts per passive bid on each leg | `5` |
| `--exit-size` | Contracts per exit ask after fills | `5` |
| `--sum-cushion` | Guardrail: ensure `bid_yes + bid_no ≤ 100 - cushion` | `3` |
| `--take-profit` | Ticks above entry to target when posting an exit ask | `2` |
| `--quote-ttl` | Seconds before entry bids auto-refresh | `6` |
| `--exit-ttl` | Seconds before exit asks auto-expire | `20` |
| `--max-inventory` | Per-leg inventory cap (contracts) | `100` |
| `--reduce-step` | Suggested reduce-only IOC size when at cap | `10` |
| `--touch-cap` | Contracts cap for TouchMaker | `40` |
| `--depth-cap` | Contracts cap for DepthLadder | `120` |
| `--band-cap` | Contracts cap for BandReplenish | `80` |
| `--depth-levels` | Number of ladder levels for DepthLadder | `3` |
| `--depth-step` | Tick step between DepthLadder levels | `2` |
| `--band-rungs` | Number of rungs in BandReplenish | `2` |
| `--band-width` | Half-width (ticks) for band replenishment | `4` |
| `--no-depth` | Disable DepthLadder strategy | off |
| `--no-band` | Disable BandReplenish strategy | off |
| `--no-touch` | Disable TouchMaker strategy | off |

## How It Works

1. **Orderbook tracking**: `orderbook_tracker.py` maintains YES/NO ladders from Kalshi snapshots/deltas, exposing utilities like `best_bid`, `best_ask`, `spread`, and `top_levels`.
2. **Strategy core** (`strategy.py`):
   - `DualSidedMockMaker` receives order book updates, market positions, will compute entry targets for both legs, and prints the bids it would post, repricing as midpoints move.
   - On fills, inventory is read from the `market_positions` stream; the strategy logs fills and refreshes exit asks accordingly.
   - Reduce-only suggestions are printed when inventory exceeds configured caps.
3. **Listener** (`mm_ws_listener.py`): wires the Kalshi WebSocket client to the tracker and strategy, logs public ticker/trade data, and forwards private events when authenticated.

## Strategy Profiles

### TouchMaker
- **Goal**: Sit at/near the best bid on both YES and NO when the per-leg spread is wide enough, optionally improving one tick on thin queues.
- **When it acts**: If `best_ask - best_bid ≥ min_spread_cents` on a leg and you still have inventory room.
- **Knobs**: `min_spread_cents`, `bid_size_contracts`, `quote_ttl_seconds`, `improve_if_last`, `queue_small_threshold`.

### DepthLadder
- **Goal**: Stair-step bids below the top of book across N levels to catch pulls and mean reversion.
- **When it acts**: For each level `L ∈ [1..depth_levels]`, places a bid `depth_step_ticks * L` below the current best bid if spread versus best ask is still ≥ `min_spread_cents`.
- **Knobs**: `depth_levels`, `depth_step_ticks`, plus the same entry controls as TouchMaker.

### BandReplenish
- **Goal**: Quote symmetrically around the mid within a fixed band to “reseed” liquidity after jumps.
- **When it acts**: Computes mid from YES best bid/ask and places `band_rungs` bids on each leg at `mid - offset` (YES) and `(100 - mid) - offset` (NO) where `offset = rung * band_half_width_ticks`, provided the per-leg spread check passes.
- **Knobs**: `band_rungs`, `band_half_width_ticks`.

### Exit Maker
- **Goal**: After fills, rest post-only asks to realize spread. For large inventory it builds a small exit ladder.
- **When it acts**: If `inventory(leg) > 0`, computes a tactical exit price relative to the current best offer and queue sizes; otherwise cancels all exits on that leg.
- **Knobs**: `exit_size_contracts`, `exit_ttl_seconds`, `exit_ladder_threshold`, `queue_small_threshold`, `queue_big_threshold`.

Note: The config includes `sum_cushion_ticks` to discourage posting both legs in a way that implies a guaranteed loss (`bid_yes + bid_no > 100`). The current engine primarily gates by per-leg spread and inventory; enforce a hard sum-cushion in code if you require it operationally.

## TouchMaker-only Runbook

TouchMaker posts passive, post-only bids at/near the best bid on both YES and NO, harvesting wide per-leg spreads. Use this runbook to run ONLY TouchMaker on a single ticker.

### Prereqs

- Conda env and dependencies:
  - `source ~/.bash_profile`
  - `conda activate event_betting`
- Kalshi credentials in `.env` or configured via `Config` so private streams authenticate.

### Start the bot (TouchMaker only)

Dry-run in demo mode with private streams, disabling other strategies:

```bash
python market_making_bot/mm_ws_listener.py \
  --ticker KXGDP-25Q4 \
  --with-private --demo-mode \
  --no-depth --no-band \
  --min-spread 4 \
  --bid-size 2 \
  --quote-ttl 10 \
  --exit-size 2 --exit-ttl 1800 \
  --max-inventory 20 \
  --sum-cushion 5 --take-profit 2
```

Notes:
- Orders created by TouchMaker and Exit Maker are post-only by design. There is no CLI toggle; this avoids crossing in thin markets.
- `--live-mode` submits actual orders/cancels through the API; omit it for safe dry-run logging only.
- `--with-private` is required to receive fills and positions; without it, inventory/exit behavior won’t function.
- `--demo-mode` points to Kalshi’s demo cluster; remove it to target production once satisfied.

### Operational checklist

1. Pick a ticker with wide per-leg spreads (≥ 3–4 ticks) and sensible event timing.
2. Start in demo mode for at least 15–30 minutes to observe: quotes posted, cancels on mid-move, exits appearing after simulated fills.
3. Tune quoting cadence (`--quote-ttl`) and exit persistence (`--exit-ttl`).
4. Set conservative limits: `--bid-size`, `--max-inventory`, `--touch-cap` (via `--touch-cap` if needed).
5. Promote to production: drop `--demo-mode` and keep `--with-private`.

### Recommended settings for low-liquidity, wide-spread Kalshi markets

- Quote cadence (`--quote-ttl`): 8–20s. Short enough to keep up with drift, long enough not to churn.
- Exit persistence (`--exit-ttl`): 600–3600s (10–60 min). Fills can take hours; keep exits resting.
- Spread floor (`--min-spread`): 4–6 ticks to get paid for inventory risk.
- Size (`--bid-size`, `--exit-size`): 1–3 contracts to avoid bloating queues; scale up later.
- Inventory caps (`--max-inventory`, `--touch-cap`): keep small (e.g., 10–30) until behavior is proven.
- Mid-move hysteresis (code-level): `cancel_move_ticks` defaults to 2; tighten to 1 if quotes get stale. This is a code knob not exposed via CLI.
- Improvement behavior (code-level): `improve_if_last=True` by default. In very crowded queues you may change it to `False` in `StrategyConfig` (not yet exposed via CLI).
- Sum cushion (`--sum-cushion`): 4–6, to reduce guaranteed-loss risk if both legs fill simultaneously.

### Example behavior (TouchMaker only)

Ticker: `KXGDP-25Q4`

```
YES: best_bid=42 (size 18), best_ask=46 (size 12)  -> spread=4
 NO: best_bid=53 (size 22), best_ask=57 (size 14)  -> spread=4
```

- Entries: posts `BUY YES 2 @ 42` and `BUY NO 2 @ 53` (post-only) if under caps.
- Thin front-of-queue: may improve to 43/54 if one-tick tighter still leaves ≥ `--min-spread`.
- After a YES fill, places post-only exits sized by `--exit-size` with TTL `--exit-ttl` at or near best ask, at least `--min-spread` above best bid.
- If mid drifts by ≥ 2 ticks (default), existing bids are cancelled and restaged at the new context.

In very thin books (e.g., spreads 6–10 ticks), use larger `--exit-ttl` so exits remain working; otherwise you may time-out repeatedly and lose queue priority.

## Kalshi-Grounded Examples

Assume `min_spread_cents=3`, `bid_size_contracts=5`, `exit_size_contracts=5`, `quote_ttl_seconds=6`, `exit_ttl_seconds=20`.

### Example 1 — TouchMaker on a wide spread
Ticker: `KXGDP-25Q4`

Orderbook snapshot (YES/NO are complements on Kalshi):

```
YES: best_bid=42 (size 18), best_ask=46 (size 12)  -> spread=4
 NO: best_bid=53 (size 22), best_ask=57 (size 14)  -> spread=4
```

- TouchMaker posts bids at 42 YES and 53 NO for 5 contracts each (both pass spread and inventory checks).
- If the best-bid queue on YES is thin (e.g., size 12 < `queue_small_threshold`), it may improve to 43 provided the resulting spread to best ask remains ≥ 3.
- If YES fills 5@42, Exit Maker posts a post-only ask at least `min_spread` over the best bid, usually near the current best ask (e.g., 46 or 45 depending on queue pressure) for `exit_size_contracts`.

### Example 2 — DepthLadder catching a pull
Ticker: `KXJOBS-25SEP` (weekly NFP release)

Orderbook snapshot:

```
YES: best_bid=31, best_ask=35  -> spread=4
 NO: best_bid=64, best_ask=68  -> spread=4
```

Config: `depth_levels=3`, `depth_step_ticks=2`.

- YES depth targets: 29, 27, 25 (each checked against current YES best ask for spread ≥ 3).
- NO depth targets: 62, 60, 58 (checked against NO best ask for spread ≥ 3).
- If a large seller pulls the YES bid at 31, your 29 might suddenly become best bid and fill; Exit Maker will then stage a 33–35 ask depending on the live offer and queue sizes.

### Example 3 — BandReplenish after a jump
Ticker: `KXINFLATION-25Q1`

Suppose mid jumps from 50 → 56 after a trade burst:

```
YES: best_bid=55, best_ask=57  -> mid≈56
 NO: best_bid=43, best_ask=45  -> mid for NO≈44
```

Config: `band_rungs=2`, `band_half_width_ticks=4`.

- YES targets at 52 and 48 (if spread to best ask passes checks); NO targets at 40 and 36.
- These “reseed” quotes often get filled when mean reversion snaps back after the burst.

### Example 4 — Exit Maker ladder on heavy inventory
You’re long 120 YES from various entries on `KXGDP-25Q4` and `exit_ladder_threshold=30`.

```
YES: best_bid=41 (size 80), best_ask=44 (size 10)
``;

- Ladder prices might be 43, 44, 45 with rung sizes capped by `exit_size_contracts` (e.g., 30/30/30), all expiring in `exit_ttl_seconds` unless refreshed.
- If recent public trades had a NO taker (selling YES), the model can nudge exit quotes up a tick to improve fill odds.

## Success Modes
- **Wide spreads with thin queues**: TouchMaker improvements capture the spread quickly while keeping post-only behavior.
- **Pulls/air pockets**: DepthLadder bids get promoted to top-of-book after liquidity pulls, earning favorable entries.
- **Mean-reversion after jumps**: BandReplenish provides liquidity near reversion levels, then harvests via exits.
- **Disciplined TTLs**: Auto-refresh limits stale exposure and helps chase drifting mids safely.

## Failure Modes and Mitigations
- **Momentum runs through the book**: Entries fill and price keeps moving. Mitigate with smaller `bid_size_contracts`, tighter `cancel_move_ticks`, and quicker `exit_ttl_seconds`.
- **Spread collapses**: Per-leg spread falls below `min_spread_cents`; entries pause. Consider dynamic `min_spread_cents` by session or disable improvement on crowded queues (`improve_if_last=False`).
- **Inventory bloat**: Multiple fills accumulate. Use `max_inventory_contracts`, increase `exit_ladder_threshold`, and wire reduce-only IOC for de-risking when caps hit.
- **Queue-crowding at offer**: Exits stuck behind large size. The exit logic tightens one tick on thin offers; consider nudging `queue_small_threshold` or increasing `exit_size_contracts` judiciously.
- **Sum-of-bids risk**: If `bid_yes + bid_no` creeps toward 100, you risk guaranteed-loss combos on simultaneous fills. Enforce `sum_cushion_ticks` in code or disable one leg when the other is high.
- **Event-driven gaps**: Macro prints or headlines gap the market; TTL-based cancels help, but consider pausing around known releases.

## Operational Tips
- Start in demo mode and `--with-private` to validate positions/exit behavior against your account.
- Set `--quote-ttl` low (3–6s) while tuning; increase once behavior is stable.
- For tight markets, disable improvement (`--no-improve`) to avoid needless queue shuffling.

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

- **Real orders optional**: By default the bot runs in dry-run mode (logs intended actions). Add `--live-mode` to actually place/cancel orders; use `--demo-mode` to target demo first before production.
- **No risk engine**: Inventory caps are static; more advanced risk measures (delta, margin, total cash) should be bolted on before going live.
- **Single market**: Currently handles one ticker. Extend the tracker and strategy to juggle multiple markets if needed.

## References

- [Kalshi WebSocket API](https://docs.kalshi.com/api-reference/websockets/)
- [Orderbook Updates stream](https://docs.kalshi.com/api-reference/websockets/orderbook-updates)
- [Public Trades stream](https://docs.kalshi.com/api-reference/websockets/public-trades)
- [Market Positions stream](https://docs.kalshi.com/api-reference/websockets/market-positions)

Happy quoting! 🎯
