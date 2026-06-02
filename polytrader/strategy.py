"""Arbitrage detection -- pure functions over market snapshots.

The core insight: for a market whose outcome tokens form a complete set, buying
one of *every* outcome guarantees a $1.00 payout (winner pays $1, losers pay
$0; or merge the set back to $1 of collateral). So:

    edge_per_set = REDEMPTION (1.0) - cost_to_buy_one_set - per_set_costs

If that's positive and above a threshold, it's an opportunity. We size it by
how many complete sets the *thinnest* leg can fill, because we must buy every
leg in equal quantity for the set to be complete.
"""

from __future__ import annotations

from typing import List, Optional

from .models import ArbOpportunity, Market

REDEMPTION_VALUE = 1.0  # a complete set always redeems for exactly $1.00


def _max_fillable_sets(market: Market) -> float:
    """Largest number of complete sets buyable given each leg's ask depth.

    Bounded by the leg with the least ask liquidity -- you can't have a partial
    complete set.
    """
    depths = []
    for t in market.tokens:
        if t.book is None:
            return 0.0
        depths.append(t.book.ask_liquidity())
    return min(depths) if depths else 0.0


def cost_to_buy_sets(market: Market, size: float) -> Optional[float]:
    """Depth-aware USDC cost to buy ``size`` complete sets, or None if too thin."""
    total = 0.0
    for t in market.tokens:
        if t.book is None:
            return None
        leg = t.book.cost_to_buy(size)
        if leg is None:
            return None
        total += leg
    return total


def find_buy_arb(
    market: Market,
    *,
    min_edge_per_set: float,
    fee_bps: float = 0.0,
    gas_cost_per_set: float = 0.0,
    max_size: Optional[float] = None,
) -> Optional[ArbOpportunity]:
    """Detect a buy-the-complete-set arbitrage on ``market``.

    Args:
        min_edge_per_set: minimum $ profit per set required to report (after
            costs). Set this above your cost noise so marginal "edges" that a
            single adverse tick would erase are ignored.
        fee_bps: taker fee in basis points of the set's $1 value (Polymarket
            CLOB has historically been 0; parameterized so you can stress it).
        gas_cost_per_set: amortized on-chain merge/redeem gas per set, in USDC.
        max_size: cap the reported size (risk layer also caps; this keeps the
            depth-walk cheap).

    Returns the opportunity sized at the largest depth-feasible quantity whose
    *marginal* edge still clears ``min_edge_per_set``, or None.
    """
    if market.closed or not market.enable_order_book or not market.has_full_books():
        return None

    feasible = _max_fillable_sets(market)
    if feasible <= 0:
        return None
    if max_size is not None:
        feasible = min(feasible, max_size)

    per_set_costs = fee_bps / 10_000.0 * REDEMPTION_VALUE + gas_cost_per_set

    # Quick reject on top-of-book: if even one set's best asks don't clear the
    # threshold, deeper fills (which are only more expensive) can't either.
    top = cost_to_buy_sets(market, _quote_unit(market))
    if top is None:
        return None
    top_set_cost = top / _quote_unit(market)
    if REDEMPTION_VALUE - top_set_cost - per_set_costs < min_edge_per_set:
        return None

    # Grow the size while the *average* edge over the whole fill stays above the
    # threshold. We coarse-search then refine so this stays cheap on deep books.
    best: Optional[ArbOpportunity] = None
    for size in _candidate_sizes(feasible):
        total_cost = cost_to_buy_sets(market, size)
        if total_cost is None:
            continue
        set_cost = total_cost / size
        edge_per_set = REDEMPTION_VALUE - set_cost - per_set_costs
        if edge_per_set < min_edge_per_set:
            continue
        opp = ArbOpportunity(
            market=market,
            size=size,
            set_cost=set_cost,
            total_cost=total_cost,
            costs_per_set=per_set_costs,
            edge_per_set=edge_per_set,
            total_edge=edge_per_set * size,
        )
        # Prefer the opportunity with the largest *total* edge captured.
        if best is None or opp.total_edge > best.total_edge:
            best = opp
    return best


def _quote_unit(market: Market) -> float:
    """A tiny reference size used for the top-of-book quick check."""
    # One token is the natural unit; but if best-ask depth is sub-1, fall back.
    return 1.0


def _candidate_sizes(feasible: float) -> List[float]:
    """Sizes to evaluate between a tiny floor and the feasible max.

    A geometric ladder keeps the search cheap (~20 points) while still finding
    the sweet spot where average edge peaks before deep, pricier levels drag it
    back below threshold.
    """
    if feasible <= 0:
        return []
    floor = min(1.0, feasible)
    sizes = []
    s = floor
    while s < feasible:
        sizes.append(round(s, 6))
        s *= 1.5
    sizes.append(round(feasible, 6))
    # de-dup while preserving order
    seen = set()
    out = []
    for x in sizes:
        if x not in seen and x > 0:
            seen.add(x)
            out.append(x)
    return out


def scan(
    markets: List[Market],
    *,
    min_edge_per_set: float,
    fee_bps: float = 0.0,
    gas_cost_per_set: float = 0.0,
    max_size: Optional[float] = None,
) -> List[ArbOpportunity]:
    """Run :func:`find_buy_arb` across many markets, best edge first."""
    opps = []
    for m in markets:
        opp = find_buy_arb(
            m,
            min_edge_per_set=min_edge_per_set,
            fee_bps=fee_bps,
            gas_cost_per_set=gas_cost_per_set,
            max_size=max_size,
        )
        if opp is not None:
            opps.append(opp)
    opps.sort(key=lambda o: o.total_edge, reverse=True)
    return opps
