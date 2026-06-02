"""Tests for the arb-detection math and order-book walking."""

from polytrader.models import Market, OrderBook, OrderLevel, Token
from polytrader.strategy import cost_to_buy_sets, find_buy_arb, scan


def _market(yes_asks, no_asks, qid="q", cid="c"):
    """Binary market with given ask ladders [(price, size), ...]."""
    yes = Token("YES_TID", "Yes", OrderBook("YES_TID", asks=[OrderLevel(p, s) for p, s in yes_asks]))
    no = Token("NO_TID", "No", OrderBook("NO_TID", asks=[OrderLevel(p, s) for p, s in no_asks]))
    return Market(condition_id=cid, question=qid, tokens=[yes, no])


# -- order book walking ------------------------------------------------------

def test_cost_to_buy_walks_levels():
    b = OrderBook("t", asks=[OrderLevel(0.40, 10), OrderLevel(0.45, 10)])
    assert b.cost_to_buy(10) == 0.40 * 10
    # 15 = 10@0.40 + 5@0.45
    assert abs(b.cost_to_buy(15) - (10 * 0.40 + 5 * 0.45)) < 1e-9


def test_cost_to_buy_insufficient_depth_returns_none():
    b = OrderBook("t", asks=[OrderLevel(0.40, 10)])
    assert b.cost_to_buy(20) is None


def test_proceeds_from_sell_walks_bids():
    b = OrderBook("t", bids=[OrderLevel(0.60, 10), OrderLevel(0.55, 10)])
    assert abs(b.proceeds_from_sell(15) - (10 * 0.60 + 5 * 0.55)) < 1e-9


# -- arb detection -----------------------------------------------------------

def test_clear_arb_detected():
    # YES 0.45 + NO 0.45 = 0.90 -> 10c edge per set
    m = _market([(0.45, 100)], [(0.45, 100)])
    opp = find_buy_arb(m, min_edge_per_set=0.005)
    assert opp is not None
    assert abs(opp.set_cost - 0.90) < 1e-9
    assert abs(opp.edge_per_set - 0.10) < 1e-9
    assert opp.size == 100  # min depth across legs
    assert abs(opp.total_edge - 10.0) < 1e-9


def test_no_arb_when_set_costs_at_least_one():
    # 0.55 + 0.50 = 1.05 -> no edge
    m = _market([(0.55, 100)], [(0.50, 100)])
    assert find_buy_arb(m, min_edge_per_set=0.005) is None


def test_edge_just_below_threshold_rejected():
    # 0.498 + 0.498 = 0.996 -> 0.4c edge, below 0.5c threshold
    m = _market([(0.498, 100)], [(0.498, 100)])
    assert find_buy_arb(m, min_edge_per_set=0.005) is None


def test_fees_and_gas_erode_edge():
    # 0.97 set cost -> 3c gross. 100bps fee (1c) + 1c gas = 2c cost -> 1c net.
    m = _market([(0.485, 100)], [(0.485, 100)])
    opp = find_buy_arb(m, min_edge_per_set=0.005, fee_bps=100, gas_cost_per_set=0.01)
    assert opp is not None
    assert abs(opp.costs_per_set - 0.02) < 1e-9
    assert abs(opp.edge_per_set - 0.01) < 1e-9


def test_fees_can_kill_a_marginal_edge():
    m = _market([(0.495, 100)], [(0.495, 100)])  # 1c gross
    # 50bps + 1c gas = 1.5c cost > 1c gross -> negative, rejected
    assert find_buy_arb(m, min_edge_per_set=0.005, fee_bps=50, gas_cost_per_set=0.01) is None


def test_size_bounded_by_thinnest_leg():
    m = _market([(0.45, 100)], [(0.45, 7)])  # NO only has depth 7
    opp = find_buy_arb(m, min_edge_per_set=0.005)
    assert opp is not None
    assert opp.size == 7


def test_depth_average_edge_includes_worse_levels():
    # YES: 5@0.40 then 95@0.50 ; NO: 0.45 flat.
    # Buying 100 sets averages YES up; edge should reflect the blended cost.
    m = _market([(0.40, 5), (0.50, 95)], [(0.45, 100)])
    opp = find_buy_arb(m, min_edge_per_set=0.005)
    assert opp is not None
    # blended YES avg = (5*0.40 + 95*0.50)/100 = 0.495 ; +0.45 = 0.945
    assert abs(opp.set_cost - 0.945) < 1e-6


def test_categorical_three_outcomes():
    a = Token("A", "A", OrderBook("A", asks=[OrderLevel(0.30, 50)]))
    b = Token("B", "B", OrderBook("B", asks=[OrderLevel(0.30, 50)]))
    c = Token("C", "C", OrderBook("C", asks=[OrderLevel(0.30, 50)]))
    m = Market("cid", "three-way", tokens=[a, b, c])
    opp = find_buy_arb(m, min_edge_per_set=0.005)
    assert opp is not None
    assert abs(opp.set_cost - 0.90) < 1e-9
    assert abs(opp.edge_per_set - 0.10) < 1e-9


def test_closed_or_bookless_markets_skipped():
    m = _market([(0.45, 100)], [(0.45, 100)])
    m.closed = True
    assert find_buy_arb(m, min_edge_per_set=0.005) is None

    m2 = _market([(0.45, 100)], [(0.45, 100)])
    m2.tokens[0].book = None
    assert find_buy_arb(m2, min_edge_per_set=0.005) is None


def test_scan_sorts_by_total_edge():
    small = _market([(0.45, 10)], [(0.45, 10)], cid="small")     # edge 0.10*10=1.0
    big = _market([(0.48, 100)], [(0.48, 100)], cid="big")        # edge 0.04*100=4.0
    opps = scan([small, big], min_edge_per_set=0.005)
    assert [o.market.condition_id for o in opps] == ["big", "small"]


def test_cost_to_buy_sets_none_on_thin_leg():
    m = _market([(0.45, 5)], [(0.45, 100)])
    assert cost_to_buy_sets(m, 10) is None  # YES can't fill 10
