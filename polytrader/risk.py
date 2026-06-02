"""Pre-trade risk checks and the kill switch.

Every opportunity must pass *all* of these before any order is placed, in
paper or live mode. The defaults are intentionally conservative; loosen them
deliberately, not by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .models import ArbOpportunity


@dataclass
class RiskLimits:
    max_usdc_per_trade: float = 50.0       # cap notional per single arb fill
    max_usdc_per_market: float = 100.0     # cap cumulative exposure to one market
    max_total_exposure: float = 500.0      # cap cumulative exposure across all
    min_set_liquidity: float = 5.0         # require >= N complete sets of depth
    min_edge_per_set: float = 0.005        # ignore < 0.5c/set after costs
    max_daily_loss: float = 50.0           # kill switch trips past this realized loss
    require_manual_confirm_live: bool = True  # live orders need explicit ack


@dataclass
class RiskState:
    """Mutable running state the limits are checked against."""

    exposure_by_market: Dict[str, float] = field(default_factory=dict)
    total_exposure: float = 0.0
    realized_pnl: float = 0.0
    halted: bool = False
    halt_reason: str = ""


class RiskManager:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.state = RiskState()

    # -- kill switch ---------------------------------------------------------
    def record_pnl(self, delta: float) -> None:
        self.state.realized_pnl += delta
        if -self.state.realized_pnl >= self.limits.max_daily_loss:
            self.halt(
                f"daily loss limit hit: realized PnL ${self.state.realized_pnl:.2f} "
                f"<= -${self.limits.max_daily_loss:.2f}"
            )

    def halt(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason

    @property
    def is_halted(self) -> bool:
        return self.state.halted

    # -- exposure accounting -------------------------------------------------
    def register_fill(self, market_id: str, notional: float) -> None:
        self.state.exposure_by_market[market_id] = (
            self.state.exposure_by_market.get(market_id, 0.0) + notional
        )
        self.state.total_exposure += notional

    def release(self, market_id: str, notional: float) -> None:
        """Reduce exposure when a set is merged/redeemed back to collateral."""
        cur = self.state.exposure_by_market.get(market_id, 0.0)
        self.state.exposure_by_market[market_id] = max(0.0, cur - notional)
        self.state.total_exposure = max(0.0, self.state.total_exposure - notional)

    # -- the gate ------------------------------------------------------------
    def approve(self, opp: ArbOpportunity) -> Tuple[bool, str, float]:
        """Approve (possibly down-sized) or reject an opportunity.

        Returns ``(approved, reason, approved_size)``. ``approved_size`` may be
        smaller than ``opp.size`` when a notional cap binds; if it's driven to
        zero the trade is rejected.
        """
        if self.is_halted:
            return False, f"HALTED: {self.state.halt_reason}", 0.0

        if opp.edge_per_set < self.limits.min_edge_per_set:
            return False, (
                f"edge ${opp.edge_per_set:.4f}/set below min "
                f"${self.limits.min_edge_per_set:.4f}"
            ), 0.0

        if opp.size < self.limits.min_set_liquidity:
            return False, (
                f"size {opp.size:.2f} sets below min liquidity "
                f"{self.limits.min_set_liquidity:.2f}"
            ), 0.0

        # Notional caps. We approximate per-set notional with the depth-aware
        # average set cost; the actual fill can only be cheaper at the margin.
        per_set = opp.set_cost
        mid = opp.market.condition_id

        # cap: per trade
        size = opp.size
        max_by_trade = self.limits.max_usdc_per_trade / per_set
        # cap: per market (account for what we already hold)
        used_mkt = self.state.exposure_by_market.get(mid, 0.0)
        room_mkt = max(0.0, self.limits.max_usdc_per_market - used_mkt)
        max_by_market = room_mkt / per_set
        # cap: total
        room_total = max(0.0, self.limits.max_total_exposure - self.state.total_exposure)
        max_by_total = room_total / per_set

        approved_size = min(size, max_by_trade, max_by_market, max_by_total)
        approved_size = round(approved_size, 6)

        if approved_size < self.limits.min_set_liquidity:
            return False, (
                "notional caps leave room for only "
                f"{approved_size:.2f} sets (< min {self.limits.min_set_liquidity:.2f}); "
                f"market_used=${used_mkt:.2f} total=${self.state.total_exposure:.2f}"
            ), 0.0

        return True, "ok", approved_size
