"""Shared order book tracker utilities for Kalshi market making tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


class OrderBookError(Exception):
    """Base class for order book tracking errors."""


@dataclass
class BestLevel:
    """Container for best bid/ask information."""

    price: Optional[int]
    size: int


class OrderBookTracker:
    """Maintain YES/NO order book levels from Kalshi WS snapshots/deltas."""

    SIDES = ("yes", "no")

    def __init__(self) -> None:
        self._books: Dict[str, Dict[int, int]] = {"yes": {}, "no": {}}
        self._has_snapshot: bool = False

    # ------------------------------------------------------------------
    # Snapshot / delta processing
    # ------------------------------------------------------------------

    def apply_snapshot(self, snapshot: Dict) -> None:
        """Replace current books with a fresh snapshot."""

        try:
            yes_levels: Iterable[Iterable[int]] = snapshot["yes"]
            no_levels: Iterable[Iterable[int]] = snapshot["no"]
        except KeyError as exc:  # pragma: no cover - defensive, docs guarantee fields
            raise OrderBookError(f"Snapshot missing expected key: {exc}") from exc

        self._books["yes"] = {
            int(price_cents): int(size_contracts)
            for price_cents, size_contracts in yes_levels
            if int(size_contracts) > 0
        }
        self._books["no"] = {
            int(price_cents): int(size_contracts)
            for price_cents, size_contracts in no_levels
            if int(size_contracts) > 0
        }
        self._has_snapshot = True

    def apply_delta(self, delta: Dict) -> None:
        """Apply an incremental update to the order book."""

        if not self._has_snapshot:
            raise OrderBookError("Received orderbook delta before snapshot was applied")

        try:
            price_cents = int(delta["price"])
            change_contracts = int(delta["delta"])
            side = delta["side"]
        except KeyError as exc:
            raise OrderBookError(f"Delta missing expected key: {exc}") from exc

        if side not in self.SIDES:
            raise OrderBookError(f"Unexpected orderbook side: {side}")

        book = self._books.setdefault(side, {})
        new_size_contracts = book.get(price_cents, 0) + change_contracts

        if new_size_contracts <= 0:
            book.pop(price_cents, None)
        else:
            book[price_cents] = new_size_contracts

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_snapshot(self) -> bool:
        return self._has_snapshot

    def top_levels(self, side: str, max_levels: int = 5) -> List[Tuple[int, int]]:
        self._validate_side(side)
        levels = sorted(
            self._books[side].items(), key=lambda item: item[0], reverse=True
        )
        if max_levels is None or max_levels >= len(levels):
            return levels
        return levels[:max_levels]

    def level_count(self, side: str) -> int:
        self._validate_side(side)
        return len(self._books[side])

    def size_at(self, side: str, price: int) -> int:
        self._validate_side(side)
        return self._books[side].get(price, 0)

    def best_bid(self, side: str) -> BestLevel:
        self._validate_side(side)
        if not self._books[side]:
            return BestLevel(None, 0)
        price = max(self._books[side])
        return BestLevel(price=price, size=self._books[side][price])

    def best_ask(self, side: str) -> BestLevel:
        """Kalshi YES/NO books are complementary; ask is derived from the opposite bid."""

        opposite = self._opposite(side)
        best_opposite = self.best_bid(opposite)
        if best_opposite.price is None:
            return BestLevel(None, 0)

        # YES price + NO price == 100 (both in cents)
        ask_price = max(0, min(100, 100 - best_opposite.price))
        return BestLevel(price=ask_price, size=best_opposite.size)

    def spread(self, side: str) -> Optional[int]:
        bid = self.best_bid(side)
        ask = self.best_ask(side)
        if bid.price is None or ask.price is None:
            return None
        return ask.price - bid.price

    def is_inverted(self, side: str) -> bool:
        bid = self.best_bid(side)
        ask = self.best_ask(side)
        if bid.price is None or ask.price is None:
            return False
        return ask.price < bid.price

    def as_dict(self) -> Dict[str, Dict[int, int]]:
        return {
            side: dict(levels)
            for side, levels in self._books.items()
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _opposite(side: str) -> str:
        return "no" if side == "yes" else "yes"

    def _validate_side(self, side: str) -> None:
        if side not in self.SIDES:
            raise ValueError(f"Unknown side: {side}")

