"""Dynamic position sizing -- including "press your winners" escalation.

Three modes, all bounded by your hard ``max_usdc_per_trade`` ceiling so nothing
here can size past the risk limits:

  fixed     always the base size (no escalation). The safe default.
  compound  per-trade size = a fraction of *current equity*. As the bankroll
            grows from realized profit, absolute size grows with it (Kelly-ish
            geometric compounding) -- and shrinks if you give some back.
  streak    anti-Martingale / Paroli: multiply the base size by
            ``win_multiplier`` for each *consecutive win*, capped at
            ``max_multiplier``; reset to base on any losing trade. This is the
            "when we win we risk more" behavior -- it only ever escalates with
            house money, never doubles down into a loss.

A ``profit_target_multiple`` (e.g. 2.0) lets the engine stop once equity has,
say, doubled -- "double the money then walk away."

IMPORTANT: escalation assumes each arb trade is near-riskless. It is not
perfectly so (slippage, partial fills, odd resolutions). Scaling size up
magnifies those tail losses too, which is exactly why every mode is clamped to
the hard per-trade ceiling and the daily-loss kill switch always wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FIXED = "fixed"
COMPOUND = "compound"
STREAK = "streak"


@dataclass
class SizingConfig:
    mode: str = FIXED
    base_usdc_per_trade: float = 10.0      # starting per-trade notional
    ceiling_usdc_per_trade: float = 50.0   # hard cap; size never exceeds this
    # streak (anti-Martingale)
    win_multiplier: float = 1.5            # size *= this per consecutive win
    max_multiplier: float = 8.0            # cap on the streak multiplier
    reset_on_loss: bool = True             # losing trade -> back to base
    # compound
    compound_fraction: float = 0.10        # per-trade size = fraction * equity
    # goal
    profit_target_multiple: Optional[float] = None  # e.g. 2.0 -> stop at 2x equity

    def __post_init__(self):
        if self.mode not in (FIXED, COMPOUND, STREAK):
            raise ValueError(f"unknown sizing mode {self.mode!r}")
        if self.win_multiplier < 1.0:
            raise ValueError("win_multiplier must be >= 1.0 (use 1.0 for no escalation)")


class Sizer:
    """Tracks equity + win streak and reports the per-trade notional cap."""

    def __init__(self, cfg: SizingConfig, starting_equity: float):
        self.cfg = cfg
        self.starting_equity = starting_equity
        self.equity = starting_equity
        self.win_streak = 0
        self.loss_streak = 0

    # -- feedback from realized trades --------------------------------------
    def on_pnl(self, delta: float) -> None:
        self.equity += delta
        if delta >= 0:
            self.win_streak += 1
            self.loss_streak = 0
        else:
            self.loss_streak += 1
            if self.cfg.reset_on_loss:
                self.win_streak = 0

    # -- the number the risk gate uses --------------------------------------
    @property
    def multiplier(self) -> float:
        """Current streak multiplier (1.0 unless escalating on a win streak)."""
        if self.cfg.mode != STREAK or self.win_streak <= 0:
            return 1.0
        m = self.cfg.win_multiplier ** self.win_streak
        return min(m, self.cfg.max_multiplier)

    def per_trade_cap(self) -> float:
        if self.cfg.mode == COMPOUND:
            raw = self.cfg.compound_fraction * max(0.0, self.equity)
        elif self.cfg.mode == STREAK:
            raw = self.cfg.base_usdc_per_trade * self.multiplier
        else:  # FIXED
            raw = self.cfg.base_usdc_per_trade
        # never exceed the hard ceiling
        return min(raw, self.cfg.ceiling_usdc_per_trade)

    # -- goal ---------------------------------------------------------------
    def target_reached(self) -> bool:
        if not self.cfg.profit_target_multiple:
            return False
        return self.equity >= self.starting_equity * self.cfg.profit_target_multiple

    def describe(self) -> str:
        return (
            f"sizer[{self.cfg.mode}] equity=${self.equity:.2f} "
            f"streak={self.win_streak}W x{self.multiplier:.2f} "
            f"-> cap=${self.per_trade_cap():.2f}"
        )
