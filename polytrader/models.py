"""Pure data types. No network, no side effects -- safe to unit test."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class OrderLevel:
    """A single price level in an order book. Price is in USDC, range (0, 1)."""

    price: float
    size: float  # number of outcome tokens (shares) available at this price


@dataclass
class OrderBook:
    """One outcome token's order book.

    ``asks`` must be sorted ascending by price (cheapest first) and ``bids``
    descending by price (highest first) -- this is how the CLOB returns them.
    We don't re-sort defensively in the hot path; :meth:`from_clob` normalizes.
    """

    token_id: str
    asks: List[OrderLevel] = field(default_factory=list)
    bids: List[OrderLevel] = field(default_factory=list)

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    def ask_liquidity(self) -> float:
        """Total token size resting on the ask side."""
        return sum(lvl.size for lvl in self.asks)

    def cost_to_buy(self, size: float) -> Optional[float]:
        """USDC cost to buy ``size`` tokens by walking the asks.

        Returns ``None`` if there isn't enough resting size to fill ``size``.
        This is a marketable (taker) fill that respects book depth, not just
        the top-of-book price -- which matters because top-of-book size is
        often tiny and quoting on it alone overstates the edge.
        """
        if size <= 0:
            return 0.0
        remaining = size
        cost = 0.0
        for lvl in self.asks:
            take = min(remaining, lvl.size)
            cost += take * lvl.price
            remaining -= take
            if remaining <= 1e-9:
                return cost
        return None  # not enough depth

    def proceeds_from_sell(self, size: float) -> Optional[float]:
        """USDC received from selling ``size`` tokens by walking the bids."""
        if size <= 0:
            return 0.0
        remaining = size
        proceeds = 0.0
        for lvl in self.bids:
            take = min(remaining, lvl.size)
            proceeds += take * lvl.price
            remaining -= take
            if remaining <= 1e-9:
                return proceeds
        return None

    @classmethod
    def from_clob(cls, token_id: str, raw: dict) -> "OrderBook":
        """Build from the Polymarket CLOB ``/book`` JSON shape.

        CLOB returns ``{"asks": [{"price": "0.52", "size": "100"}, ...],
        "bids": [...]}`` with string-encoded numbers and no guaranteed sort.
        """

        def levels(side: list) -> List[OrderLevel]:
            return [OrderLevel(float(x["price"]), float(x["size"])) for x in side or []]

        asks = sorted(levels(raw.get("asks", [])), key=lambda l: l.price)
        bids = sorted(levels(raw.get("bids", [])), key=lambda l: l.price, reverse=True)
        return cls(token_id=token_id, asks=asks, bids=bids)


@dataclass
class Token:
    """One outcome of a market (e.g. the YES token of a binary market)."""

    token_id: str
    outcome: str
    book: Optional[OrderBook] = None


@dataclass
class Market:
    """A market whose ``tokens`` form a complete, mutually-exclusive set.

    For a binary market that's [YES, NO]; for a categorical market it's the
    full list of outcomes. By construction exactly one resolves to $1, so a
    complete set (one of each) is always worth $1 -- that invariant is the
    whole basis of the arb.
    """

    condition_id: str
    question: str
    tokens: List[Token] = field(default_factory=list)
    closed: bool = False
    enable_order_book: bool = True

    def has_full_books(self) -> bool:
        return bool(self.tokens) and all(t.book is not None for t in self.tokens)


@dataclass(frozen=True)
class ArbOpportunity:
    """A detected buy-the-complete-set-cheap opportunity."""

    market: Market
    size: float  # number of complete sets we can buy at this edge
    set_cost: float  # USDC to acquire ONE complete set (depth-aware avg)
    total_cost: float  # USDC for the full ``size``
    costs_per_set: float  # fees + gas charged per set
    edge_per_set: float  # $1 - set_cost - costs_per_set  (profit per set)
    total_edge: float  # edge_per_set * size

    @property
    def edge_bps(self) -> float:
        """Edge as basis points of the $1 redemption value."""
        return self.edge_per_set * 10_000

    def describe(self) -> str:
        legs = " + ".join(
            f"{t.outcome}@{t.book.best_ask:.3f}" if t.book and t.book.best_ask else f"{t.outcome}@?"
            for t in self.market.tokens
        )
        return (
            f"[ARB] {self.market.question[:60]!r} "
            f"set_cost=${self.set_cost:.4f} (+${self.costs_per_set:.4f} costs) "
            f"edge=${self.edge_per_set:.4f}/set ({self.edge_bps:.0f}bps) "
            f"size={self.size:.1f} total_edge=${self.total_edge:.2f} | {legs}"
        )
