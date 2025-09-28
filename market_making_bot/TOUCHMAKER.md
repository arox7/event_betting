## TouchMaker Strategy

TouchMaker posts passive, post-only bids at/near the best bid on both YES and NO when the per-leg spread is wide enough. It is designed to harvest spread without aggressive chasing.

### Core behavior
- Posts at the current best bid per leg when `best_ask - best_bid ≥ min_spread_cents` and inventory allows.
- Does not aggressively chase when outbid; it generally holds the resting order.
- One-tick improvement: if `improve_if_last=True` and the front-of-queue is thin (`best_bid.size < queue_small_threshold`), improves price by exactly 1 tick if the resulting spread still passes `min_spread_cents`.
- Quote TTL: each entry has an expiration (`quote_ttl_seconds`). On refresh, targets are recomputed from the current book and restaged.
- Mid-move cancel: if mid changes by `cancel_move_ticks` or more between refreshes, all entries are cancelled and restaged at the new context.
- Always gated by per-leg spread and inventory caps.

### Algorithm outline
1. Read best bids/asks for YES and NO.
2. Compute per-leg spread; skip a leg if spread < `min_spread_cents`.
3. Check inventory room for each leg based on `max_inventory_contracts` and group caps (`touch_contract_limit`).
4. Choose target price = best bid, optionally +1 tick if `improve_if_last` and thin queue, while preserving spread floor.
5. Emit or refresh post-only order intents with TTL `quote_ttl_seconds`.
6. Reconcile: cancel intents no longer in targets; place new/changed ones.
7. On mid shift ≥ `cancel_move_ticks`, cancel all entries and restage.

### Key configuration knobs
- `min_spread_cents`: Per-leg spread floor to justify entry quotes.
- `bid_size_contracts`: Contracts per entry per leg.
- `quote_ttl_seconds`: Auto-refresh frequency for entry bids.
- `improve_if_last`: Enable 1-tick improvement on thin queues.
- `queue_small_threshold`: Queue size threshold considered “thin” for improvement.
- `max_inventory_contracts`: Net YES/NO cap across strategies (TouchMaker respects via `inventory_room`).
- `touch_contract_limit`: Per-strategy cap for TouchMaker group.
- `cancel_move_ticks`: Mid-move threshold that triggers cancel-and-restage.
- `sum_cushion_ticks`: Guardrail to avoid implied guaranteed-loss combinations when both legs are quoted.

### Chasing vs holding
- Holds by default: when someone overbids you, the resting order typically remains.
- Improve one tick at most when thin: controlled by `improve_if_last` and `queue_small_threshold`.
- No multi-tick chase loops: improvement is bounded to a single tick per decision cycle and only if spread remains healthy.

### How to tune
- Avoid chasing: set `improve_if_last=False`; consider longer `quote_ttl_seconds` to restage less often.
- Chase slightly more: lower `queue_small_threshold` (more scenarios count as thin) and reduce `quote_ttl_seconds` for quicker refreshes.
- Conserve risk: reduce `bid_size_contracts`, lower `touch_contract_limit`, and/or lower `max_inventory_contracts`.
- Tighten responsiveness to jumps: lower `cancel_move_ticks` from 2 → 1 to restage quicker on mid shifts.

### Operational tips
- Start in demo: run with `--with-private --demo-mode` to validate fills/positions plumbing.
- Real orders: add `--live-mode` once satisfied; keep `post_only` behavior to avoid crossing.
- Monitor logs: look for “[TOUCH] improving …” and “[CANCEL TOUCH] …” lines to verify improvement and reconciliation behavior.

### Example
Given YES best bid/ask = 70/74 (spread 4), NO best bid/ask = 26/30 (spread 4):
- Targets YES bid at 70 and NO bid at 26 for `bid_size_contracts`.
- If YES best-bid queue is thin and `improve_if_last=True`, it may improve YES to 71 provided 74 − 71 ≥ `min_spread_cents`.
- If mid moves by ≥ `cancel_move_ticks`, cancels existing entries and restages.


