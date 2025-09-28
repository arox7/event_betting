"""
Shared configuration and data classes for market making strategies.

This module contains common data structures used across multiple strategy files,
allowing them to be imported independently without circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


def _now_str() -> str:
    """Generate timestamp string for logging."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def clamp(value: int, lo: int, hi: int) -> int:
    """Clamp value between lo and hi bounds."""
    return max(lo, min(hi, value))


@dataclass
class OrderIntent:
    """Representation of a Create Order request we *would* send."""

    ticker: str
    strategy: str
    purpose: str  # e.g. "entry"/"exit"
    action: str  # "buy"/"sell"
    side: str  # "yes"/"no"
    price_cents: int
    count: int
    post_only: bool
    expiration_ts: Optional[int]
    order_group_id: str
    client_order_id: str
    hedge_with: Optional[str] = None

    def to_api_payload(self) -> Dict[str, Any]:
        """Convert OrderIntent to Kalshi API payload format."""
        field = "yes_price" if self.side == "yes" else "no_price"
        payload: Dict[str, Any] = {
            "action": self.action,
            "side": self.side,
            "ticker": self.ticker,
            field: self.price_cents,
            "count": self.count,
            "post_only": self.post_only,
            "client_order_id": self.client_order_id,
            "order_group_id": self.order_group_id,
        }
        if self.expiration_ts is not None:
            payload["expiration_ts"] = self.expiration_ts
        if self.hedge_with:
            payload["hedge_with_client_order_id"] = self.hedge_with
        return payload


@dataclass
class OrderGroupState:
    """Tracks cap + pending orders for a strategy bucket."""

    name: str
    contracts_limit: int
    filled_contracts: int = 0
    created: bool = False
    pending: Dict[str, OrderIntent] = field(default_factory=dict)
    group_id: Optional[str] = None

    def remaining(self) -> int:
        """Calculate remaining capacity in this group."""
        return max(0, self.contracts_limit - self.filled_contracts)

    def register_intent(self, intent: OrderIntent) -> None:
        """Register a new order intent in this group."""
        self.pending[intent.client_order_id] = intent

    def add_intent(self, client_order_id: str, order: OrderIntent) -> None:
        """Add an order intent to this group."""
        self.pending[client_order_id] = order

    def remove_intent(self, client_order_id: str) -> None:
        """Remove an order intent from this group."""
        self.pending.pop(client_order_id, None)

    def register_fill(self, count: int) -> None:
        """Register a fill that counts against this group's capacity."""
        self.filled_contracts += count


@dataclass
class StrategyConfig:
    """High level knobs for all strategies."""

    ticker: str
    live_mode: bool = False
    min_spread_cents: int = 3
    bid_size_contracts: int = 5
    exit_size_contracts: int = 5
    sum_cushion_ticks: int = 3
    take_profit_ticks: int = 2
    quote_ttl_seconds: int = 6
    exit_ttl_seconds: int = 20
    cancel_move_ticks: int = 2
    max_inventory_contracts: int = 100
    reduce_only_step_contracts: int = 10

    touch_enabled: bool = True
    touch_contract_limit: int = 40

    depth_enabled: bool = True
    depth_contract_limit: int = 120
    depth_levels: int = 3
    depth_step_ticks: int = 2

    band_enabled: bool = True
    band_contract_limit: int = 80
    band_half_width_ticks: int = 4
    band_rungs: int = 2

    queue_small_threshold: int = 50
    queue_big_threshold: int = 400
    exit_ladder_threshold: int = 30
    improve_if_last: bool = True
    
    # Hysteresis and anti-oscillation
    queue_thin_threshold: int = 40
    queue_thick_threshold: int = 150
    min_time_between_requotes_ms: int = 1000  # 1 second
    cooldown_after_shade_ms: int = 3000  # 3 seconds after shading down