"""Replay recorded snapshot frames through the strategy + paper broker.

This reuses the *exact* production code paths -- ``strategy.scan``, the
``RiskManager`` gate, the ``PaperBroker`` fills and set merges -- so a backtest
measures the real bot, not a parallel reimplementation. Each frame is treated
as one engine tick: fill against that frame's books, merge complete sets, book
the P&L.

Caveat: replaying your own recorded books assumes the liquidity you saw would
still have been there for you. Real execution competes for it. Backtest P&L is
therefore an optimistic upper bound, same as paper mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import strategy
from .brokers.paper import PaperBroker
from .config import Config
from .risk import RiskManager
from .snapshot import read_frames


@dataclass
class BacktestResult:
    frames: int = 0
    opportunities: int = 0
    trades: int = 0
    skipped: int = 0
    starting_usdc: float = 0.0
    ending_usdc: float = 0.0
    realized_pnl: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    per_market_edge: dict = field(default_factory=dict)

    @property
    def return_pct(self) -> float:
        if self.starting_usdc <= 0:
            return 0.0
        return (self.ending_usdc - self.starting_usdc) / self.starting_usdc * 100.0

    def summary(self) -> str:
        return (
            f"frames={self.frames} opps={self.opportunities} trades={self.trades} "
            f"skipped={self.skipped} | start=${self.starting_usdc:.2f} "
            f"end=${self.ending_usdc:.2f} realizedPnL=${self.realized_pnl:.2f} "
            f"({self.return_pct:+.2f}%)"
            + (f" HALTED: {self.halt_reason}" if self.halted else "")
        )


class Backtester:
    def __init__(self, config: Config, risk: Optional[RiskManager] = None):
        self.config = config
        self.risk = risk or RiskManager(config.limits)
        self.broker = PaperBroker(config.paper_starting_usdc)

    def run(self, snapshot_path: str) -> BacktestResult:
        res = BacktestResult(starting_usdc=self.broker.usdc_balance())

        for frame in read_frames(snapshot_path):
            res.frames += 1
            if self.risk.is_halted:
                break

            markets = frame["markets"]
            books = {t.token_id: t.book for m in markets for t in m.tokens if t.book}
            self.broker.set_books(books)

            opps = strategy.scan(
                markets,
                min_edge_per_set=self.risk.limits.min_edge_per_set,
                fee_bps=self.config.fee_bps,
                gas_cost_per_set=self.config.gas_cost_per_set,
                max_size=self.risk.limits.max_usdc_per_trade,
            )
            res.opportunities += len(opps)

            for opp in opps:
                if self.risk.is_halted:
                    break
                approved, _reason, size = self.risk.approve(opp)
                if not approved:
                    res.skipped += 1
                    continue
                if self._fill_and_merge(opp, size, res):
                    res.trades += 1

        res.ending_usdc = self.broker.usdc_balance()
        res.realized_pnl = self.risk.state.realized_pnl
        res.halted = self.risk.is_halted
        res.halt_reason = self.risk.state.halt_reason
        return res

    def _fill_and_merge(self, opp, size, res: BacktestResult) -> bool:
        fills = []
        for t in opp.market.tokens:
            cap = min(0.999, (t.book.best_ask or 0.999) + 0.01)
            fill = self.broker.buy(t.token_id, size, max_price=cap)
            if not fill.ok or fill.filled_size + 1e-9 < size:
                # unwind partials
                if fill.ok and fill.filled_size > 0:
                    fills.append(fill)
                for f in fills:
                    self.broker.sell(f.token_id, f.filled_size, min_price=0.0)
                return False
            fills.append(fill)

        spent = -sum(f.cost for f in fills)
        credited = self.broker.redeem_set([t.token_id for t in opp.market.tokens], size)
        pnl = credited - spent
        self.risk.record_pnl(pnl)
        res.per_market_edge[opp.market.condition_id] = (
            res.per_market_edge.get(opp.market.condition_id, 0.0) + pnl
        )
        return True
