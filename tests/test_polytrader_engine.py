"""End-to-end engine test with a fake data source -- no network."""

from polytrader.config import Config
from polytrader.engine import Engine
from polytrader.models import Market, OrderBook, OrderLevel, Token
from polytrader.brokers.paper import PaperBroker
from polytrader.risk import RiskLimits, RiskManager


class FakeData:
    """Stands in for PolymarketData; serves a fixed market snapshot."""

    def __init__(self, markets):
        self._markets = markets

    def fetch_active_markets(self, limit=50):
        return self._markets

    def hydrate_books(self, markets):
        return {t.token_id: t.book for m in markets for t in m.tokens if t.book}


def _arb_market(cid="c", price=0.45, depth=100):
    yes = Token("YES", "Yes", OrderBook("YES", asks=[OrderLevel(price, depth)], bids=[OrderLevel(price - 0.01, depth)]))
    no = Token("NO", "No", OrderBook("NO", asks=[OrderLevel(price, depth)], bids=[OrderLevel(price - 0.01, depth)]))
    return Market(cid, "arb?", tokens=[yes, no])


def test_engine_executes_arb_and_realizes_profit_in_paper():
    cfg = Config(mode="paper", paper_starting_usdc=1000.0)
    cfg.limits = RiskLimits(min_edge_per_set=0.005, min_set_liquidity=1,
                            max_usdc_per_trade=50, max_usdc_per_market=1000,
                            max_total_exposure=1000, max_daily_loss=100)
    broker = PaperBroker(cfg.paper_starting_usdc)
    risk = RiskManager(cfg.limits)
    engine = Engine(cfg, broker, risk, data=FakeData([_arb_market()]))

    executed = engine.tick()

    assert len(executed) == 1
    # bought ~55 sets at 0.90, merged for $1 each -> ~10% edge realized
    assert risk.state.realized_pnl > 0
    assert broker.usdc_balance() > 1000.0


def test_engine_does_nothing_without_edge():
    cfg = Config(mode="paper")
    cfg.limits = RiskLimits(min_edge_per_set=0.005, min_set_liquidity=1)
    broker = PaperBroker(1000.0)
    risk = RiskManager(cfg.limits)
    # set cost 0.51+0.51 = 1.02 -> no edge
    engine = Engine(cfg, broker, risk, data=FakeData([_arb_market(price=0.51)]))

    executed = engine.tick()
    assert executed == []
    assert broker.usdc_balance() == 1000.0


def test_engine_halts_stop_trading():
    cfg = Config(mode="paper")
    cfg.limits = RiskLimits(min_edge_per_set=0.005, min_set_liquidity=1)
    broker = PaperBroker(1000.0)
    risk = RiskManager(cfg.limits)
    risk.halt("manual")
    engine = Engine(cfg, broker, risk, data=FakeData([_arb_market()]))
    assert engine.tick() == []
