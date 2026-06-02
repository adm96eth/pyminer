"""Tests for the paper broker fills and the risk manager gate."""

from polytrader.brokers.base import Side
from polytrader.brokers.paper import PaperBroker
from polytrader.models import ArbOpportunity, Market, OrderBook, OrderLevel, Token
from polytrader.risk import RiskLimits, RiskManager


def _book(asks=None, bids=None, tid="t"):
    return OrderBook(
        tid,
        asks=[OrderLevel(p, s) for p, s in (asks or [])],
        bids=[OrderLevel(p, s) for p, s in (bids or [])],
    )


# -- paper broker ------------------------------------------------------------

def test_paper_buy_walks_book_and_debits_usdc():
    pb = PaperBroker(starting_usdc=100.0)
    pb.set_books({"t": _book(asks=[(0.40, 10), (0.50, 10)])})
    fill = pb.buy("t", 15, max_price=0.99)
    assert fill.ok and fill.fully_filled
    assert abs(fill.cost - -(10 * 0.40 + 5 * 0.50)) < 1e-9
    assert abs(pb.usdc_balance() - (100.0 - 6.5)) < 1e-9
    assert pb.position("t") == 15


def test_paper_buy_respects_price_cap():
    pb = PaperBroker(starting_usdc=100.0)
    pb.set_books({"t": _book(asks=[(0.40, 10), (0.60, 10)])})
    fill = pb.buy("t", 20, max_price=0.45)  # second level too expensive
    assert fill.filled_size == 10
    assert not fill.fully_filled


def test_paper_buy_insufficient_usdc():
    pb = PaperBroker(starting_usdc=1.0)
    pb.set_books({"t": _book(asks=[(0.40, 10)])})
    fill = pb.buy("t", 10, max_price=0.99)
    assert not fill.ok and "insufficient" in fill.detail


def test_paper_sell_limited_to_position():
    pb = PaperBroker(starting_usdc=100.0)
    pb.set_books({"t": _book(asks=[(0.40, 10)], bids=[(0.55, 10)])})
    pb.buy("t", 10, max_price=0.99)
    fill = pb.sell("t", 50, min_price=0.0)  # only hold 10
    assert fill.filled_size == 10
    assert pb.position("t") == 0


def test_redeem_set_credits_one_dollar_each_and_realizes_profit():
    pb = PaperBroker(starting_usdc=100.0)
    pb.set_books({
        "YES": _book(asks=[(0.45, 10)]),
        "NO": _book(asks=[(0.45, 10)]),
    })
    pb.buy("YES", 10, max_price=0.99)
    pb.buy("NO", 10, max_price=0.99)
    spent = 100.0 - pb.usdc_balance()
    assert abs(spent - 9.0) < 1e-9
    credited = pb.redeem_set(["YES", "NO"], 10)
    assert credited == 10
    assert pb.position("YES") == 0 and pb.position("NO") == 0
    # net profit = $10 redeemed - $9 spent = $1
    assert abs(pb.usdc_balance() - 101.0) < 1e-9


def test_redeem_set_requires_full_legs():
    pb = PaperBroker(starting_usdc=100.0)
    pb.set_books({"YES": _book(asks=[(0.45, 10)]), "NO": _book(asks=[(0.45, 5)])})
    pb.buy("YES", 10, max_price=0.99)
    pb.buy("NO", 5, max_price=0.99)
    try:
        pb.redeem_set(["YES", "NO"], 10)
        assert False, "expected ValueError"
    except ValueError:
        pass


# -- risk manager ------------------------------------------------------------

def _opp(size=100, set_cost=0.90, edge=0.10, cid="c"):
    m = Market(cid, "q", tokens=[Token("YES", "Yes"), Token("NO", "No")])
    return ArbOpportunity(
        market=m, size=size, set_cost=set_cost, total_cost=set_cost * size,
        costs_per_set=0.0, edge_per_set=edge, total_edge=edge * size,
    )


def test_risk_rejects_low_edge():
    rm = RiskManager(RiskLimits(min_edge_per_set=0.02))
    ok, reason, _ = rm.approve(_opp(edge=0.01))
    assert not ok and "below min" in reason


def test_risk_rejects_thin_liquidity():
    rm = RiskManager(RiskLimits(min_set_liquidity=10))
    ok, reason, _ = rm.approve(_opp(size=3))
    assert not ok and "liquidity" in reason


def test_risk_downsizes_to_per_trade_cap():
    # set_cost 0.90, cap $50 -> ~55.5 sets
    rm = RiskManager(RiskLimits(max_usdc_per_trade=50, min_set_liquidity=1,
                                max_usdc_per_market=1e9, max_total_exposure=1e9))
    ok, _, size = rm.approve(_opp(size=100, set_cost=0.90))
    assert ok
    assert abs(size - (50 / 0.90)) < 1e-3


def test_risk_respects_market_exposure_already_used():
    rm = RiskManager(RiskLimits(max_usdc_per_trade=1e9, max_usdc_per_market=100,
                                max_total_exposure=1e9, min_set_liquidity=1))
    rm.register_fill("c", 90.0)  # only $10 room left in market c
    ok, _, size = rm.approve(_opp(size=1000, set_cost=0.50, cid="c"))
    assert ok
    assert abs(size - (10 / 0.50)) < 1e-3  # 20 sets


def test_kill_switch_trips_on_daily_loss():
    rm = RiskManager(RiskLimits(max_daily_loss=20))
    rm.record_pnl(-25.0)
    assert rm.is_halted
    ok, reason, _ = rm.approve(_opp())
    assert not ok and "HALTED" in reason


def test_release_reduces_exposure():
    rm = RiskManager()
    rm.register_fill("c", 50.0)
    assert rm.state.total_exposure == 50.0
    rm.release("c", 50.0)
    assert rm.state.total_exposure == 0.0
    assert rm.state.exposure_by_market["c"] == 0.0
