"""Simulated broker: fills marketable orders against a live book snapshot.

Same code path as live -- the engine calls ``buy``/``sell`` identically -- but
fills are simulated against whatever order book snapshot the engine last fed in
via :meth:`set_books`. No money moves. This is where you build trust before
flipping ``mode`` to live.

Simplifications vs. reality (documented so they don't surprise you live):
  * Fills are instantaneous against the snapshot; real books move between
    quote and fill (slippage/adverse selection).
  * No queue position, no partial-fill latency, no maker rebates.
"""

from __future__ import annotations

from typing import Dict

from ..models import OrderBook
from .base import Broker, Fill, Side


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, starting_usdc: float):
        self._usdc = starting_usdc
        self._positions: Dict[str, float] = {}   # token_id -> tokens held
        self._books: Dict[str, OrderBook] = {}

    # the engine refreshes these each poll before placing orders
    def set_books(self, books: Dict[str, OrderBook]) -> None:
        self._books = books

    def usdc_balance(self) -> float:
        return self._usdc

    def position(self, token_id: str) -> float:
        return self._positions.get(token_id, 0.0)

    def buy(self, token_id: str, size: float, max_price: float) -> Fill:
        book = self._books.get(token_id)
        if book is None:
            return Fill(token_id, Side.BUY, size, 0.0, 0.0, 0.0, False, "no book")

        remaining = size
        cost = 0.0
        filled = 0.0
        for lvl in book.asks:
            if lvl.price > max_price + 1e-12:
                break  # respect the price cap
            take = min(remaining, lvl.size)
            cost += take * lvl.price
            filled += take
            remaining -= take
            if remaining <= 1e-9:
                break

        if filled <= 0:
            return Fill(token_id, Side.BUY, size, 0.0, 0.0, 0.0, False, "no fill under cap")
        if cost > self._usdc + 1e-9:
            return Fill(token_id, Side.BUY, size, 0.0, 0.0, 0.0, False, "insufficient USDC")

        self._usdc -= cost
        self._positions[token_id] = self.position(token_id) + filled
        avg = cost / filled
        return Fill(
            token_id, Side.BUY, size, filled, avg, -cost,
            ok=True,
            detail="filled" if abs(filled - size) < 1e-9 else "partial",
        )

    def sell(self, token_id: str, size: float, min_price: float) -> Fill:
        book = self._books.get(token_id)
        if book is None:
            return Fill(token_id, Side.SELL, size, 0.0, 0.0, 0.0, False, "no book")
        held = self.position(token_id)
        size = min(size, held)
        if size <= 0:
            return Fill(token_id, Side.SELL, 0.0, 0.0, 0.0, 0.0, False, "no position")

        remaining = size
        proceeds = 0.0
        filled = 0.0
        for lvl in book.bids:
            if lvl.price < min_price - 1e-12:
                break
            take = min(remaining, lvl.size)
            proceeds += take * lvl.price
            filled += take
            remaining -= take
            if remaining <= 1e-9:
                break

        if filled <= 0:
            return Fill(token_id, Side.SELL, size, 0.0, 0.0, 0.0, False, "no fill over floor")

        self._usdc += proceeds
        self._positions[token_id] = held - filled
        avg = proceeds / filled
        return Fill(token_id, Side.SELL, size, filled, avg, proceeds, ok=True, detail="filled")

    def redeem_set(self, token_ids: list, size: float, condition_id: str = "") -> float:
        """Merge ``size`` complete sets back to $1 each of USDC collateral.

        Burns ``size`` tokens of each leg and credits ``size`` USDC. Returns the
        USDC credited. This is what realizes the arb profit in paper mode.
        """
        for tid in token_ids:
            if self.position(tid) + 1e-9 < size:
                raise ValueError(f"cannot merge: short {tid}")
        for tid in token_ids:
            self._positions[tid] = self.position(tid) - size
        self._usdc += size  # complete set -> $1 each
        return size
