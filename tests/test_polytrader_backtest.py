"""Tests for snapshot round-tripping and the backtest harness."""

from polytrader.backtest import Backtester
from polytrader.config import Config
from polytrader.models import Market, OrderBook, OrderLevel, Token
from polytrader.risk import RiskLimits
from polytrader.snapshot import (
    SnapshotRecorder,
    market_from_obj,
    market_to_obj,
    read_frames,
)


def _arb_market(cid="c", price=0.45, depth=100):
    yes = Token("YES", "Yes", OrderBook("YES", asks=[OrderLevel(price, depth)],
                                        bids=[OrderLevel(price - 0.01, depth)]))
    no = Token("NO", "No", OrderBook("NO", asks=[OrderLevel(price, depth)],
                                     bids=[OrderLevel(price - 0.01, depth)]))
    return Market(cid, "arb?", tokens=[yes, no])


# -- snapshot serialization --------------------------------------------------

def test_market_round_trips_through_obj():
    m = _arb_market()
    m2 = market_from_obj(market_to_obj(m))
    assert m2.condition_id == m.condition_id
    assert [t.token_id for t in m2.tokens] == ["YES", "NO"]
    assert m2.tokens[0].book.asks[0].price == 0.45
    assert m2.tokens[0].book.bids[0].size == 100


def test_recorder_writes_readable_frames(tmp_path):
    path = tmp_path / "snap.jsonl"
    rec = SnapshotRecorder(str(path))
    rec.record([_arb_market(cid="a")], ts=1.0)
    rec.record([_arb_market(cid="b")], ts=2.0)

    frames = list(read_frames(str(path)))
    assert len(frames) == 2
    assert frames[0]["ts"] == 1.0
    assert frames[1]["markets"][0].condition_id == "b"


# -- backtester --------------------------------------------------------------

def _write_snapshot(path, markets_per_frame):
    rec = SnapshotRecorder(str(path))
    for i, markets in enumerate(markets_per_frame):
        rec.record(markets, ts=float(i))


def test_backtest_profits_on_arb_frames(tmp_path):
    path = tmp_path / "snap.jsonl"
    _write_snapshot(path, [[_arb_market()], [_arb_market()], [_arb_market()]])

    cfg = Config(mode="paper", paper_starting_usdc=1000.0)
    cfg.limits = RiskLimits(min_edge_per_set=0.005, min_set_liquidity=1,
                            max_usdc_per_trade=50, max_usdc_per_market=1000,
                            max_total_exposure=1e9, max_daily_loss=1e9)
    res = Backtester(cfg).run(str(path))

    assert res.frames == 3
    assert res.trades == 3
    assert res.realized_pnl > 0
    assert res.ending_usdc > 1000.0
    assert res.return_pct > 0
    assert "c" in res.per_market_edge


def test_backtest_no_trades_without_edge(tmp_path):
    path = tmp_path / "snap.jsonl"
    _write_snapshot(path, [[_arb_market(price=0.55)]])  # set cost 1.10

    cfg = Config(mode="paper")
    cfg.limits = RiskLimits(min_edge_per_set=0.005, min_set_liquidity=1)
    res = Backtester(cfg).run(str(path))
    assert res.trades == 0
    assert res.realized_pnl == 0.0


def test_backtest_summary_is_descriptive(tmp_path):
    path = tmp_path / "snap.jsonl"
    _write_snapshot(path, [[_arb_market()]])
    cfg = Config(mode="paper")
    cfg.limits = RiskLimits(min_edge_per_set=0.005, min_set_liquidity=1,
                            max_usdc_per_trade=50, max_total_exposure=1e9)
    res = Backtester(cfg).run(str(path))
    s = res.summary()
    assert "frames=1" in s and "trades=" in s and "%" in s
