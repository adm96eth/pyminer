"""Tests for dynamic position sizing and its risk-manager integration."""

import pytest

from polytrader.models import ArbOpportunity, Market, Token
from polytrader.risk import RiskLimits, RiskManager
from polytrader.sizing import COMPOUND, FIXED, STREAK, Sizer, SizingConfig


def _opp(cid="c", size=1000, set_cost=0.90, edge=0.10):
    m = Market(cid, "q", tokens=[Token("YES", "Yes"), Token("NO", "No")])
    return ArbOpportunity(m, size, set_cost, set_cost * size, 0.0, edge, edge * size)


# -- Sizer unit behavior -----------------------------------------------------

def test_fixed_mode_is_constant():
    s = Sizer(SizingConfig(mode=FIXED, base_usdc_per_trade=10, ceiling_usdc_per_trade=50), 1000)
    assert s.per_trade_cap() == 10
    s.on_pnl(5.0)
    s.on_pnl(5.0)
    assert s.per_trade_cap() == 10  # wins don't change fixed sizing


def test_streak_escalates_on_consecutive_wins():
    s = Sizer(SizingConfig(mode=STREAK, base_usdc_per_trade=10, win_multiplier=2.0,
                           max_multiplier=8.0, ceiling_usdc_per_trade=1000), 1000)
    assert s.per_trade_cap() == 10            # streak 0 -> base
    s.on_pnl(1.0); assert s.per_trade_cap() == 20   # 1 win -> x2
    s.on_pnl(1.0); assert s.per_trade_cap() == 40   # 2 wins -> x4
    s.on_pnl(1.0); assert s.per_trade_cap() == 80   # 3 wins -> x8


def test_streak_capped_by_max_multiplier():
    s = Sizer(SizingConfig(mode=STREAK, base_usdc_per_trade=10, win_multiplier=2.0,
                           max_multiplier=4.0, ceiling_usdc_per_trade=1000), 1000)
    for _ in range(5):
        s.on_pnl(1.0)
    assert s.multiplier == 4.0          # not 32
    assert s.per_trade_cap() == 40


def test_streak_resets_on_loss():
    s = Sizer(SizingConfig(mode=STREAK, base_usdc_per_trade=10, win_multiplier=2.0,
                           ceiling_usdc_per_trade=1000), 1000)
    s.on_pnl(1.0); s.on_pnl(1.0)
    assert s.per_trade_cap() == 40
    s.on_pnl(-1.0)                      # a loss wipes the streak
    assert s.win_streak == 0
    assert s.per_trade_cap() == 10


def test_streak_never_exceeds_ceiling():
    s = Sizer(SizingConfig(mode=STREAK, base_usdc_per_trade=10, win_multiplier=2.0,
                           max_multiplier=100, ceiling_usdc_per_trade=35), 1000)
    for _ in range(5):
        s.on_pnl(1.0)
    assert s.per_trade_cap() == 35      # clamped to hard ceiling


def test_compound_scales_with_equity():
    s = Sizer(SizingConfig(mode=COMPOUND, compound_fraction=0.10,
                           ceiling_usdc_per_trade=1e9), 1000)
    assert s.per_trade_cap() == 100     # 10% of 1000
    s.on_pnl(1000.0)                    # equity now 2000
    assert s.per_trade_cap() == 200     # 10% of 2000
    s.on_pnl(-500.0)                    # equity 1500
    assert s.per_trade_cap() == 150


def test_profit_target_reached():
    s = Sizer(SizingConfig(mode=FIXED, profit_target_multiple=2.0), 100)
    assert not s.target_reached()
    s.on_pnl(99.0)
    assert not s.target_reached()
    s.on_pnl(1.0)                       # equity 200 == 2x
    assert s.target_reached()


def test_invalid_mode_and_multiplier():
    with pytest.raises(ValueError):
        SizingConfig(mode="martingale")
    with pytest.raises(ValueError):
        SizingConfig(win_multiplier=0.5)


# -- integration with RiskManager -------------------------------------------

def test_risk_uses_sizer_cap_then_escalates():
    sizer = Sizer(SizingConfig(mode=STREAK, base_usdc_per_trade=9.0, win_multiplier=2.0,
                               ceiling_usdc_per_trade=100), 1000)
    rm = RiskManager(RiskLimits(max_usdc_per_trade=100, max_usdc_per_market=1e9,
                                max_total_exposure=1e9, min_set_liquidity=1,
                                min_edge_per_set=0.001, max_daily_loss=1e9),
                     sizer=sizer)
    # set_cost 0.90, base cap $9 -> 10 sets
    ok, _, size = rm.approve(_opp(set_cost=0.90))
    assert ok and abs(size - 10) < 1e-6

    rm.record_pnl(1.0)  # 1 win -> cap $18 -> 20 sets
    ok, _, size = rm.approve(_opp(set_cost=0.90))
    assert abs(size - 20) < 1e-6


def test_risk_sizer_never_breaches_hard_ceiling():
    sizer = Sizer(SizingConfig(mode=STREAK, base_usdc_per_trade=10, win_multiplier=10,
                               max_multiplier=1e9, ceiling_usdc_per_trade=1e9), 1000)
    rm = RiskManager(RiskLimits(max_usdc_per_trade=45, max_usdc_per_market=1e9,
                                max_total_exposure=1e9, min_set_liquidity=1,
                                min_edge_per_set=0.001, max_daily_loss=1e9),
                     sizer=sizer)
    for _ in range(5):
        rm.record_pnl(1.0)              # sizer wants huge, limit says $45
    ok, _, size = rm.approve(_opp(set_cost=0.90))
    # hard limit $45 / 0.90 = 50 sets, regardless of streak
    assert ok and abs(size - 50) < 1e-6


def test_risk_halts_on_profit_target():
    sizer = Sizer(SizingConfig(mode=FIXED, profit_target_multiple=2.0), 100)
    rm = RiskManager(RiskLimits(max_daily_loss=1e9), sizer=sizer)
    rm.record_pnl(100.0)                # equity doubled
    assert rm.is_halted
    assert "profit target" in rm.state.halt_reason
