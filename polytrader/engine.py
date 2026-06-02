"""The trading loop: fetch -> detect -> risk-check -> execute -> account.

Both paper and live run through the exact same path; only the broker differs.
Execution is atomic-ish per opportunity: we buy every leg of the set, and if
any leg fails to fully fill we unwind the legs we did get rather than sit on an
incomplete (and therefore unhedged) set.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from . import strategy
from .brokers.base import Broker
from .brokers.paper import PaperBroker
from .client import PolymarketData
from .config import Config, Secrets
from .models import ArbOpportunity, Market
from .risk import RiskManager

log = logging.getLogger("polytrader")


class Engine:
    def __init__(
        self,
        config: Config,
        broker: Broker,
        risk: RiskManager,
        data: Optional[PolymarketData] = None,
    ):
        self.config = config
        self.broker = broker
        self.risk = risk
        self.data = data or PolymarketData(config.clob_host, config.gamma_host)

    # -- one full pass -------------------------------------------------------
    def tick(self) -> List[ArbOpportunity]:
        if self.risk.is_halted:
            log.warning("halted: %s", self.risk.state.halt_reason)
            return []

        markets = self.data.fetch_active_markets(self.config.market_limit)
        books = self.data.hydrate_books(markets)

        # paper broker needs the latest snapshot to fill against
        if isinstance(self.broker, PaperBroker):
            self.broker.set_books(books)

        opps = strategy.scan(
            markets,
            min_edge_per_set=self.risk.limits.min_edge_per_set,
            fee_bps=self.config.fee_bps,
            gas_cost_per_set=self.config.gas_cost_per_set,
            max_size=self.risk.limits.max_usdc_per_trade,  # coarse cap; risk refines
        )

        executed = []
        for opp in opps:
            if self.risk.is_halted:
                break
            if self._execute(opp):
                executed.append(opp)
        return executed

    # -- execute one opportunity --------------------------------------------
    def _execute(self, opp: ArbOpportunity) -> bool:
        approved, reason, size = self.risk.approve(opp)
        if not approved:
            log.debug("skip %s: %s", opp.market.condition_id, reason)
            return False

        log.info("EXECUTE %s | approved_size=%.2f", opp.describe(), size)

        if self.config.is_live and self.config.dry_run_live:
            log.info("DRY-RUN (live mode, dry_run_live=True): not sending orders")
            # still walk the legs so the dry-run logs each intended order
        fills = []
        for t in opp.market.tokens:
            cap = t.book.best_ask if t.book else None
            if cap is None:
                self._unwind(fills)
                return False
            # allow crossing a few ticks into the book to fill the full size
            cap = min(0.999, cap + 0.01)
            fill = self.broker.buy(t.token_id, size, max_price=cap)
            if not fill.ok or fill.filled_size + 1e-9 < size:
                log.warning("leg failed (%s): %s -- unwinding", t.outcome, fill.detail)
                if fill.ok and fill.filled_size > 0:
                    fills.append(fill)
                self._unwind(fills)
                return False
            fills.append(fill)

        spent = -sum(f.cost for f in fills)
        self.risk.register_fill(opp.market.condition_id, spent)

        # Realize the arb: a complete set merges back to $1 each.
        if isinstance(self.broker, PaperBroker) and not self.config.is_live:
            token_ids = [t.token_id for t in opp.market.tokens]
            try:
                credited = self.broker.redeem_set(token_ids, size, opp.market.condition_id)
                self.risk.release(opp.market.condition_id, spent)
                pnl = credited - spent
                self.risk.record_pnl(pnl)
                log.info("MERGED %d legs: +$%.2f collateral, realized PnL $%.2f",
                         len(token_ids), credited, pnl)
            except ValueError as e:
                log.error("merge failed: %s", e)
        else:
            # Live: the set is held; merge/redeem is an on-chain action you run
            # separately (or at resolution). We leave exposure registered.
            log.info("set acquired live: spent $%.2f, hold until merge/resolution", spent)
        return True

    def _unwind(self, fills) -> None:
        """Sell back any legs we filled when the set couldn't be completed."""
        for f in fills:
            if f.filled_size > 0:
                r = self.broker.sell(f.token_id, f.filled_size, min_price=0.0)
                log.info("unwind %s: %s", f.token_id, r.detail)

    # -- run forever ---------------------------------------------------------
    def run(self, max_ticks: Optional[int] = None) -> None:
        n = 0
        log.info("starting engine in %s mode (broker=%s)", self.config.mode, self.broker.name)
        while True:
            try:
                self.tick()
            except Exception as e:  # keep the loop alive; log loudly
                log.exception("tick error: %s", e)
            n += 1
            if max_ticks is not None and n >= max_ticks:
                break
            if self.risk.is_halted:
                log.error("ENGINE HALTED: %s", self.risk.state.halt_reason)
                break
            time.sleep(self.config.poll_interval_s)
